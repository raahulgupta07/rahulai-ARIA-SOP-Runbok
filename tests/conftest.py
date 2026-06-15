"""Shared test setup.

DB-touching tests depend on the `db` fixture (builds the schema once per session
against whatever DATABASE_URL points to). Pure-unit tests (citation maths, the
compose-env-wiring check) take no fixture and need no database.

CI uses a throwaway postgres service. Locally, run inside the app container with
DATABASE_URL pointed at a *test* database (never the real one):

    docker exec -e DATABASE_URL=postgresql://docsensei:docsensei@db:5432/docsensei_test \
        docsensei-app python -m pytest tests/ -q
"""
import pathlib
import sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


@pytest.fixture(scope="session")
def db():
    """Build the schema once; yields the get_conn factory."""
    from app.db import init_db, get_conn
    init_db()
    yield get_conn
