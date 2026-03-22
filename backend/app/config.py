import os


def _env_bool(name: str, default: bool = False) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///./clawbbs.db")
FINANCE_THRESHOLD = float(os.getenv("FINANCE_THRESHOLD", "0.4"))
POST_CONTENT_MAX_CHARS = int(os.getenv("POST_CONTENT_MAX_CHARS", "5000"))
AGENT_TOKEN_HEADER = os.getenv("AGENT_TOKEN_HEADER", "X-Agent-Token")
USER_TOKEN_HEADER = os.getenv("USER_TOKEN_HEADER", "X-User-Token")
USER_SESSION_COOKIE_NAME = os.getenv("USER_SESSION_COOKIE_NAME", "clawbbs_session")
USER_SESSION_COOKIE_SECURE = _env_bool("USER_SESSION_COOKIE_SECURE", False)
USER_SESSION_COOKIE_SAMESITE = os.getenv("USER_SESSION_COOKIE_SAMESITE", "lax")
AGENT_BOOTSTRAP_TOKEN = os.getenv("AGENT_BOOTSTRAP_TOKEN", "")
PAIRING_CODE_TTL_MINUTES = int(os.getenv("PAIRING_CODE_TTL_MINUTES", "120"))
CONNECT_CODE_TTL_MINUTES = int(os.getenv("CONNECT_CODE_TTL_MINUTES", "10"))
USER_SESSION_TTL_HOURS = int(os.getenv("USER_SESSION_TTL_HOURS", "72"))
AGENT_TOKEN_TTL_DAYS = int(os.getenv("AGENT_TOKEN_TTL_DAYS", "365"))
AGENT_TASK_LEASE_SECONDS = int(os.getenv("AGENT_TASK_LEASE_SECONDS", "300"))
PGC_ENABLED = _env_bool("PGC_ENABLED", False)
PGC_POOL_SIZE = int(os.getenv("PGC_POOL_SIZE", "100"))
PGC_POSTS_PER_TICK = int(os.getenv("PGC_POSTS_PER_TICK", "1"))
PGC_MAX_NEWS_AGE_MINUTES = int(os.getenv("PGC_MAX_NEWS_AGE_MINUTES", "180"))
PGC_NEWS_FETCH_TIMEOUT_SECONDS = int(os.getenv("PGC_NEWS_FETCH_TIMEOUT_SECONDS", "12"))
PGC_HEADLINE_MIN_SCORE = int(os.getenv("PGC_HEADLINE_MIN_SCORE", "2"))
PGC_AGENT_COOLDOWN_MINUTES = int(os.getenv("PGC_AGENT_COOLDOWN_MINUTES", "180"))
PGC_TOPIC_COOLDOWN_MINUTES = int(os.getenv("PGC_TOPIC_COOLDOWN_MINUTES", "120"))
PGC_MIN_POST_INTERVAL_MINUTES = int(os.getenv("PGC_MIN_POST_INTERVAL_MINUTES", "15"))
PGC_QUIET_HOURS_START = int(os.getenv("PGC_QUIET_HOURS_START", "0"))
PGC_QUIET_HOURS_END = int(os.getenv("PGC_QUIET_HOURS_END", "8"))
PGC_QUIET_MIN_POST_INTERVAL_MINUTES = int(os.getenv("PGC_QUIET_MIN_POST_INTERVAL_MINUTES", "60"))
PGC_NEWS_RSS_URLS = [
    value.strip()
    for value in os.getenv(
        "PGC_NEWS_RSS_URLS",
        "https://news.google.com/rss/search?q=%E8%B4%A2%E7%BB%8F&hl=zh-CN&gl=CN&ceid=CN:zh-Hans,https://news.google.com/rss/search?q=A%E8%82%A1+OR+%E7%BE%8E%E8%82%A1+OR+%E5%AE%8F%E8%A7%82&hl=zh-CN&gl=CN&ceid=CN:zh-Hans,https://feeds.content.dowjones.io/public/rss/mw_realtimeheadlines",
    ).split(",")
    if value.strip()
]
PUBLIC_BASE_URL = os.getenv("PUBLIC_BASE_URL", "")
