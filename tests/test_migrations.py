import os
import sqlite3
import subprocess
import sys
from pathlib import Path

from sqlalchemy import create_engine

from app.db.base import Base


def run_migrate(project_root: Path, database: Path):
    env = os.environ.copy()
    env["DATABASE_URL"] = f"sqlite:///{database}"
    return subprocess.run(
        [sys.executable, "-m", "scripts.migrate"],
        cwd=project_root,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )


def run_alembic(project_root: Path, database: Path, revision: str):
    env = os.environ.copy()
    env["DATABASE_URL"] = f"sqlite:///{database}"
    return subprocess.run(
        [sys.executable, "-m", "alembic", "upgrade", revision],
        cwd=project_root,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )


def test_alembic_initializes_empty_database_idempotently(tmp_path):
    database = tmp_path / "assistant.db"
    project_root = Path(__file__).resolve().parents[1]

    for _ in range(2):
        result = run_migrate(project_root, database)
        assert result.returncode == 0, result.stderr

    with sqlite3.connect(database) as connection:
        tables = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            )
        }
        event_columns = {
            row[1] for row in connection.execute("PRAGMA table_info(events)")
        }
        revision = connection.execute(
            "SELECT version_num FROM alembic_version"
        ).fetchone()[0]

    assert {"events", "dialogue_sessions", "alembic_version"} <= tables
    assert "due_date" in event_columns
    assert revision == "0002_add_due_date"


def test_migrate_adopts_compatible_legacy_database(tmp_path):
    database = tmp_path / "legacy.db"
    Base.metadata.create_all(create_engine(f"sqlite:///{database}"))

    result = run_migrate(Path(__file__).resolve().parents[1], database)

    assert result.returncode == 0, result.stderr
    with sqlite3.connect(database) as connection:
        revision = connection.execute(
            "SELECT version_num FROM alembic_version"
        ).fetchone()[0]
        event_count = connection.execute("SELECT count(*) FROM events").fetchone()[0]
    assert revision == "0002_add_due_date"
    assert event_count == 0


def test_migrate_upgrades_unversioned_revision_0001_schema(tmp_path):
    database = tmp_path / "legacy-0001.db"
    project_root = Path(__file__).resolve().parents[1]
    created = run_alembic(project_root, database, "0001_initial")
    assert created.returncode == 0, created.stderr
    with sqlite3.connect(database) as connection:
        connection.execute("DROP TABLE alembic_version")

    result = run_migrate(project_root, database)

    assert result.returncode == 0, result.stderr
    with sqlite3.connect(database) as connection:
        columns = {
            row[1] for row in connection.execute("PRAGMA table_info(events)")
        }
        revision = connection.execute(
            "SELECT version_num FROM alembic_version"
        ).fetchone()[0]
    assert "due_date" in columns
    assert revision == "0002_add_due_date"
