"""SQLite checkpointer helper for mid-run crash and resume verification."""

from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

from langgraph.checkpoint.serde.jsonplus import JsonPlusSerializer
from langgraph.checkpoint.sqlite import SqliteSaver

_ALLOWED_TYPES = [
    ("msme_saathi.state", "EnterpriseProfile"),
    ("msme_saathi.state", "ExecutionPlan"),
    ("msme_saathi.state", "PlanStep"),
    ("msme_saathi.state", "ReplanRecord"),
]


@contextmanager
def sqlite_checkpointer(db_path: str | Path = "data/checkpoints.sqlite") -> Iterator[SqliteSaver]:
    """Open a persistent SQLite checkpointer backed by `db_path`."""
    path = Path(db_path)
    if str(db_path) != ":memory:":
        path.parent.mkdir(parents=True, exist_ok=True)

    conn = sqlite3.connect(str(db_path), check_same_thread=False)
    try:
        saver = SqliteSaver(conn, serde=JsonPlusSerializer(allowed_msgpack_modules=_ALLOWED_TYPES))
        saver.setup()
        yield saver
    finally:
        conn.close()
