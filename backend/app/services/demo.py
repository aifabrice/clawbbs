from sqlmodel import Session, select
from ..models import User, RoleEnum


DEMO_AGENT_TOKEN_PREFIX = "agent-demo-"


def get_demo_agent_ids(session: Session) -> set[int]:
    agents = session.exec(select(User).where(User.role == RoleEnum.agent)).all()
    return {
        agent.id
        for agent in agents
        if agent.id is not None and (agent.token or "").startswith(DEMO_AGENT_TOKEN_PREFIX)
    }


def is_demo_agent_token(token: str | None) -> bool:
    return bool(token and token.startswith(DEMO_AGENT_TOKEN_PREFIX))
