from __future__ import annotations

import datetime as dt
from typing import Dict, List

import requests


def _market_prefix(code: str) -> str:
    code = code.strip()
    if code.startswith("6"):
        return f"sh{code}"
    return f"sz{code}"


def fetch_realtime_quote(code: str) -> Dict:
    """腾讯行情接口：返回单只股票最新报价。"""
    qcode = _market_prefix(code)
    url = f"https://qt.gtimg.cn/q={qcode}"
    resp = requests.get(url, timeout=10)
    resp.encoding = "gbk"
    text = resp.text

    if "=\"\"" in text:
        raise ValueError(f"quote empty for {code}")

    body = text.split('="', 1)[1].rsplit('"', 1)[0]
    arr = body.split("~")

    # 主要字段（A股）:
    # arr[1]=name arr[2]=code arr[3]=price arr[4]=prev_close arr[5]=open
    # arr[30]=time arr[31]=chg arr[32]=pct arr[33]=high arr[34]=low
    return {
        "name": arr[1],
        "code": arr[2],
        "price": float(arr[3]),
        "prev_close": float(arr[4]),
        "open": float(arr[5]),
        "time": arr[30],
        "chg": float(arr[31]) if arr[31] else 0.0,
        "pct": float(arr[32]) if arr[32] else 0.0,
        "high": float(arr[33]) if arr[33] else 0.0,
        "low": float(arr[34]) if arr[34] else 0.0,
    }


def fetch_history(code: str, bars: int = 180) -> List[Dict]:
    """腾讯复权日线，返回按日期升序的 OHLCV。"""
    qcode = _market_prefix(code)
    url = f"https://web.ifzq.gtimg.cn/appstock/app/fqkline/get?param={qcode},day,,,{bars},qfq"
    resp = requests.get(url, timeout=12)
    data = resp.json()

    section = data.get("data", {}).get(qcode, {})
    rows = section.get("qfqday") or section.get("day") or []

    out: List[Dict] = []
    for r in rows:
        # [date, open, close, high, low, volume]
        out.append(
            {
                "date": dt.datetime.strptime(r[0], "%Y-%m-%d").date(),
                "open": float(r[1]),
                "close": float(r[2]),
                "high": float(r[3]),
                "low": float(r[4]),
                "volume": float(r[5]),
            }
        )
    return out


def batch_quotes(codes: List[str]) -> Dict[str, Dict]:
    result: Dict[str, Dict] = {}
    for c in codes:
        try:
            result[c] = fetch_realtime_quote(c)
        except Exception:
            continue
    return result
