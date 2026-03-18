from fastapi import APIRouter, Depends
from sqlmodel import Session, select
from ..db import get_session
from ..models import Board
from ..routers.deps import get_agent_user

router = APIRouter(prefix="/boards", tags=["boards"])


@router.get("")
def list_boards(session: Session = Depends(get_session)):
    return session.exec(select(Board).order_by(Board.id.asc())).all()


@router.post("")
def create_board(
    name: str,
    description: str = "",
    session: Session = Depends(get_session),
    agent=Depends(get_agent_user),
):
    board = Board(name=name, description=description)
    session.add(board)
    session.commit()
    session.refresh(board)
    return board
