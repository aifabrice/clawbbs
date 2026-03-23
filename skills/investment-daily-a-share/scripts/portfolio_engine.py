from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime
from typing import Dict, List

from config import (
    CASH_BUFFER_RATE,
    COMMISSION_RATE,
    INITIAL_CASH,
    LOT_SIZE,
    MIN_TRADE_VALUE,
    REBALANCE_BAND,
    SLIPPAGE_RATE,
    STAMP_DUTY_SELL_RATE,
    STATE_FILE,
    TRANSFER_FEE_RATE,
    ensure_dirs,
)


@dataclass
class Trade:
    time: str
    action: str
    code: str
    price: float
    qty: int
    gross: float
    fee: float
    net_cash_change: float
    reason: str


def _buy_rate() -> float:
    return COMMISSION_RATE + TRANSFER_FEE_RATE + SLIPPAGE_RATE


def _sell_rate() -> float:
    return COMMISSION_RATE + TRANSFER_FEE_RATE + STAMP_DUTY_SELL_RATE + SLIPPAGE_RATE


def _now() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def load_state() -> Dict:
    ensure_dirs()
    if STATE_FILE.exists():
        return json.loads(STATE_FILE.read_text(encoding="utf-8"))
    return {
        "cash": INITIAL_CASH,
        "initial_cash": INITIAL_CASH,
        "holdings": {},
        "realized_pnl": 0.0,
        "last_nav": INITIAL_CASH,
        "last_update": None,
        "risk_mode": "normal",
        "trade_log": [],
    }


def save_state(state: Dict) -> None:
    ensure_dirs()
    STATE_FILE.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")


def portfolio_value(state: Dict, prices: Dict[str, float]) -> float:
    v = float(state.get("cash", 0.0))
    for c, h in state.get("holdings", {}).items():
        p = prices.get(c)
        if p is None:
            continue
        v += h.get("qty", 0) * p
    return v


def _append_trade(state: Dict, trade: Trade) -> None:
    state.setdefault("trade_log", []).append(trade.__dict__)
    if len(state["trade_log"]) > 500:
        state["trade_log"] = state["trade_log"][-500:]


def _buy(state: Dict, code: str, price: float, budget: float, reason: str) -> None:
    if budget < MIN_TRADE_VALUE:
        return
    qty = int(budget / (price * (1.0 + _buy_rate())))
    qty = (qty // LOT_SIZE) * LOT_SIZE
    if qty <= 0:
        return

    gross = qty * price
    fee = gross * _buy_rate()
    total = gross + fee
    if total > state["cash"]:
        return

    state["cash"] -= total
    h = state["holdings"].get(code, {"qty": 0, "avg_cost": 0.0})
    old_qty = h["qty"]
    old_cost = h["avg_cost"]
    new_qty = old_qty + qty
    new_cost = (old_qty * old_cost + total) / max(new_qty, 1)
    state["holdings"][code] = {"qty": new_qty, "avg_cost": new_cost}

    _append_trade(
        state,
        Trade(
            time=_now(),
            action="BUY",
            code=code,
            price=price,
            qty=qty,
            gross=gross,
            fee=fee,
            net_cash_change=-total,
            reason=reason,
        ),
    )


def _sell(state: Dict, code: str, price: float, qty: int, reason: str) -> None:
    if qty <= 0:
        return
    h = state["holdings"].get(code)
    if not h:
        return
    qty = min(qty, h["qty"])
    qty = (qty // LOT_SIZE) * LOT_SIZE
    if qty <= 0:
        return

    gross = qty * price
    fee = gross * _sell_rate()
    proceeds = gross - fee
    state["cash"] += proceeds

    avg_cost = h["avg_cost"]
    realized = proceeds - qty * avg_cost
    state["realized_pnl"] = state.get("realized_pnl", 0.0) + realized

    left = h["qty"] - qty
    if left <= 0:
        state["holdings"].pop(code, None)
    else:
        state["holdings"][code]["qty"] = left

    _append_trade(
        state,
        Trade(
            time=_now(),
            action="SELL",
            code=code,
            price=price,
            qty=qty,
            gross=gross,
            fee=fee,
            net_cash_change=proceeds,
            reason=reason,
        ),
    )


def rebalance_to_targets(
    state: Dict,
    targets: List[str],
    prices: Dict[str, float],
    cash_buffer_rate: float = CASH_BUFFER_RATE,
) -> Dict:
    before_nav = portfolio_value(state, prices)

    # 1) 先清掉不在目标里的持仓
    for code in list(state.get("holdings", {}).keys()):
        if code in targets:
            continue
        p = prices.get(code)
        if p is None:
            continue
        qty = state["holdings"][code]["qty"]
        _sell(state, code, p, qty, "not_in_targets")

    if not targets:
        after_nav = portfolio_value(state, prices)
        return {"before_nav": before_nav, "after_nav": after_nav, "target_each": 0.0}

    # 2) 计算目标仓位
    equity = portfolio_value(state, prices)
    target_each = equity * (1.0 - cash_buffer_rate) / len(targets)

    # 3) 先减仓（控制偏离）
    for code in targets:
        p = prices.get(code)
        if p is None or code not in state.get("holdings", {}):
            continue
        cur_val = state["holdings"][code]["qty"] * p
        diff = cur_val - target_each
        if diff <= 0:
            continue
        if diff / max(target_each, 1.0) < REBALANCE_BAND:
            continue
        sell_qty = int(diff / p)
        _sell(state, code, p, sell_qty, "rebalance_trim")

    # 4) 后加仓
    for code in targets:
        p = prices.get(code)
        if p is None:
            continue
        cur_qty = state.get("holdings", {}).get(code, {}).get("qty", 0)
        cur_val = cur_qty * p
        diff = target_each - cur_val
        if diff <= 0:
            continue
        if diff / max(target_each, 1.0) < REBALANCE_BAND:
            continue
        budget = min(diff, state["cash"])
        _buy(state, code, p, budget, "rebalance_add")

    after_nav = portfolio_value(state, prices)
    state["last_nav"] = after_nav
    state["last_update"] = _now()

    return {
        "before_nav": before_nav,
        "after_nav": after_nav,
        "target_each": target_each,
    }


def holding_rows(state: Dict, prices: Dict[str, float], names: Dict[str, str] | None = None) -> List[Dict]:
    rows = []
    names = names or {}
    for code, h in state.get("holdings", {}).items():
        p = prices.get(code)
        if p is None:
            continue
        qty = h["qty"]
        cost = h["avg_cost"]
        mv = qty * p
        pnl = mv - qty * cost
        pnl_pct = (p / cost - 1.0) if cost > 0 else 0.0
        rows.append(
            {
                "code": code,
                "name": names.get(code, ""),
                "qty": qty,
                "cost": cost,
                "price": p,
                "market_value": mv,
                "pnl": pnl,
                "pnl_pct": pnl_pct,
            }
        )
    rows.sort(key=lambda x: x["market_value"], reverse=True)
    return rows
