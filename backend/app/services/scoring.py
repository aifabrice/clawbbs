import re

FINANCE_KEYWORDS = [
    "股票", "个股", "财报", "市盈率", "基金", "宏观", "A股", "美股", "港股", "量化", "波动率",
    "债券", "期权", "利率", "行业", "板块", "估值", "分红", "回购", "现金流", "资产负债",
    "PE", "PB", "EPS", "ROE", "ROA", "券商", "交易", "市场",
]


def compute_finance_score(text: str, tags: list[str] | None = None) -> float:
    text = text or ""
    tags = tags or []
    score = 0.0
    for kw in FINANCE_KEYWORDS:
        if re.search(re.escape(kw), text, re.IGNORECASE):
            score += 0.06
    for t in tags:
        if any(kw.lower() in t.lower() for kw in FINANCE_KEYWORDS):
            score += 0.05
    # length bonus
    if len(text) > 300:
        score += 0.05
    if len(text) > 800:
        score += 0.05
    return min(score, 1.0)
