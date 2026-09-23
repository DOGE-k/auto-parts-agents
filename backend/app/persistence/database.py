from __future__ import annotations

import os
from pathlib import Path

from sqlalchemy import create_engine, event, inspect
from sqlalchemy.orm import Session, sessionmaker

from app.persistence.models import Base

BACKEND_DIR = Path(__file__).resolve().parents[2]
DEFAULT_DB_FILE = BACKEND_DIR / "data" / "demo.db"
DATABASE_URL = os.getenv("DATABASE_URL", f"sqlite:///{DEFAULT_DB_FILE.as_posix()}")

if DATABASE_URL.startswith("sqlite:///"):
    db_path = DATABASE_URL.removeprefix("sqlite:///")
    if db_path and db_path != ":memory:":
        Path(db_path).parent.mkdir(parents=True, exist_ok=True)

engine = create_engine(
    DATABASE_URL,
    connect_args={"check_same_thread": False} if DATABASE_URL.startswith("sqlite") else {},
    pool_pre_ping=True,
)


if DATABASE_URL.startswith("sqlite"):
    @event.listens_for(engine, "connect")
    def _enable_sqlite_foreign_keys(dbapi_connection, _connection_record) -> None:
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()


SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False, class_=Session)


def init_database() -> None:
    inspector = inspect(engine)
    table_names = set(inspector.get_table_names())
    required_tables = set(Base.metadata.tables)
    missing = sorted(required_tables - table_names)
    if "alembic_version" not in table_names or missing:
        details = f"缺少表：{', '.join(missing)}" if missing else "缺少 alembic_version"
        raise RuntimeError(f"数据库迁移未完成（{details}）；请在 backend 目录运行 `alembic upgrade head`。")


def get_session():
    with SessionLocal() as session:
        yield session
