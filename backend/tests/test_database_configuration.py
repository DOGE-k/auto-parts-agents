"""数据库入口配置顺序回归；子进程仅接触临时项目配置。"""
from __future__ import annotations

import os
from pathlib import Path
import shutil
import subprocess
import sys

import pytest


_PROBE = r"""
import os
from pathlib import Path
import dotenv

backend = Path.cwd().resolve()
allowed = {backend.parent / '.env', backend / '.env'}
original_load = dotenv.load_dotenv
loaded = []

def isolated_load(path, *args, **kwargs):
    candidate = Path(path).resolve()
    assert candidate in allowed, 'Configuration read escaped the temporary project'
    loaded.append(candidate)
    return original_load(candidate, *args, **kwargs)

dotenv.load_dotenv = isolated_load
from app.persistence.database import DATABASE_URL, engine
from sqlalchemy.engine import make_url

try:
    expected = Path(os.environ['TEST_EXPECTED_DATABASE_FILE']).resolve()
    url = make_url(DATABASE_URL)
    assert url.drivername == 'sqlite', 'Unexpected database driver'
    assert Path(url.database).resolve() == expected, 'Configuration precedence mismatch'
    assert Path(engine.url.database).resolve() == expected, 'Engine configuration mismatch'
    assert loaded == [backend.parent / '.env', backend / '.env'], 'Configuration load order mismatch'
    with engine.connect() as connection:
        assert connection.exec_driver_sql('SELECT 1').scalar() == 1
    print('database_configuration_ok')
finally:
    engine.dispose()
"""


@pytest.mark.parametrize("configuration", ["environment", "root", "backend", "default"])
def test_database_configuration_precedence_isolated(tmp_path: Path, configuration: str):
    source_backend = Path(__file__).resolve().parents[1]
    project = tmp_path / "isolated_project"
    backend = project / "backend"
    persistence = backend / "app" / "persistence"
    persistence.mkdir(parents=True)
    (backend / "app" / "__init__.py").write_text("", encoding="utf-8")
    (persistence / "__init__.py").write_text("", encoding="utf-8")
    # Use the actual modules, with __file__ rooted exclusively in this fixture.
    for name in ("database.py", "models.py"):
        shutil.copyfile(source_backend / "app" / "persistence" / name, persistence / name)

    environment_db = project / "environment.db"
    root_db = project / "root.db"
    backend_db = project / "backend.db"
    if configuration in {"environment", "root"}:
        (project / ".env").write_text(f"DATABASE_URL=sqlite:///{root_db.as_posix()}\n", encoding="utf-8")
    if configuration in {"environment", "root", "backend"}:
        (backend / ".env").write_text(f"DATABASE_URL=sqlite:///{backend_db.as_posix()}\n", encoding="utf-8")

    # Do not inherit production configuration or credentials from the test host.
    child_env = {
        key: os.environ[key]
        for key in ("SYSTEMROOT", "WINDIR", "PATH", "TEMP", "TMP")
        if key in os.environ
    }
    child_env.update(PYTHONPATH=str(backend), PYTHONUTF8="1", PYTHONDONTWRITEBYTECODE="1")
    expected = {
        "environment": environment_db,
        "root": root_db,
        "backend": backend_db,
        "default": backend / "data" / "demo.db",
    }[configuration]
    child_env["TEST_EXPECTED_DATABASE_FILE"] = str(expected)
    if configuration == "environment":
        child_env["DATABASE_URL"] = f"sqlite:///{environment_db.as_posix()}"

    result = subprocess.run(
        [sys.executable, "-c", _PROBE],
        cwd=backend,
        env=child_env,
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=30,
    )
    # Never include arbitrary child output (and therefore configuration) on failure.
    assert result.returncode == 0, f"Isolated database configuration failed: {configuration}"
    assert result.stdout.strip() == "database_configuration_ok"
    assert expected.is_file()
