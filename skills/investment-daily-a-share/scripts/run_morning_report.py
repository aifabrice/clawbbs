from __future__ import annotations

from datetime import date

from config import (
    BUY_TOP_RANK,
    DEFAULT_UNIVERSE,
    HOLD_BUFFER_RANK,
    MAX_POSITIONS,
    MIN_SCORE_TO_BUY,
    REPORT_DIR,
    ensure_dirs,
)
from data_api import batch_quotes
from portfolio_engine import holding_rows, load_state
from report_utils import build_holdings_markdown, build_signals_markdown, money, now_text, pct
from strategy import select_targets


def main() -> None:
    ensure_dirs()
    state = load_state()

    signals, _ = select_targets(
        universe=DEFAULT_UNIVERSE,
        max_positions=MAX_POSITIONS,
        min_score=MIN_SCORE_TO_BUY,
        current_holdings=list(state.get("holdings", {}).keys()),
        buy_top_rank=BUY_TOP_RANK,
        hold_buffer_rank=HOLD_BUFFER_RANK,
    )

    quote_codes = sorted(set(DEFAULT_UNIVERSE + list(state.get("holdings", {}).keys())))
    qmap = batch_quotes(quote_codes)
    prices = {c: v["price"] for c, v in qmap.items()}
    names = {c: v["name"] for c, v in qmap.items()}

    rows = holding_rows(state, prices, names=names)
    nav = state.get("cash", 0.0) + sum(r["market_value"] for r in rows)
    total_ret = nav / state.get("initial_cash", nav) - 1.0 if state.get("initial_cash", 0) else 0.0

    report = []
    today = date.today().isoformat()
    report.append(f"# A股投资今日持仓 - 早晨报告 ({today})")
    report.append(f"生成时间: {now_text()}")
    report.append("")

    report.append("## 当前组合")
    report.append(f"- 当前估算净值: {money(nav)}")
    report.append(f"- 现金: {money(state.get('cash', 0.0))}")
    report.append(f"- 累计收益: {pct(total_ret)}")
    report.append(f"- 已实现盈亏: {money(state.get('realized_pnl', 0.0))}")
    report.append(f"- 风险模式: {state.get('risk_mode', 'normal')}")
    report.append("")

    report.append("## 当前持仓明细")
    report.append(build_holdings_markdown(rows))

    report.append("## 今日关注清单（建议优先）")
    report.append(build_signals_markdown(signals))

    if signals:
        top_codes = ", ".join([s.code for s in signals[: min(3, len(signals))]])
        report.append("## 今日操作建议（规则化）")
        report.append(f"- 优先关注: {top_codes}")
        report.append("- 若你当前持仓与目标差异较大，收盘任务会自动给出调仓动作与成本。")
    else:
        report.append("## 今日操作建议（规则化）")
        report.append("- 今日无明显强信号，建议降低交易频率，优先控回撤。")

    text = "\n".join(report).strip() + "\n"
    path = REPORT_DIR / f"morning-{today}.md"
    path.write_text(text, encoding="utf-8")

    print(text)
    print(f"REPORT_PATH={path}")


if __name__ == "__main__":
    main()
