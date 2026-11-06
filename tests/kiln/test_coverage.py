"""`kiln coverage board | needs` on the demo instance: the counting rule, the refresh, the open-gap list."""

import json
from pathlib import Path

import pytest
import yaml
from jsonschema import Draft202012Validator

from kiln import cli, coverage

REPO = Path(__file__).resolve().parents[2]
PROJECT = Path("projects") / "demo_robot"


def run(argv: list[str], capsys: pytest.CaptureFixture[str]) -> tuple[int, str, str]:
    """Call the CLI in-process; argparse's SystemExit becomes the exit code."""
    try:
        code = cli.main(argv)
    except SystemExit as exc:
        code = exc.code if isinstance(exc.code, int) else 1
    captured = capsys.readouterr()
    return code, captured.out, captured.err


def board_file(home: Path) -> Path:
    return home / PROJECT / "coverage.yaml"


def assert_valid_board(path: Path) -> None:
    schema = json.loads((REPO / "schemas" / "coverage.schema.json").read_text(encoding="utf-8"))
    errors = [e.message for e in Draft202012Validator(schema).iter_errors(yaml.safe_load(path.read_text("utf-8")))]
    assert not errors, errors


def image(**fields) -> dict:
    base = {"id": "img-000000000001", "tier": "SECONDARY", "disposition": "ACCEPT_TRAIN", "split": "train",
            "viewpoint": None, "framing": None, "labels": []}
    return {**base, **fields}


def write_gaps(home: Path, gaps: list[dict], header: str = "") -> None:
    record = {"schema": "kiln/coverage/1", "project": "demo_robot", "gaps": gaps}
    board_file(home).write_text(header + yaml.safe_dump(record, sort_keys=False), encoding="utf-8")


def gap(dimension: str, bucket: str, target: int, current: int, priority: str = "normal",
        status: str = "open") -> dict:
    return {"dimension": dimension, "bucket": bucket, "target": target, "current": current, "priority": priority,
            "status": status, "note": None, "legacy_id": None}


# --- the counting rule ------------------------------------------------------------------------------------------

def test_only_accepted_or_split_images_count() -> None:
    assert coverage.counts_as_coverage(image())
    assert coverage.counts_as_coverage(image(disposition="ACCEPT_CORE", split="unassigned"))
    assert coverage.counts_as_coverage(image(disposition=None, split="holdout"))
    assert not coverage.counts_as_coverage(image(disposition="REFINE", split="unassigned"))
    assert not coverage.counts_as_coverage(image(disposition="ACCEPT_REFERENCE_ONLY", split="reference_only"))
    assert not coverage.counts_as_coverage(image(tier="REJECTED"))
    assert not coverage.counts_as_coverage(image(tier="CANDIDATE", disposition=None, split="unassigned"))


def test_fields_and_labels_fill_buckets_once_per_image() -> None:
    one = image(viewpoint="back", framing="close", labels=["framing:close", "outfit:default", "check:head"])
    assert coverage.image_buckets(one) == {("viewpoint", "back"), ("framing", "close"), ("outfit", "default"),
                                           ("check", "head")}
    totals = coverage.count([one, image(id="img-000000000002", labels=["outfit:default"]),
                             image(id="img-000000000003", tier="REJECTED", labels=["outfit:default"])])
    assert totals[("framing", "close")] == 1
    assert totals[("outfit", "default")] == 2


def test_status_follows_the_count() -> None:
    assert [coverage.status_for(n, 3) for n in (0, 1, 2, 3, 4)] == ["open", "filling", "filling", "met", "met"]


# --- board ------------------------------------------------------------------------------------------------------

def test_board_on_the_demo_refreshes_and_writes(demo_workspace: Path, frozen_clock, capsys) -> None:
    before = board_file(demo_workspace).read_text(encoding="utf-8")
    code, out, _ = run(["coverage", "board"], capsys)
    assert code == 0
    lines = out.splitlines()
    assert lines[:6] == [
        "dimension | bucket | target | current | remaining | priority | status",
        "framing | close | 4 | 3 | 1 | high | filling",
        "viewpoint | back | 3 | 2 | 1 | normal | filling",
        "viewpoint | profile | 3 | 3 | 0 | normal | met",
        "lighting | night | 2 | 0 | 2 | low | open",
        "8/12 placements filled across 4 buckets: 1 met, 3 open or filling, 0 waived",
    ]
    assert "changed: viewpoint/back: status open -> filling" in lines
    # the demo's images carry lighting:<bucket> labels, so the board has no unlabelled-dimension note
    assert not any(line.startswith("note: no image carries a") for line in lines), lines
    assert lines[-2:] == ["coverage.yaml updated: 1 bucket changed", "next: kiln coverage needs"]
    after = board_file(demo_workspace).read_text(encoding="utf-8")
    assert after != before
    assert after.splitlines()[0] == before.splitlines()[0], "the leading comment is kept"
    assert_valid_board(board_file(demo_workspace))
    assert yaml.safe_load(after)["gaps"][0]["status"] == "filling"
    code, out, _ = run(["coverage", "board"], capsys)
    assert code == 0 and "coverage.yaml unchanged" in out.splitlines()


def test_board_names_a_dimension_no_image_is_labelled_for(demo_workspace: Path, frozen_clock, capsys) -> None:
    write_gaps(demo_workspace, [gap("season", "winter", 2, 0)])
    code, out, _ = run(["coverage", "board", "--dry-run"], capsys)
    assert code == 0
    notes = [line for line in out.splitlines() if line.startswith("note: no image carries a `season:<bucket>`")]
    assert len(notes) == 1 and "lighting" in notes[0], out


def test_board_dry_run_writes_nothing(demo_workspace: Path, capsys) -> None:
    before = board_file(demo_workspace).read_bytes()
    code, out, _ = run(["coverage", "board", "--dry-run"], capsys)
    assert code == 0
    assert "dry run: 1 bucket would change; coverage.yaml not written" in out.splitlines()
    assert board_file(demo_workspace).read_bytes() == before


def test_board_counts_a_promoted_image_and_keeps_waived(demo_workspace: Path, frozen_clock, capsys) -> None:
    write_gaps(demo_workspace, [gap("viewpoint", "back", 3, 0), gap("framing", "wide", 1, 0),
                                gap("outfit", "default", 9, 5, status="waived"),
                                gap("framing", "medium", 2, 7, status="met")])
    code, out, _ = run(["coverage", "board", "--json"], capsys)
    assert code == 0
    result = json.loads(out)
    by_key = {(g["dimension"], g["bucket"]): g for g in result["gaps"]}
    assert (by_key[("viewpoint", "back")]["current"], by_key[("viewpoint", "back")]["status"]) == (2, "filling")
    assert (by_key[("framing", "wide")]["current"], by_key[("framing", "wide")]["status"]) == (1, "met")
    assert (by_key[("outfit", "default")]["current"], by_key[("outfit", "default")]["status"]) == (5, "waived")
    assert by_key[("framing", "medium")]["status"] == "met"  # 4 accepted medium shots >= 2
    assert result["written"] is True
    assert_valid_board(board_file(demo_workspace))


def test_board_says_when_inner_comments_are_dropped(demo_workspace: Path, capsys) -> None:
    text = board_file(demo_workspace).read_text(encoding="utf-8")
    board_file(demo_workspace).write_text(text.replace("- dimension: lighting", "# night shots later\n"
                                                       "- dimension: lighting"), encoding="utf-8")
    code, out, _ = run(["coverage", "board"], capsys)
    assert code == 0
    assert "note: comments below the header of coverage.yaml were not kept" in out.splitlines()


def test_board_refuses_a_missing_or_broken_file(demo_workspace: Path, capsys) -> None:
    board_file(demo_workspace).unlink()
    code, out, err = run(["coverage", "board"], capsys)
    assert code == 2 and out == ""
    assert err.splitlines()[0] == "no coverage.yaml in this project: the person writes the targets first"
    write_gaps(demo_workspace, [{"dimension": "viewpoint", "bucket": "back", "target": -1}])
    code, _, err = run(["coverage", "needs"], capsys)
    assert code == 2
    assert err.startswith("coverage.yaml: at /gaps/0")
    write_gaps(demo_workspace, [gap("viewpoint", "back", 3, 2), gap("viewpoint", "back", 4, 2)])
    code, _, err = run(["coverage", "board"], capsys)
    assert code == 2
    assert err.splitlines()[0] == "coverage.yaml: at /gaps/1: viewpoint/back is already gap 0; one gap per bucket"
    board_file(demo_workspace).write_text("schema: kiln/coverage/1\ngaps: [unclosed\n", encoding="utf-8")
    code, _, err = run(["coverage", "board"], capsys)
    assert code == 2 and err.startswith("coverage.yaml: not readable: ")


# --- needs ------------------------------------------------------------------------------------------------------

def test_needs_on_the_demo(demo_workspace: Path, capsys) -> None:
    code, out, err = run(["coverage", "needs"], capsys)
    assert code == 0, err
    assert out.splitlines() == [
        "dimension | bucket | target | current | remaining | priority | status",
        "framing | close | 4 | 3 | 1 | high | filling",
        "viewpoint | back | 3 | 2 | 1 | normal | open",
        "lighting | night | 2 | 0 | 2 | low | open",
        "3 open gaps in demo_robot: 4 placements still needed (one image fills one bucket in each dimension "
        "it carries)",
    ]


def test_needs_notes_a_stale_board_and_an_empty_one(demo_workspace: Path, capsys) -> None:
    write_gaps(demo_workspace, [gap("viewpoint", "back", 3, 0), gap("framing", "close", 1, 1, status="met")])
    code, out, _ = run(["coverage", "needs"], capsys)
    assert code == 0
    assert out.splitlines()[-1] == ("note: coverage.yaml is behind the images in 2 buckets (viewpoint/back 0 -> 2, "
                                    "framing/close 1 -> 3); run `kiln coverage board` to refresh it")
    write_gaps(demo_workspace, [gap("framing", "close", 1, 3, status="met")])
    code, out, _ = run(["coverage", "needs"], capsys)
    assert (code, out.splitlines()) == (0, ["no open gaps in demo_robot"])


# --- choosing the project ---------------------------------------------------------------------------------------

def test_several_projects_need_a_slug(demo_workspace: Path, capsys) -> None:
    other = demo_workspace / "projects" / "other_bot"
    other.mkdir()
    (other / "project.yaml").write_text("schema: kiln/project/1\nslug: other_bot\n", encoding="utf-8")
    code, _, err = run(["coverage", "needs"], capsys)
    assert code == 2
    assert err.splitlines() == ["this instance has 2 projects: demo_robot, other_bot", "pass --project <slug>"]
    code, out, _ = run(["coverage", "needs", "--project", "demo_robot"], capsys)
    assert code == 0 and out.startswith("dimension | bucket")
    code, _, err = run(["coverage", "needs", "--project", "nobody"], capsys)
    assert code == 2 and "no project nobody in this instance" in err


def test_no_instance_is_a_usage_error(tmp_path: Path, capsys) -> None:
    code, _, err = run(["coverage", "needs", "--workspace", str(tmp_path / "nowhere")], capsys)
    assert code == 2 and err.strip()
