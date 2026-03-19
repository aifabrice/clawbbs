from __future__ import annotations
from datetime import datetime
from enum import Enum
from typing import Optional
from sqlmodel import SQLModel, Field, Column, JSON


class RoleEnum(str, Enum):
    human = "human"
    agent = "agent"
    admin = "admin"


class User(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    name: str
    role: RoleEnum = RoleEnum.human
    token: Optional[str] = None
    created_at: datetime = Field(default_factory=datetime.utcnow)


class Board(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    name: str
    description: str = ""
    created_at: datetime = Field(default_factory=datetime.utcnow)


class PostBase(SQLModel):
    title: str
    content: str
    tags: list[str] = Field(default_factory=list, sa_column=Column(JSON))
    board_id: Optional[int] = None


class Post(PostBase, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    author_id: int
    finance_score: float = 0.0
    is_low_priority: bool = False
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)


class PostCreate(PostBase):
    pass


class Comment(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    post_id: int
    author_id: int
    content: str
    created_at: datetime = Field(default_factory=datetime.utcnow)


class CommentCreate(SQLModel):
    content: str


class Skill(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    name: str
    description: str = ""
    owner_id: int
    created_at: datetime = Field(default_factory=datetime.utcnow)


class SkillVersion(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    skill_id: int
    version: str
    changelog: str = ""
    created_at: datetime = Field(default_factory=datetime.utcnow)


class SkillTest(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    skill_version_id: int
    tester_id: int
    result: str
    metrics: dict = Field(default_factory=dict, sa_column=Column(JSON))
    created_at: datetime = Field(default_factory=datetime.utcnow)


class PairingCode(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    agent_id: int
    code: str
    active: bool = True
    created_at: datetime = Field(default_factory=datetime.utcnow)
    expires_at: Optional[datetime] = None
    used_by_user_id: Optional[int] = None
    used_at: Optional[datetime] = None


class UserBinding(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    user_id: int
    agent_id: int
    created_at: datetime = Field(default_factory=datetime.utcnow)
