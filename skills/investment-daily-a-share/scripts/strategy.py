from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List, Tuple

from config import (
    BACKTEST_WARMUP_DAYS,
    BUY_TOP_RANK,
    CASH_BUFFER_RATE,
    COMMISSION_RATE,
    HOLD_BUFFER_RANK,
    REBALANCE_BAND,
    SLIPPAGE_RATE,
    STAMP_DUTY_SELL_RATE,
    TRANSFER_FEE_RATE,
)
from data_api import fetch_history


@dataclass
class Signal:
    code: str
    score: float
    close: float
    ma20: float
    ma60: float
    ret5: float
    ret20: float


def _ma(values: List[float], n: int) -> float:
    return sum(values[-n:]) / n


def _ret(values: List[float], n: int) -> float:
    return values[-1] / values[-1 - n] - 1.0


def score_series(closes: List[float], code: str) -> Signal | None:
    if len(closes) < 61:
        return None

    close = closes[-1]
    ma20 = _ma(closes, 20)
    ma60 = _ma(closes, 60)
    ret5 = _ret(closes, 5)
    ret20 = _ret(closes, 20)

    # 评分逻辑（越大越强）
    score = 0.0
    score += 35.0 if close > ma20 else -15.0
    score += 25.0 if ma20 > ma60 else -15.0
    score += max(min(ret20 * 220.0, 25.0), -25.0)
    score += max(min(ret5 * 140.0, 15.0), -15.0)

    return Signal(
        code=code,
        score=score,
        close=close,
        ma20=ma20,
        ma60=ma60,
        ret5=ret5,
        ret20=ret20,
    )


def select_targets(
    universe: List[str],
    max_positions: int,
    min_score: float,
    current_holdings: List[str] | None = None,
    buy_top_rank: int = BUY_TOP_RANK,
    hold_buffer_rank: int = HOLD_BUFFER_RANK,
) -> Tuple[List[Signal], Dict[str, List[dict]]]:
    """
    低换手目标选择：
    - 新进只能来自 TopN（buy_top_rank）且分数 >= min_score
    - 旧持仓只要仍在 TopM（hold_buffer_rank）内优先保留
    """
    histories: Dict[str, List[dict]] = {}
    ranked: List[Signal] = []

    for code in universe:
        try:
            hist = fetch_history(code, bars=220)
        except Exception:
            continue
        if len(hist) < 61:
            continue
        histories[code] = hist
        closes = [x["close"] for x in hist]
        sig = score_series(closes, code)
        if sig is None:
            continue
        ranked.append(sig)

    ranked.sort(key=lambda x: x.score, reverse=True)
    sig_by_code = {s.code: s for s in ranked}
    rank_map = {s.code: i + 1 for i, s in enumerate(ranked)}

    holdings = list(current_holdings or [])
    targets: List[str] = []

    # 1) 先保留仍在 TopM 的旧持仓（防抖）
    keep_codes = [c for c in holdings if rank_map.get(c, 10**9) <= hold_buffer_rank]
    keep_codes.sort(key=lambda c: rank_map.get(c, 10**9))
    for c in keep_codes:
        if c in sig_by_code and c not in targets:
            targets.append(c)
        if len(targets) >= max_positions:
            break

    # 2) 新进仅允许来自 TopN 且分数达标
    for s in ranked:
        if len(targets) >= max_positions:
            break
        if s.score < min_score:
            continue
        if rank_map[s.code] > buy_top_rank:
            continue
        if s.code in targets:
            continue
        targets.append(s.code)

    # 3) 若仓位还没补齐，再从达标股票里按分数补齐
    if len(targets) < max_positions:
        for s in ranked:
            if len(targets) >= max_positions:
                break
            if s.score < min_score:
                continue
            if s.code in targets:
                continue
            targets.append(s.code)

    return [sig_by_code[c] for c in targets if c in sig_by_code], histories


def _buy_cost_rate() -> float:
    return COMMISSION_RATE + TRANSFER_FEE_RATE + SLIPPAGE_RATE


def _sell_cost_rate() -> float:
    return COMMISSION_RATE + TRANSFER_FEE_RATE + STAMP_DUTY_SELL_RATE + SLIPPAGE_RATE


def rolling_backtest(
    universe: List[str],
    max_positions: int,
    min_score: float,
    lookback_days: int,
    initial_cash: float,
    buy_top_rank: int = BUY_TOP_RANK,
    hold_buffer_rank: int = HOLD_BUFFER_RANK,
) -> Dict:
    """简单日频回测：按收盘价再平衡，计入交易成本。"""
    # 1) 拉历史
    hists: Dict[str, List[dict]] = {}
    for c in universe:
        try:
            h = fetch_history(c, bars=max(lookback_days + 90, 180))
        except Exception:
            continue
        if len(h) >= BACKTEST_WARMUP_DAYS + 5:
            hists[c] = h

    if len(hists) < max(3, min(max_positions, 3)):
        return {
            "ok": False,
            "reason": "可用历史数据不足",
        }

    # 2) 构造回测日期（取所有股票的日期并集）
    all_dates = sorted({row["date"] for h in hists.values() for row in h})
    if len(all_dates) < BACKTEST_WARMUP_DAYS + 20:
        return {"ok": False, "reason": "日期样本不足"}

    dates = all_dates[-lookback_days:]

    # price lookup
    close_map: Dict[str, Dict] = {}
    for c, h in hists.items():
        close_map[c] = {r["date"]: r["close"] for r in h}

    cash = initial_cash
    shares: Dict[str, float] = {}
    nav_curve: List[Tuple[str, float]] = []

    for i, day in enumerate(dates):
        # 3) 用截至当日的历史打分
        ranked_today: List[Signal] = []
        prices_today: Dict[str, float] = {}

        for c, h in hists.items():
            # 截至当日的序列
            seq = [r for r in h if r["date"] <= day]
            if len(seq) < BACKTEST_WARMUP_DAYS:
                continue
            if day not in close_map[c]:
                continue
            closes = [x["close"] for x in seq]
            sig = score_series(closes, c)
            if sig is None:
                continue
            ranked_today.append(sig)
            prices_today[c] = close_map[c][day]

        ranked_today.sort(key=lambda x: x.score, reverse=True)
        rank_map = {s.code: idx + 1 for idx, s in enumerate(ranked_today)}

        # 低换手选目标：先保留旧仓（TopM），再允许新进（TopN）
        targets: List[str] = []
        keep_codes = [c for c in shares.keys() if rank_map.get(c, 10**9) <= hold_buffer_rank]
        keep_codes.sort(key=lambda c: rank_map.get(c, 10**9))
        for c in keep_codes:
            if c in prices_today and c not in targets:
                targets.append(c)
            if len(targets) >= max_positions:
                break

        for s in ranked_today:
            if len(targets) >= max_positions:
                break
            if s.score < min_score:
                continue
            if rank_map[s.code] > buy_top_rank:
                continue
            if s.code in targets:
                continue
            targets.append(s.code)

        if len(targets) < max_positions:
            for s in ranked_today:
                if len(targets) >= max_positions:
                    break
                if s.score < min_score:
                    continue
                if s.code in targets:
                    continue
                targets.append(s.code)

        # 4) 先计算当前净值
        equity = cash
        for c, q in shares.items():
            p = prices_today.get(c)
            if p is not None:
                equity += q * p

        if not targets:
            nav_curve.append((str(day), equity))
            continue

        target_each = equity * (1.0 - CASH_BUFFER_RATE) / len(targets)

        # 5) 先卖出非目标
        for c in list(shares.keys()):
            if c in targets:
                continue
            p = prices_today.get(c)
            if p is None:
                continue
            gross = shares[c] * p
            cash += gross * (1.0 - _sell_cost_rate())
            shares.pop(c, None)

        # 6) 调整目标仓位（带宽控制）
        # 先减仓
        for c in targets:
            p = prices_today.get(c)
            if p is None:
                continue
            cur = shares.get(c, 0.0) * p
            diff = cur - target_each
            if diff <= 0:
                continue
            if diff / max(target_each, 1.0) < REBALANCE_BAND:
                continue
            sell_amt = diff
            sell_qty = min(shares.get(c, 0.0), sell_amt / p)
            if sell_qty <= 0:
                continue
            cash += sell_qty * p * (1.0 - _sell_cost_rate())
            shares[c] = shares.get(c, 0.0) - sell_qty
            if shares[c] <= 1e-9:
                shares.pop(c, None)

        # 后加仓
        for c in targets:
            p = prices_today.get(c)
            if p is None:
                continue
            cur = shares.get(c, 0.0) * p
            diff = target_each - cur
            if diff <= 0:
                continue
            if diff / max(target_each, 1.0) < REBALANCE_BAND:
                continue
            buy_amt = min(diff, cash)
            if buy_amt <= 0:
                continue
            buy_qty = buy_amt / (p * (1.0 + _buy_cost_rate()))
            cost = buy_qty * p * (1.0 + _buy_cost_rate())
            if buy_qty <= 0:
                continue
            cash -= cost
            shares[c] = shares.get(c, 0.0) + buy_qty

        # 7) 记录净值
        nav = cash
        for c, q in shares.items():
            p = prices_today.get(c)
            if p is not None:
                nav += q * p
        nav_curve.append((str(day), nav))

    if len(nav_curve) < 2:
        return {"ok": False, "reason": "净值曲线不足"}

    start_nav = nav_curve[0][1]
    end_nav = nav_curve[-1][1]
    total_ret = end_nav / start_nav - 1.0

    peak = nav_curve[0][1]
    mdd = 0.0
    wins = 0
    rets = []
    for i in range(1, len(nav_curve)):
        prev = nav_curve[i - 1][1]
        cur = nav_curve[i][1]
        r = cur / prev - 1.0
        rets.append(r)
        if r > 0:
            wins += 1
        peak = max(peak, cur)
        dd = cur / peak - 1.0
        mdd = min(mdd, dd)

    win_rate = wins / max(len(rets), 1)

    return {
        "ok": True,
        "start": nav_curve[0][0],
        "end": nav_curve[-1][0],
        "days": len(nav_curve),
        "total_return": total_ret,
        "max_drawdown": mdd,
        "win_rate": win_rate,
        "end_nav": end_nav,
    }
