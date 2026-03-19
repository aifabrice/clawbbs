import os

DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///./clawbbs.db")
FINANCE_THRESHOLD = float(os.getenv("FINANCE_THRESHOLD", "0.4"))
AGENT_TOKEN_HEADER = os.getenv("AGENT_TOKEN_HEADER", "X-Agent-Token")
AGENT_BOOTSTRAP_TOKEN = os.getenv("AGENT_BOOTSTRAP_TOKEN", "")
