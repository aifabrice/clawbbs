from __future__ import annotations

from datetime import datetime
from typing import Dict, List


def pct(v: float) -> str:
    return f"{v * 100:.2f}%"


def money(v: float) -> str:
    return f"{v:,.2f}"


def build_holdings_markdown(rows: List[Dict]) -> str:
    if not rows:
        return "- 当前空仓\n"
    lines = []
    for r in rows:
        name = f"{r['name']}" if r.get("name") else ""
        lines.append(
            f"- {r['code']} {name} | 持仓 {r['qty']} 股 | 成本 {r['cost']:.2f} | 现价 {r['price']:.2f} | 浮盈 {money(r['pnl'])} ({pct(r['pnl_pct'])})"
        )
    return "\n".join(lines) + "\n"


def build_signals_markdown(signals: List) -> str:
    if not signals:
        return "- 今日无满足阈值的候选\n"
    lines = []
    for s in signals:
        lines.append(
            f"- {s.code} | score={s.score:.1f} | close={s.close:.2f} | MA20={s.ma20:.2f} | MA60={s.ma60:.2f} | 5D={pct(s.ret5)} | 20D={pct(s.ret20)}"
        )
    return "\n".join(lines) + "\n"


def build_trade_markdown(trades: List[Dict]) -> str:
    if not trades:
        return "- 今日无交易\n"
    lines = []
    for t in trades:
        lines.append(
            f"- {t['time']} {t['action']} {t['code']} {t['qty']}股 @ {t['price']:.2f} | 费用 {money(t['fee'])} | 现金变化 {money(t['net_cash_change'])} | {t['reason']}"
        )
    return "\n".join(lines) + "\n"


def now_text() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")
