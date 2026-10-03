"""一次性数据迁移：SQLite 业务库 → PostgreSQL（阶段十 10.7）。

用法：先在 .env 配置 DATABASE_URL=postgresql+psycopg2://...，再执行：
    python migrate_sqlite_to_postgres.py [--drop]

行为：
1. 按 SQLAlchemy Base.metadata 在目标库建表（与 Alembic 同源）；
2. 从 backend/data/demo.db 逐表搬运全部行（列名对齐，psycopg2 赋值
   语义允许 int→boolean、ISO 文本→timestamp 的隐式转换）；
3. 幂等：目标表非空时默认跳过该表；--drop 先清空再搬。

凭据从环境变量读取，不打印。真实业务表以 real_* 为主，其余为
Mock/历史表面数据，一并迁移保持行为一致。
"""
from __future__ import annotations

import argparse
import sqlite3
import sys
from pathlib import Path

import psycopg2
from sqlalchemy import create_engine, text

import os
sys.path.insert(0, str(Path(__file__).parent))
from app.persistence.database import DATABASE_URL  # noqa: E402
from app.persistence.models import Base  # noqa: E402

SQLITE_PATH = Path(__file__).parent / "data" / "demo.db"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--drop", action="store_true", help="迁移前清空目标表")
    args = parser.parse_args()

    if not DATABASE_URL.startswith("postgresql"):
        print("DATABASE_URL 未指向 PostgreSQL，拒绝执行（防止误写 SQLite）")
        return 1
    if not SQLITE_PATH.exists():
        print(f"SQLite 业务库不存在：{SQLITE_PATH}")
        return 1

    engine = create_engine(DATABASE_URL)
    Base.metadata.create_all(bind=engine)
    # 按外键依赖拓扑排序，保证被引用表先插入
    tables = [t.name for t in Base.metadata.sorted_tables]

    sqlite_conn = sqlite3.connect(SQLITE_PATH)
    sqlite_conn.row_factory = sqlite3.Row
    pg = psycopg2.connect(DATABASE_URL.replace("postgresql+psycopg2://", "postgresql://"))
    pg.autocommit = True

    total_migrated = 0
    skipped = []

    def _column_types(cur, table: str) -> dict[str, str]:
        cur.execute(
            "SELECT column_name, data_type FROM information_schema.columns "
            "WHERE table_name = %s", (table,)
        )
        return {r[0]: r[1] for r in cur.fetchall()}

    def _coerce(values: list, columns: list, types: dict[str, str]) -> list:
        out = []
        for col, value in zip(columns, values):
            if types.get(col) == "boolean" and isinstance(value, int):
                out.append(bool(value))
            else:
                out.append(value)
        return out
    with engine.begin() as _:
        pass  # 确保 engine 初始化无异常

    for table in tables:
        try:
            rows = sqlite_conn.execute(f"SELECT * FROM {table}").fetchall()
        except sqlite3.OperationalError:
            skipped.append(f"{table}(sqlite 无此表)")
            continue
        if not rows:
            continue
        columns = rows[0].keys()
        col_list = ", ".join(f'"{c}"' for c in columns)
        placeholders = ", ".join(["%s"] * len(columns))
        with pg.cursor() as cur:
            if args.drop:
                cur.execute(f'DELETE FROM "{table}"')
            cur.execute(f'SELECT count(*) FROM "{table}"')
            existing = cur.fetchone()[0]
            if existing:
                skipped.append(f"{table}(已有 {existing} 行)")
                continue
            types = _column_types(cur, table)
            inserted = 0
            for row in rows:
                values = _coerce([row[c] for c in columns], list(columns), types)
                cur.execute(f'INSERT INTO "{table}" ({col_list}) VALUES ({placeholders})', values)
                inserted += 1
        total_migrated += inserted
        print(f"{table}: {inserted} 行")

    pg.close()
    print(f"\n迁移完成：{total_migrated} 行" + (f"；跳过：{', '.join(skipped)}" if skipped else ""))
    return 0


if __name__ == "__main__":
    sys.exit(main())
