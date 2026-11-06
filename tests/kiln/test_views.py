"""Each project question kiln answers from its files, checked against the same question asked in SQL.

The demo's records are loaded into an in-memory SQLite database (one table per record kind, only the fields the
questions read); each test asks the question there and compares the answer with what the command prints, so the
command and an independent query must agree:
- the project's health      -> `kiln status --json`
- lineage leaks             -> `kiln dataset check --leaks` (and `freeze` refuses a planted leak)
- images per tier           -> `kiln dataset census`
- members per split         -> `kiln dataset census --split`
"""

import json
import sqlite3
from pathlib import Path

import pytest
import yaml
from kiln_project_helpers import SLUG, latest_images, read_jsonl, run

from kiln.project import snapshot as snapshot_mod


def _db(home: Path) -> sqlite3.Connection:
    """The demo project as rows: one table per record kind, only the fields the four questions read."""
    project = home / "projects" / SLUG
    db = sqlite3.connect(":memory:")
    db.executescript("""
        CREATE TABLE projects (project TEXT PRIMARY KEY, title TEXT, status TEXT, phase TEXT);
        CREATE TABLE images (image TEXT PRIMARY KEY, project TEXT, tier TEXT,
                                      width INTEGER, height INTEGER, lineage_root TEXT);
        CREATE TABLE gaps (project TEXT, target INTEGER, current INTEGER, status TEXT);
        CREATE TABLE decisions (project TEXT, id TEXT, status TEXT);
        CREATE TABLE runs (id TEXT, project TEXT);
        CREATE TABLE snapshots (snapshot TEXT, project TEXT);
        CREATE TABLE members (snapshot TEXT, image TEXT, split TEXT);
    """)
    meta = yaml.safe_load((project / "project.yaml").read_text(encoding="utf-8"))
    state = json.loads((project / "state.json").read_text(encoding="utf-8"))
    db.execute("INSERT INTO projects VALUES (?,?,?,?)", (SLUG, meta["title"], state["status"], state["phase"]))
    for image, rec in latest_images(project).items():
        db.execute("INSERT INTO images VALUES (?,?,?,?,?,?)",
                   (image, SLUG, rec["tier"], rec["width"], rec["height"], rec["lineage"]["root"]))
    for gap in yaml.safe_load((project / "coverage.yaml").read_text(encoding="utf-8"))["gaps"]:
        db.execute("INSERT INTO gaps VALUES (?,?,?,?)", (SLUG, gap["target"], gap["current"], gap["status"]))
    for path in sorted((project / "decisions").glob("dec-*.md")):
        front = yaml.safe_load(path.read_text(encoding="utf-8").split("---")[1])
        db.execute("INSERT INTO decisions VALUES (?,?,?)", (SLUG, front["id"], front["status"]))
    for path in sorted((project / "runs").glob("*/run.json")):
        db.execute("INSERT INTO runs VALUES (?,?)", (json.loads(path.read_text("utf-8"))["id"], SLUG))
    for path in sorted((project / "snapshots").glob("*.json")):
        snap = json.loads(path.read_text(encoding="utf-8"))
        db.execute("INSERT INTO snapshots VALUES (?,?)", (snap["id"], SLUG))
        db.executemany("INSERT INTO members VALUES (?,?,?)",
                       [(snap["id"], m["image"], m["split"]) for m in snap["members"]])
    return db


# --- the project's health ---------------------------------------------------------------------------------------

def test_status_counts_match_sql(demo_workspace: Path, capsys):
    row = _db(demo_workspace).execute("""
        SELECT x.project, x.title, x.status, x.phase,
               (SELECT COUNT(*) FROM images r WHERE r.project = x.project AND r.tier = 'CORE'),
               (SELECT COUNT(*) FROM images r WHERE r.project = x.project
                                                        AND r.tier <> 'REJECTED'),
               (SELECT COUNT(*) FROM gaps g WHERE g.project = x.project
                                                     AND g.status IN ('open', 'filling')
                                                     AND g.current < g.target),
               (SELECT COUNT(*) FROM decisions d WHERE d.project = x.project AND d.status = 'open'),
               (SELECT COUNT(*) FROM runs t WHERE t.project = x.project),
               (SELECT COUNT(*) FROM snapshots s WHERE s.project = x.project)
        FROM projects x""").fetchone()
    code, out = run(["status", "--json"], capsys)
    assert code == 0, out
    got = json.loads(out[0])
    keys = ("project", "title", "status", "phase", "core_refs", "total_refs", "open_gaps", "open_decisions",
            "runs", "snapshots")
    assert tuple(got[k] for k in keys) == row
    assert row[4:] == (2, 18, 3, 1, 1, 2)  # the demo's numbers, so a broken fixture cannot pass silently
    assert got["stage"] == "06-split"
    code, out = run(["status"], capsys)
    assert out[:2] == [f"project: {SLUG} (Demo Robot)", "status: curating"]
    assert "open_decisions: 1" in out and "goal: new version 0.3.0 from 0.2.0 (easel run run-20300101-001)" in out


# --- images per tier --------------------------------------------------------------------------------------------

def test_census_matches_sql(demo_workspace: Path, capsys):
    rows = _db(demo_workspace).execute("""
        SELECT tier, COUNT(*),
               SUM(CASE WHEN width IS NOT NULL AND MIN(width, height) < 768 THEN 1 ELSE 0 END),
               SUM(CASE WHEN width IS NULL THEN 1 ELSE 0 END)
        FROM images GROUP BY project, tier""").fetchall()
    code, out = run(["dataset", "census"], capsys)
    assert code == 0, out
    printed = {line.split()[0]: tuple(int(x) for x in line.split()[1:]) for line in out[1:-1]}
    assert printed == {tier: (n, short, unknown) for tier, n, short, unknown in rows}
    assert printed["CORE"] == (2, 2, 0) and sum(v[0] for v in printed.values()) == 19
    assert out[-1].split()[:2] == ["total", "19"]


def test_census_counts_unknown_dimensions(demo_workspace: Path, capsys):
    project = demo_workspace / "projects" / SLUG
    lines = read_jsonl(project / "images.jsonl")
    imported = [line for line in lines if line["root"] == "imports"]
    last = dict(imported[-1], width=None, height=None, at="2030-01-05T00:00:00+00:00")  # an import without a header
    with (project / "images.jsonl").open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(last) + "\n")
    code, out = run(["dataset", "census"], capsys)
    candidate = next(line for line in out if line.startswith("CANDIDATE")).split()
    assert candidate[1:] == ["5", "4", "1"]


# --- members per split ------------------------------------------------------------------------------------------

def test_census_split_matches_sql(demo_workspace: Path, capsys):
    rows = _db(demo_workspace).execute(
        "SELECT snapshot, split, COUNT(*) FROM members GROUP BY snapshot, split").fetchall()
    code, out = run(["dataset", "census", "--split"], capsys)
    assert code == 0, out
    printed = [tuple(line.split()) for line in out[1:]]
    snapshots = {(w, s, int(n)) for w, s, n in printed if w != "current"}
    assert snapshots == set(rows)
    # the current split of the kept images: the three imports are not split yet
    assert {(s, int(n)) for w, s, n in printed if w == "current"} == {("train", 10), ("holdout", 2), ("unassigned", 3)}


# --- lineage leaks ----------------------------------------------------------------------------------------------

LEAK_SQL = """
    SELECT m.snapshot, COALESCE(r.lineage_root, r.image), COUNT(DISTINCT m.split)
    FROM members m JOIN images r ON r.image = m.image
    GROUP BY m.snapshot, COALESCE(r.lineage_root, r.image)
    HAVING COUNT(DISTINCT m.split) > 1"""


def test_no_lineage_leak_in_the_demo(demo_workspace: Path, capsys):
    assert _db(demo_workspace).execute(LEAK_SQL).fetchall() == []
    code, out = run(["dataset", "check", "--leaks"], capsys)
    assert (code, out) == (0, ["leaks: 0"])


def test_planted_leak_in_a_snapshot_is_found(demo_workspace: Path, capsys):
    project = demo_workspace / "projects" / SLUG
    snap = json.loads((project / "snapshots" / "0.2.0.json").read_text(encoding="utf-8"))
    crop = next(m for m in snap["members"] if m["image"] == "img-0f9fa34cfeb0")  # a crop of img-fa40ee72d799
    crop["split"] = "holdout"
    snap.update(id=f"{SLUG}/0.2.1", version="0.2.1", members_sha256=snapshot_mod.members_sha256(snap["members"]))
    (project / "snapshots" / "0.2.1.json").write_text(json.dumps(snap, indent=2), encoding="utf-8")
    want = _db(demo_workspace).execute(LEAK_SQL).fetchall()
    assert want == [(f"{SLUG}/0.2.1", "img-fa40ee72d799", 2)]
    code, out = run(["dataset", "check", "--leaks"], capsys)
    assert code == 1, out
    assert out == [f"leak: {SLUG}/0.2.1 lineage img-fa40ee72d799 on holdout, train "
                   "(2 images: img-0f9fa34cfeb0 img-fa40ee72d799)", "leaks: 1"]


def test_planted_leak_in_the_split_is_refused_by_freeze(demo_workspace: Path, capsys):
    project = demo_workspace / "projects" / SLUG
    code, out = run(["images", "assign", "img-0f9fa34cfeb0", "--split", "holdout"], capsys)
    assert code == 0 and out[1].startswith("  warn: lineage img-fa40ee72d799 now sits on train and holdout"), out
    code, out = run(["dataset", "check", "--leaks"], capsys)
    assert code == 1 and out[0].startswith("leak: current lineage img-fa40ee72d799 on holdout, train"), out
    # freeze --only-new would not see it (both are old members); a fresh version from scratch does
    code, out = run(["dataset", "freeze", "1.0.0", "--only-new"], capsys)
    assert code == 0, out  # only the three imports are new: no leak among them
    for version in ("0.1.0", "0.2.0", "1.0.0"):
        (project / "snapshots" / f"{version}.json").unlink()
    code, out = run(["dataset", "freeze", "1.1.0", "--only-new"], capsys)
    assert code == 2, out
    assert out[0] == "REFUSED: 1 lineage leak(s): one lineage on both sides of the split"
    assert out[1].startswith(f"  leak: {SLUG}/1.1.0 lineage img-fa40ee72d799 on holdout, train")
    assert not (project / "snapshots" / "1.1.0.json").exists()


@pytest.mark.parametrize("split", ["train", "holdout"])
def test_leak_check_ignores_reference_only_children(demo_workspace: Path, capsys, split):
    code, out = run(["images", "assign", "img-fa40ee72d799", "--split", split], capsys)
    code, out = run(["images", "assign", "img-0f9fa34cfeb0", "--split", "reference_only"], capsys)
    code, out = run(["dataset", "check", "--leaks"], capsys)
    assert (code, out) == (0, ["leaks: 0"])
