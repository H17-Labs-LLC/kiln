"""`kiln decide open | <id> | list [--open]` on the demo instance: ids, verbatim answers, supersession, refusals."""

import json
from pathlib import Path

import pytest
import yaml
from jsonschema import Draft202012Validator

from kiln import cli, decisions
from kiln._common import store

REPO = Path(__file__).resolve().parents[2]
DECISIONS = Path("projects") / "demo_robot" / "decisions"


def run(argv: list[str], capsys: pytest.CaptureFixture[str]) -> tuple[int, str, str]:
    """Call the CLI in-process; argparse's SystemExit becomes the exit code."""
    try:
        code = cli.main(argv)
    except SystemExit as exc:
        code = exc.code if isinstance(exc.code, int) else 1
    captured = capsys.readouterr()
    return code, captured.out, captured.err


def front_matter(path: Path) -> dict:
    """The front matter the way tests/test_schemas.py reads a record .md: between the first two `---` lines."""
    text = path.read_text(encoding="utf-8")
    assert text.startswith("---\n")
    return yaml.safe_load(text.split("---\n")[1])


def assert_valid(path: Path) -> dict:
    schema = json.loads((REPO / "schemas" / "decision-frontmatter.schema.json").read_text(encoding="utf-8"))
    data = front_matter(path)
    errors = [e.message for e in Draft202012Validator(schema).iter_errors(data)]
    assert not errors, errors
    assert next(iter(data)) == "schema"
    return data


# --- list -------------------------------------------------------------------------------------------------------

def test_list_on_the_demo(demo_workspace: Path, capsys) -> None:
    code, out, err = run(["decide", "list"], capsys)
    assert code == 0, err
    assert out.splitlines() == [
        "id | status | title | decision",
        "dec-0001 | decided | Holdout size for 0.2.0 | two: one front, one back",
        "dec-0002 | open | Retrain 0.3.0: continue or start fresh | -",
        "2 decisions in demo_robot: 1 open, 1 decided",
    ]


def test_list_open_on_the_demo(demo_workspace: Path, capsys) -> None:
    code, out, _ = run(["decide", "list", "--open"], capsys)
    assert code == 0
    assert out.splitlines() == [
        "id | opened_at | title | question",
        "dec-0002 | 2030-01-04T09:05:00+00:00 | Retrain 0.3.0: continue or start fresh | Does the 0.3.0 run "
        "continue from the step-800 LoRA or start fresh from the base?",
        "1 open decision in demo_robot",
        'next: ask the person; record each answer verbatim with kiln decide <dec-id> "<answer>"',
    ]


# --- open, decide, supersede ------------------------------------------------------------------------------------

def test_open_writes_the_next_id(demo_workspace: Path, frozen_clock, capsys) -> None:
    code, out, err = run(["decide", "open", "Holdout size for 0.3.0", "--question", "How many images hold out?",
                          "--option", "one", "--option", "two", "--context", "census: 2 holdout images"], capsys)
    assert code == 0, err
    assert out.splitlines() == [
        "dec-0003 opened: Holdout size for 0.3.0",
        "file: projects/demo_robot/decisions/dec-0003.md",
        'next: ask the person the question; then kiln decide dec-0003 "<their answer>"',
    ]
    path = demo_workspace / DECISIONS / "dec-0003.md"
    data = assert_valid(path)
    assert (data["id"], data["status"], data["options"], data["opened_at"]) == (
        "dec-0003", "open", ["one", "two"], "2030-01-01T00:00:00+00:00")
    assert data["decided_by"] is None and data["decision"] is None
    body = path.read_text(encoding="utf-8").split("---\n", 2)[2]
    assert "census: 2 holdout images" in body and 'kiln decide dec-0003 "<answer>"' in body


def test_decide_records_the_answer_verbatim_in_both_spellings(demo_workspace: Path, frozen_clock, capsys) -> None:
    path = demo_workspace / DECISIONS / "dec-0002.md"
    body = path.read_text(encoding="utf-8").split("---\n", 2)[2]
    code, out, err = run(["decide", "dec-0002", "fresh from the base", "--rationale", "avoid the finger errors"],
                         capsys)
    assert (code, out.splitlines()) == (0, ["dec-0002 decided: fresh from the base"]), err
    data = assert_valid(path)
    assert (data["status"], data["decision"], data["rationale"], data["decided_by"], data["decided_at"]) == (
        "decided", "fresh from the base", "avoid the finger errors", "operator", "2030-01-01T00:00:00+00:00")
    assert path.read_text(encoding="utf-8").split("---\n", 2)[2] == body, "the body is kept as it was"
    run(["decide", "open", "Steps for 0.3.0", "--question", "How many steps?"], capsys)
    code, out, _ = run(["decide", "decide", "dec-0003", "1200"], capsys)
    assert (code, out.splitlines()) == (0, ["dec-0003 decided: 1200"])


def test_a_decided_record_is_not_rewritten(demo_workspace: Path, capsys) -> None:
    path = demo_workspace / DECISIONS / "dec-0001.md"
    before = path.read_bytes()
    code, out, err = run(["decide", "dec-0001", "three"], capsys)
    assert code == 2 and out == ""
    assert err.splitlines() == [
        "dec-0001 is decided (2030-01-02T09:40:00+00:00); a recorded answer is not rewritten",
        'to change course: kiln decide open "<title>" --question "<q>" --supersedes dec-0001',
    ]
    assert path.read_bytes() == before


def test_deciding_a_successor_supersedes_the_old_one(demo_workspace: Path, frozen_clock, capsys) -> None:
    code, _, err = run(["decide", "open", "Holdout size for 0.3.0", "--question", "How many?",
                        "--supersedes", "dec-0001"], capsys)
    assert code == 0, err
    assert front_matter(demo_workspace / DECISIONS / "dec-0001.md")["status"] == "decided", "not before the answer"
    code, out, _ = run(["decide", "dec-0003", "three"], capsys)
    assert out.splitlines() == ["dec-0003 decided: three", "dec-0001 superseded by dec-0003"]
    old = assert_valid(demo_workspace / DECISIONS / "dec-0001.md")
    assert (old["status"], old["decision"]) == ("superseded", "two: one front, one back")
    assert assert_valid(demo_workspace / DECISIONS / "dec-0003.md")["supersedes"] == "dec-0001"


@pytest.mark.parametrize("argv, first_line", [
    (["decide", "dec-0009", "yes"], "no decision dec-0009 in this project (decisions: dec-0001, dec-0002)"),
    (["decide", "dec-0002", "   "], "the answer is empty: pass the person's words verbatim, in quotes"),
    (["decide", "open", "  ", "--question", "q?"], "a decision needs a title"),
    (["decide", "open", "t", "--question", "q?", "--supersedes", "dec-0042"],
     "no decision dec-0042 in this project (decisions: dec-0001, dec-0002)"),
])
def test_refusals_exit_2(demo_workspace: Path, capsys, argv: list[str], first_line: str) -> None:
    code, out, err = run(argv, capsys)
    assert (code, out) == (2, "")
    assert err.splitlines()[0] == first_line
    assert sorted(p.name for p in (demo_workspace / DECISIONS).iterdir()) == ["dec-0001.md", "dec-0002.md"]


def test_an_unknown_action_is_a_usage_error(demo_workspace: Path, capsys) -> None:
    code, _, err = run(["decide", "maybe"], capsys)
    assert code == 2 and "invalid choice: 'maybe'" in err


# --- reading files a person wrote -------------------------------------------------------------------------------

def test_a_broken_file_is_skipped_and_reported(demo_workspace: Path, capsys) -> None:
    (demo_workspace / DECISIONS / "dec-0007.md").write_text("no front matter here\n", encoding="utf-8")
    code, out, err = run(["decide", "list"], capsys)
    assert code == 1
    assert err.splitlines() == ["skipped decisions/dec-0007.md: does not start with a --- line (YAML front matter)"]
    assert out.splitlines()[-1] == "2 decisions in demo_robot: 1 open, 1 decided"


def test_unquoted_timestamps_and_crlf_are_read(tmp_path: Path) -> None:
    folder = tmp_path / "decisions"
    folder.mkdir()
    text = ("---\nschema: kiln/decision-frontmatter/1\nid: dec-0005\nproject: demo_robot\ntitle: T\nstatus: open\n"
            "question: Q?\nopened_at: 2030-01-03T08:00:00+00:00\n---\n\nbody\n")
    (folder / "dec-0005.md").write_bytes(text.replace("\n", "\r\n").encode("utf-8"))
    found, problems = decisions.load_all(tmp_path)
    assert problems == []
    assert found[0].front["opened_at"] == "2030-01-03T08:00:00+00:00"
    assert found[0].body == "\nbody\n"
    assert decisions.open_rows(found)[0]["id"] == "dec-0005"


def test_split_front_shapes() -> None:
    assert decisions.split_front("---\na: 1\n---\nbody\n") == ("a: 1\n", "body\n")
    assert decisions.split_front("---\na: 1\n---") == ("a: 1\n", "")
    with pytest.raises(ValueError, match="no closing"):
        decisions.split_front("---\na: 1\n")


def test_a_held_lock_exits_4_and_is_journaled(demo_workspace: Path, frozen_clock, capsys, monkeypatch) -> None:
    real_lock, project = store.lock, demo_workspace / "projects" / "demo_robot"

    def lock(root, tool, timeout=None):  # only the project's lock is "held"; the journal's still works
        if Path(root).resolve() == project.resolve():
            raise store.Held("another process holds the store lock (test)", Path(root))
        return real_lock(root, tool, timeout)

    monkeypatch.setattr(store, "lock", lock)
    code, _, _ = run(["decide", "dec-0002", "fresh"], capsys)
    assert code == 4
    journal = (demo_workspace / "journal" / "2030-01-01.md").read_text(encoding="utf-8")
    assert "stuck: another process holds the store lock (test)" in journal
