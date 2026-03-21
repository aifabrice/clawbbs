import os
from sqlmodel import SQLModel, Session, create_engine, select
from app import models

SQLITE_PATH = os.path.join(os.path.dirname(__file__), "..", "clawbbs.db")
SQLITE_URL = f"sqlite:///{os.path.abspath(SQLITE_PATH)}"

cred_path = os.path.join(os.path.dirname(__file__), "..", "..", "data", "mysql_credentials.txt")
cred_user = "clawbbs"
cred_pass = ""
if os.path.exists(cred_path):
    try:
        raw = open(cred_path, "r", encoding="utf-8").read().strip()
        if ":" in raw:
            cred_user, cred_pass = raw.split(":", 1)
    except Exception:
        pass

MYSQL_URL = os.environ.get(
    "MYSQL_URL",
    f"mysql+pymysql://{cred_user}:{cred_pass}@127.0.0.1:3306/clawbbs?charset=utf8mb4",
)

sqlite_engine = create_engine(SQLITE_URL)
mysql_engine = create_engine(MYSQL_URL)

# create tables in MySQL
SQLModel.metadata.create_all(mysql_engine)

TABLES = [
    models.User,
    models.UserCredential,
    models.Board,
    models.Post,
    models.Comment,
    models.Skill,
    models.SkillTest,
    models.PairingCode,
    models.UserBinding,
    models.SkillInstallTask,
    models.PostVote,
    models.CommentLike,
]


def table_count(session, model):
    return session.exec(select(model)).first() is not None


with Session(sqlite_engine) as s_src, Session(mysql_engine) as s_dst:
    for model in TABLES:
        # skip if dest already has rows
        if table_count(s_dst, model):
            print(f"skip {model.__name__} (dest not empty)")
            continue
        rows = s_src.exec(select(model)).all()
        if not rows:
            print(f"empty {model.__name__}")
            continue
        for row in rows:
            data = row.model_dump()
            s_dst.add(model(**data))
        s_dst.commit()
        print(f"copied {model.__name__}: {len(rows)}")

print("done")
