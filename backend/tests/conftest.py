"""Pytest 全局夹具：为测试套件固定独立可写数据库。

必须在任何 app 模块导入之前设置 DATABASE_URL（app.persistence.database
在导入时即创建引擎）；.env 通过 load_dotenv(override=False) 加载，
不会覆盖此处预设的环境变量。

测试库使用临时目录下的独立 SQLite 文件，schema 直接由当前
Base.metadata 创建（与 Alembic 迁移同源），测试结束后删除，
运行中的业务数据库（backend/data/demo.db）不被触碰。
"""
from __future__ import annotations

import os
import tempfile
import uuid
from pathlib import Path

_TEST_DB_PATH = (
    Path(tempfile.gettempdir())
    / f"auto_parts_backend_test_{os.getpid()}_{uuid.uuid4().hex[:8]}.db"
)
os.environ["DATABASE_URL"] = f"sqlite:///{_TEST_DB_PATH.as_posix()}"

import pytest  # noqa: E402
from app.persistence.database import engine  # noqa: E402
from app.persistence.models import Base  # noqa: E402


def pytest_configure(config) -> None:
    Base.metadata.create_all(bind=engine)


def pytest_unconfigure(config) -> None:
    engine.dispose()
    try:
        _TEST_DB_PATH.unlink()
    except OSError:
        pass


@pytest.fixture
def test_db_path() -> Path:
    return _TEST_DB_PATH
