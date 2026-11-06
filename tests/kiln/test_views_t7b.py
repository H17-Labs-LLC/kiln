"""`kiln coverage needs` and `kiln decide list --open`, checked against the same question asked in SQL.

Each test loads the project files into an in-memory SQLite database (only the fields the question reads), asks
the question as a view, and checks that the command prints the same rows in the same order. SQL leaves ties
unordered, so the exact comparison adds the file order (coverage) or the id (decisions) as the last sort key; the
commands break ties the same way. Planted rows cover every branch: each priority, met and waived gaps, a gap at
target with status still open, superseded and decided decisions, and opened_at out of id order.
"""

import json
import sqlite3
from pathlib import Path

import pytest
import yaml

from kiln import cli

PROJECT = Path("projects") / "demo_robot"

GAPS_DDL = """
CREATE TABLE gaps (
    n INTEGER PRIMARY KEY, project TEXT NOT NULL, dimension TEXT NOT NULL, bucket TEXT NOT NULL,
    target INTEGER NOT NULL, current INTEGER DEFAULT 0, priority TEXT DEFAULT 'normal',
    status TEXT DEFAULT 'open'
);
CREATE VIEW open_gaps AS
    SELECT project, dimension, bucket, target, current,
           target - current AS remaining, priority, status
      FROM gaps
     WHERE status IN ('open', 'filling') AND current < target
     ORDER BY CASE priority WHEN 'blocker' THEN 0 WHEN 'high' THEN 1 WHEN 'normal' THEN 2 ELSE 3 END,
              remaining DESC;
"""
GAPS_EXACT = """
SELECT g.project, g.dimension, g.bucket, g.target, g.current,
       g.target - g.current AS remaining, g.priority, g.status
  FROM gaps g
 WHERE g.status IN ('open', 'filling') AND g.current < g.target
 ORDER BY CASE g.priority WHEN 'blocker' THEN 0 WHEN 'high' THEN 1 WHEN 'normal' THEN 2 ELSE 3 END,
          remaining DESC, g.n
"""
DECISIONS_DDL = """
CREATE TABLE decisions (
    id TEXT PRIMARY KEY, project TEXT NOT NULL, title TEXT NOT NULL, question TEXT,
    status TEXT DEFAULT 'open', opened_at TEXT
);
CREATE VIEW open_decisions AS
    SELECT project, id, title, question, opened_at
      FROM decisions
     WHERE status = 'open'
     ORDER BY project, opened_at;
"""
DECISIONS_EXACT = """
SELECT project, id, title, question, opened_at FROM decisions WHERE status = 'open'
 ORDER BY project, opened_at, id
"""


def run_json(argv: list[str], capsys: pytest.CaptureFixture[str]) -> dict:
    code = cli.main([*argv, "--json"])
    captured = capsys.readouterr()
    assert code == 0, captured.err
    return json.loads(captured.out)


def gap(dimension: str, bucket: str, target: int, current: int, priority: str, status: str) -> dict:
    return {"dimension": dimension, "bucket": bucket, "target": target, "current": current, "priority": priority,
            "status": status, "note": None, "legacy_id": None}


def test_coverage_needs_matches_sql(demo_workspace: Path, capsys) -> None:
    path = demo_workspace / PROJECT / "coverage.yaml"
    board = yaml.safe_load(path.read_text(encoding="utf-8"))
    board["gaps"] += [
        gap("expression", "smile", 4, 1, "blocker", "filling"),
        gap("outfit", "coat", 6, 0, "high", "open"),        # more remaining than framing/close: comes first
        gap("environment", "street", 2, 2, "normal", "open"),  # at target although still marked open: excluded
        gap("lighting", "dusk", 5, 0, "low", "waived"),      # waived: excluded
        gap("framing", "wide", 2, 1, "low", "open"),         # ties with nothing; after lighting/night (2 remaining)
        gap("viewpoint", "front", 3, 2, "normal", "open"),   # ties viewpoint/back (1 remaining): file order
    ]
    path.write_text(yaml.safe_dump(board, sort_keys=False), encoding="utf-8")

    db = sqlite3.connect(":memory:")
    db.executescript(GAPS_DDL)
    db.executemany("INSERT INTO gaps (n, project, dimension, bucket, target, current,"
                   " priority, status) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                   [(n, board["project"], g["dimension"], g["bucket"], g["target"], g["current"], g["priority"],
                     g["status"]) for n, g in enumerate(board["gaps"], 1)])
    exact = db.execute(GAPS_EXACT).fetchall()
    view = db.execute("SELECT * FROM open_gaps").fetchall()
    assert sorted(view) == sorted(exact), "the exact query selects what the view selects"

    rows = run_json(["coverage", "needs"], capsys)["gaps"]
    got = [(r["project"], r["dimension"], r["bucket"], r["target"], r["current"], r["remaining"], r["priority"],
            r["status"]) for r in rows]
    assert got == exact
    assert [f"{r['dimension']}/{r['bucket']}" for r in rows] == [
        "expression/smile", "outfit/coat", "framing/close", "viewpoint/back", "viewpoint/front", "lighting/night",
        "framing/wide"]


def test_decide_list_open_matches_sql(demo_workspace: Path, frozen_clock, capsys, monkeypatch) -> None:
    for title, when in (("Steps for 0.3.0", "2030-01-06T10:00:00+00:00"),
                        ("Eval suite for 0.3.0", "2030-01-03T08:00:00+00:00"),
                        ("Same minute as dec-0002", "2030-01-04T09:05:00+00:00"),
                        ("Answered at once", "2030-01-02T00:00:00+00:00")):
        monkeypatch.setenv("KILN_NOW", when)
        assert cli.main(["decide", "open", title, "--question", f"{title}?"]) == 0
    assert cli.main(["decide", "dec-0006", "yes"]) == 0  # decided: excluded
    assert cli.main(["decide", "open", "Replace dec-0001", "--question", "q?", "--supersedes", "dec-0001"]) == 0
    assert cli.main(["decide", "dec-0007", "replaced"]) == 0  # dec-0001 becomes superseded: excluded
    capsys.readouterr()

    db = sqlite3.connect(":memory:")
    db.executescript(DECISIONS_DDL)
    for path in sorted((demo_workspace / PROJECT / "decisions").glob("dec-*.md")):
        front = yaml.safe_load(path.read_text(encoding="utf-8").split("---\n")[1])
        db.execute("INSERT INTO decisions VALUES (?, ?, ?, ?, ?, ?)",
                   (front["id"], front["project"], front["title"], front["question"], front["status"],
                    front["opened_at"]))
    exact = db.execute(DECISIONS_EXACT).fetchall()
    view = db.execute("SELECT * FROM open_decisions").fetchall()
    assert sorted(view) == sorted(exact)

    rows = run_json(["decide", "list", "--open"], capsys)["decisions"]
    got = [(r["project"], r["id"], r["title"], r["question"], r["opened_at"]) for r in rows]
    assert got == exact
    assert [r["id"] for r in rows] == ["dec-0004", "dec-0002", "dec-0005", "dec-0003"]
