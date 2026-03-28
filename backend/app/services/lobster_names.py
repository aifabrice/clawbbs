from __future__ import annotations

import random
from sqlmodel import Session, select

from ..models import RoleEnum, User

ADJECTIVES = [
    "Amber",
    "Nova",
    "Onyx",
    "Silver",
    "Echo",
    "Cedar",
    "Misty",
    "Velvet",
    "Solar",
    "Luna",
    "Coral",
    "Astra",
    "Maple",
    "Ivory",
    "Nimbus",
    "Sable",
    "Pixel",
    "Harbor",
    "River",
    "Comet",
]

NOUNS = [
    "Harbor",
    "Drift",
    "Bay",
    "Wave",
    "Shell",
    "Anchor",
    "Cove",
    "Bloom",
    "Trail",
    "Orbit",
    "Reef",
    "Spark",
    "Grove",
    "Tide",
    "Pine",
    "Quill",
    "Stone",
    "Dawn",
    "Vale",
    "Brook",
]


def _exists(session: Session, name: str) -> bool:
    return session.exec(select(User).where((User.name == name) & (User.role == RoleEnum.agent))).first() is not None


def looks_like_legacy_lobster_name(name: str | None) -> bool:
    value = (name or "").strip().lower()
    if not value:
        return True
    return value == "user-owned-lobster" or value.startswith("lobster-") or value.startswith("agent-")


def generate_random_lobster_name(session: Session) -> str:
    for _ in range(48):
        name = f"{random.choice(ADJECTIVES)} {random.choice(NOUNS)}".strip()
        if not _exists(session, name):
            return name
    return f"Lobster {random.randint(1000, 9999)}"
