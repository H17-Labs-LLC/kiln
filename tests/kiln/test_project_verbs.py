"""The smaller verbs of the project store: images, captions, project new/show/lint, status, and the planned stubs."""

import csv
import json
import shutil
from pathlib import Path

import pytest
from kiln_project_helpers import SLUG, assert_project_valid, latest_images, read_jsonl, run


def test_images_list_filters(demo_workspace: Path, capsys):
    code, out = run(["images", "list"], capsys)
    assert code == 0 and out[-1] == "19 images (15 kept, 1 without a verdict)", out
    assert any(line.endswith("raw/demo_robot-16.png  (legacy, untriaged)") for line in out)
    code, out = run(["images", "list", "--root", "imports"], capsys)
    assert out[-1] == "3 images (3 kept, 0 without a verdict)"
    code, out = run(["images", "list", "--untriaged"], capsys)
    assert out[0].startswith("img-27daf07ca88f") and out[-1].startswith("1 images")
    code, out = run(["images", "list", "--disposition", "none", "--json"], capsys)
    assert [json.loads(line)["id"] for line in out] == ["img-27daf07ca88f"]


def test_label_promote_and_assign_write_complete_records(demo_workspace: Path, capsys, frozen_clock):
    project = demo_workspace / "projects" / SLUG
    before = len(read_jsonl(project / "images.jsonl"))
    code, out = run(["images", "label", "img-27daf07ca88f", "--label", "light:night", "--viewpoint", "back",
                     "--disposition", "ACCEPT_TRAIN"], capsys)
    assert code == 0 and out == ["img-27daf07ca88f: disposition ACCEPT_TRAIN, viewpoint back, labels light:night"]
    code, out = run(["images", "label", "img-27daf07ca88f", "--remove-label", "light:night"], capsys)
    assert code == 0, out
    code, out = run(["images", "label", "img-27daf07ca88f", "--label", "Bad Label"], capsys)
    assert code == 2 and "namespace:value" in out[0]
    code, out = run(["images", "label", "img-27daf07ca88f"], capsys)
    assert code == 2 and out[0].startswith("nothing to record")
    code, out = run(["images", "promote", "img-27daf07ca88f", "--tier", "SECONDARY", "--reason", "clean back view"],
                    capsys)
    assert code == 0 and out == ["img-27daf07ca88f: tier CANDIDATE -> SECONDARY"]
    code, out = run(["images", "assign", "img-27daf07ca88f", "img-7e042496c6a7", "--split", "holdout"], capsys)
    assert code == 0, out
    assert out[0] == "img-27daf07ca88f: split unassigned -> holdout"
    assert "only ACCEPT_TRAIN and ACCEPT_CORE images are frozen" in out[2]
    record = latest_images(project)["img-27daf07ca88f"]
    assert (record["event"], record["tier"], record["split"], record["labels"]) == ("assign", "SECONDARY", "holdout", [])
    assert (record["disposition"], record["verdict_source"], record["legacy_untriaged"]) == ("ACCEPT_TRAIN", "operator", False)
    assert record["viewpoint"] == "back" and "tier CANDIDATE -> SECONDARY (clean back view)" in record["notes"]
    assert record["by"] == "kiln images assign" and record["at"] == "2030-01-01T00:00:00+00:00"
    assert len(read_jsonl(project / "images.jsonl")) == before + 5
    code, out = run(["images", "label", "img-000000000000", "--note", "x"], capsys)
    assert code == 2 and out[0].startswith("unknown image")
    assert_project_valid(project)


def test_child_outside_the_roots_and_bad_grid_cell(demo_workspace: Path, capsys, tmp_path: Path):
    outside = tmp_path / "stray.png"
    shutil.copyfile(demo_workspace / "dataset-images" / "raw" / "demo_robot-01.png", outside)
    outside.write_bytes(outside.read_bytes() + b"\x01")
    code, out = run(["images", "child", "img-010af39c9f5c", str(outside), "--transform", "flip"], capsys)
    assert code == 2 and "not inside one of the project's image roots" in out[0], out
    code, out = run(["images", "child", "img-010af39c9f5c", str(outside), "--transform", "flip",
                     "--grid-cell", "1,2,3"], capsys)
    assert code == 2 and "--grid-cell" in out[0]
    inside = demo_workspace / "projects" / SLUG / "images" / "edits" / "01-edit.png"
    inside.parent.mkdir(parents=True)
    shutil.copyfile(outside, inside)
    code, out = run(["images", "child", "img-010af39c9f5c", str(inside), "--transform", "edit"], capsys)
    assert code == 0, out
    assert out[1] == "  lineage root img-010af39c9f5c  synthetic_detail_added 1"
    child = latest_images(demo_workspace / "projects" / SLUG)[out[0].split()[0]]
    assert (child["root"], child["file"], child["disposition"]) == ("imports", "edits/01-edit.png", None)
    assert child["lineage"]["parent"] == "img-010af39c9f5c" and child["width"] == 8


def _sheet_rows(path: Path) -> list[dict]:
    with path.open(encoding="utf-8", newline="") as fh:
        return list(csv.DictReader(fh))


def _write_rows(path: Path, rows: list[dict]) -> None:
    with path.open("w", encoding="utf-8", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=list(rows[0]), lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)


def test_caption_sheet_skips_changed_rows_and_records_rejections(demo_workspace: Path, capsys, frozen_clock):
    project = demo_workspace / "projects" / SLUG
    code, out = run(["captions", "sheet", "--all"], capsys)
    assert code == 0 and out[0].endswith("(15 rows)"), out
    sheet = project / "sheets" / "captions-20300101-001.csv"
    rows = _sheet_rows(sheet)
    rows[0]["decision"] = "reject"
    rows[1]["decision"] = "approve"
    rows[1]["caption"] = rows[1]["draft"] + ", warm light"
    rows[2]["decision"] = "approve"
    rows[2]["draft"] = "an older draft"   # as if the caption changed after the sheet was written
    rows[3]["decision"] = "maybe"
    _write_rows(sheet, rows)
    code, out = run(["captions", "approve", "--sheet", str(sheet)], capsys)
    assert code == 0, out
    assert out[-2] == "1 rows approved, 1 rejected, 11 left blank, 2 skipped (sheet captions-20300101-001.csv)"
    assert any("changed since the sheet was written" in line for line in out)
    code, out = run(["captions", "apply"], capsys)
    assert out[-1] == "1 approved captions applied, 1 rejections recorded (0 sheet rows applied before)", out
    last = {line["image"]: line for line in read_jsonl(project / "captions.jsonl")}
    assert last[rows[0]["image"]]["status"] == "rejected"
    edited = last[rows[1]["image"]]
    assert (edited["status"], edited["basis"], edited["source"]) == ("approved", "manual", "operator")
    assert edited["sheet"] == f"projects/{SLUG}/sheets/captions-20300101-001.csv"
    code, out = run(["captions", "status"], capsys)
    assert "approved: 14" in out and "rejected: 1" in out
    code, out = run(["captions", "list", "--status", "rejected"], capsys)
    assert out[0].startswith(rows[0]["image"]) and out[-1] == "1 captions"
    assert_project_valid(project)


def test_caption_sheet_refuses_a_file_that_is_not_a_sheet(demo_workspace: Path, capsys, tmp_path: Path):
    bad = tmp_path / "notes.csv"
    bad.write_text("a,b\n1,2\n", encoding="utf-8")
    code, out = run(["captions", "approve", "--sheet", str(bad)], capsys)
    assert code == 2 and "not a caption sheet" in out[0]


def test_project_new_refusals_and_show(demo_workspace: Path, capsys):
    code, out = run(["project", "new", "Bad-Slug", "--type", "character-anime"], capsys)
    assert code == 2 and "lowercase letters" in out[0]
    code, out = run(["project", "new", "second", "--type", "concept"], capsys)
    assert code == 2 and "a type is one of" in out[0]
    code, out = run(["project", "new", "second", "--type", "style", "--family", "nosuchfamily"], capsys)
    assert code == 2 and "unknown family nosuchfamily" in out[0]
    code, out = run(["project", "new", "second", "--type", "style", "--family", "illustrious"], capsys)
    assert code == 0, out
    code, out = run(["project", "show"], capsys)
    assert code == 2 and out == ["this instance has 2 projects (demo_robot, second): pass --project <slug>"]
    code, out = run(["project", "show", "--project", SLUG], capsys)
    assert code == 0, out
    assert out[0] == f"project: {SLUG} (Demo Robot)"
    assert "  0.2.0  12 members (10 train, 2 holdout, from 0.1.0)" in out
    code, out = run(["status"], capsys)
    assert out.count("") == 1 and "project: second (Second)" in out
    code, out = run(["next", "--project", "second", "--stage"], capsys)
    assert out == ["00-new-project"]


@pytest.mark.parametrize("argv, line", [
    (["status", "--handoff"], "planned: T7b (status --handoff"),
])
def test_planned_actions_say_so(demo_workspace: Path, capsys, argv, line):
    from kiln.commands import status as status_cmd

    if argv[0] == "status" and status_cmd._handoff_available():
        pytest.skip("the handoff module exists (T7b)")
    code, out = run(argv, capsys)
    assert code == 2 and out[0].startswith(line), out


def test_status_handoff_is_wired_to_the_handoff_module(demo_workspace: Path, capsys):
    from kiln.commands import status as status_cmd

    if not status_cmd._handoff_available():
        pytest.skip("kiln.handoff (T7b) is not built")
    code, out = run(["status", "--handoff"], capsys)
    assert code == 0, out
    assert "stage: 06-split" in out and "next: kiln dataset census" in out
