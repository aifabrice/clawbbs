from __future__ import annotations

import json
from datetime import date, datetime

from config import (
    BACKTEST_FILE,
    BACKTEST_LOOKBACK_DAYS,
    BUY_TOP_RANK,
    CASH_BUFFER_RATE,
    DEFAULT_UNIVERSE,
    HOLD_BUFFER_RANK,
    MAIN_REBALANCE_WEEKDAYS,
    MAX_POSITIONS,
    MIN_SCORE_TO_BUY,
    REPORT_DIR,
    ensure_dirs,
)
from data_api import batch_quotes
from portfolio_engine import holding_rows, load_state, rebalance_to_targets, save_state
from report_utils import build_holdings_markdown, build_signals_markdown, build_trade_markdown, money, now_text, pct
from strategy import rolling_backtest, select_targets


def main() -> None:
    ensure_dirs()
    state = load_state()

    # 1) 选股信号
    signals, _ = select_targets(
        universe=DEFAULT_UNIVERSE,
        max_positions=MAX_POSITIONS,
        min_score=MIN_SCORE_TO_BUY,
        current_holdings=list(state.get("holdings", {}).keys()),
        buy_top_rank=BUY_TOP_RANK,
        hold_buffer_rank=HOLD_BUFFER_RANK,
    )
    targets = [s.code for s in signals]

    # 2) 获取报价（候选池 + 当前持仓）
    quote_codes = sorted(set(DEFAULT_UNIVERSE + list(state.get("holdings", {}).keys())))
    qmap = batch_quotes(quote_codes)
    prices = {c: v["price"] for c, v in qmap.items()}
    names = {c: v["name"] for c, v in qmap.items()}

    # 风险模式：回测差时提高现金缓冲
    risk_mode = state.get("risk_mode", "normal")
    cash_buffer = 0.18 if risk_mode == "defensive" else CASH_BUFFER_RATE

    # 3) 执行调仓（主调仓日：周一/周三/周五）
    weekday = datetime.now().weekday()  # 0=Mon
    is_main_rebalance_day = weekday in MAIN_REBALANCE_WEEKDAYS

    before_trade_count = len(state.get("trade_log", []))
    if is_main_rebalance_day:
        reb = rebalance_to_targets(state, targets, prices, cash_buffer_rate=cash_buffer)
    else:
        nav_no_reb = state.get("cash", 0.0)
        for c, h in state.get("holdings", {}).items():
            p = prices.get(c)
            if p is None:
                continue
            nav_no_reb += h.get("qty", 0) * p
        state["last_nav"] = nav_no_reb
        state["last_update"] = now_text()
        reb = {"before_nav": nav_no_reb, "after_nav": nav_no_reb, "target_each": 0.0}

    after_trade_count = len(state.get("trade_log", []))
    new_trades = state.get("trade_log", [])[before_trade_count:after_trade_count]

    # 4) 回测（近120日）
    bt = rolling_backtest(
        universe=DEFAULT_UNIVERSE,
        max_positions=MAX_POSITIONS,
        min_score=MIN_SCORE_TO_BUY,
        lookback_days=BACKTEST_LOOKBACK_DAYS,
        initial_cash=state.get("initial_cash", 200000.0),
        buy_top_rank=BUY_TOP_RANK,
        hold_buffer_rank=HOLD_BUFFER_RANK,
    )

    # 根据回测动态调整风险模式（简单版自动调参）
    if bt.get("ok"):
        if bt["total_return"] < 0 and bt["max_drawdown"] < -0.10:
            state["risk_mode"] = "defensive"
        elif bt["total_return"] > 0.03 and bt["max_drawdown"] > -0.08:
            state["risk_mode"] = "normal"

    save_state(state)

    # 5) 报告
    rows = holding_rows(state, prices, names=names)
    nav = reb["after_nav"]
    total_ret = nav / state.get("initial_cash", nav) - 1.0
    today = date.today().isoformat()

    report = []
    report.append(f"# A股投资今日持仓 - 收盘回测日报 ({today})")
    report.append(f"生成时间: {now_text()}")
    report.append("")
    report.append("## 组合摘要")
    report.append(f"- 当前净值: {money(nav)}")
    report.append(f"- 初始资金: {money(state.get('initial_cash', 0.0))}")
    report.append(f"- 累计收益: {pct(total_ret)}")
    report.append(f"- 已实现盈亏: {money(state.get('realized_pnl', 0.0))}")
    report.append(f"- 风险模式: {state.get('risk_mode', 'normal')}")
    report.append(f"- 主调仓日: {'是' if is_main_rebalance_day else '否（仅更新估值/回测）'}")
    report.append("")

    report.append("## 今日目标持仓（规则信号）")
    report.append(build_signals_markdown(signals))

    report.append("## 当前持仓")
    report.append(build_holdings_markdown(rows))

    report.append("## 今日交易（含成本）")
    report.append(build_trade_markdown(new_trades))

    report.append("## 收盘后回测摘要")
    if bt.get("ok"):
        report.append(f"- 区间: {bt['start']} ~ {bt['end']} ({bt['days']} 个交易日)")
        report.append(f"- 区间收益: {pct(bt['total_return'])}")
        report.append(f"- 最大回撤: {pct(bt['max_drawdown'])}")
        report.append(f"- 胜率(按日): {pct(bt['win_rate'])}")
        report.append(f"- 结束净值(回测): {money(bt['end_nav'])}")
    else:
        report.append(f"- 回测不可用: {bt.get('reason', 'unknown')}")

    text = "\n".join(report).strip() + "\n"

    report_path = REPORT_DIR / f"close-{today}.md"
    report_path.write_text(text, encoding="utf-8")

    # 记录回测历史
    hist = []
    if BACKTEST_FILE.exists():
        try:
            hist = json.loads(BACKTEST_FILE.read_text(encoding="utf-8"))
        except Exception:
            hist = []
    hist.append(
        {
            "date": today,
            "nav": nav,
            "total_return": total_ret,
            "risk_mode": state.get("risk_mode", "normal"),
            "backtest": bt,
        }
    )
    hist = hist[-180:]
    BACKTEST_FILE.write_text(json.dumps(hist, ensure_ascii=False, indent=2), encoding="utf-8")

    print(text)
    print(f"REPORT_PATH={report_path}")


if __name__ == "__main__":
    main()
