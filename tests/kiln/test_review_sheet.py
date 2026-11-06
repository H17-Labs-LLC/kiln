"""`kiln review sheet` -> the operator fills it -> `kiln review apply --sheet`: verdicts, checks and notes come back
as the operator's, and nothing else changes. The test plays the operator."""

import csv
from pathlib import Path

import pytest
from kiln_project_helpers import assert_project_valid, latest_images, read_jsonl, run
from kiln_source_helpers import fixtures, strip_imports

CHECK_COLUMNS = ["check:face", "check:head", "check:colours", "check:body", "check:feature", "check:hands"]


@pytest.fixture
def project(demo_workspace: Path, frozen_clock, tmp_path: Path, capsys) -> Path:
    """The demo (no easel imports) with three new untriaged images from a folder."""
    folder = strip_imports(demo_workspace)
    batch = tmp_path / "batch"
    batch.mkdir()
    for name, n in (("a.png", 21), ("b.png", 22), ("c.png", 23)):
        (batch / name).write_bytes(fixtures().png_bytes(n))
    code, out = run(["images", "import", "--from-folder", str(batch)], capsys)
    assert code == 0, out
    assert out[-2] == "3 images registered from batch (untriaged); 0 already registered, 0 problem(s)"
    return folder


def _ids(project: Path) -> dict[str, str]:
    return {r["file"]: i for i, r in latest_images(project).items() if r["file"].startswith("batch/")}


def _sheet(capsys, *extra: str) -> Path:
    code, out = run(["review", "sheet", *extra], capsys)
    assert code == 0, out
    assert out[0].startswith("review sheet: projects/demo_robot/sheets/review-20300101-")
    return Path(out[0].split()[2])


def _read(path: Path) -> tuple[list[str], list[dict]]:
    with path.open(encoding="utf-8", newline="") as fh:
        reader = csv.DictReader(fh)
        return list(reader.fieldnames or []), list(reader)


def _write(path: Path, columns: list[str], rows: list[dict]) -> None:
    with path.open("w", encoding="utf-8", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=columns, lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)


def test_the_sheet_round_trip_records_the_operators_verdicts(project: Path, demo_workspace: Path, capsys):
    rel = _sheet(capsys)
    sheet = demo_workspace / rel
    columns, rows = _read(sheet)
    assert columns[:10] == ["image", "root", "file", "source", "tier", "disposition", "split", "viewpoint",
                            "framing", "labels"]
    assert columns[10:] == [*CHECK_COLUMNS, "verdict", "note"]
    ids = _ids(project)
    assert sorted(r["image"] for r in rows) == sorted(ids.values()), "only the images without a verdict"
    answers = {"batch/a.png": ("train", "pass", "front", "standing by a window"),
               "batch/b.png": ("REJECT", "fail", "", ""),
               "batch/c.png": ("reference", "", "", "")}
    for row in rows:
        verdict, face, viewpoint, note = answers[row["file"]]
        row.update({"verdict": verdict, "check:face": face, "viewpoint": viewpoint, "note": note})
        if row["file"] == "batch/a.png":
            row["check:hands"] = "na"
    _write(sheet, columns, rows)
    code, out = run(["review", "apply", "--sheet", str(sheet)], capsys)
    assert code == 0, out
    assert out[0] == ("3 verdicts recorded (1 ACCEPT_REFERENCE_ONLY, 1 ACCEPT_TRAIN, 1 REJECT), 0 other changes, "
                      f"0 unchanged, 0 left blank, 0 skipped (sheet {sheet.name})")
    now = latest_images(project)
    a, b, c = (now[ids[f"batch/{x}.png"]] for x in "abc")
    assert a["disposition"] == "ACCEPT_TRAIN" and a["verdict_source"] == "operator"
    assert a["labels"] == ["check-na:hands", "check:face"], "na is recorded as check-na:<key>"
    assert a["viewpoint"] == "front" and a["notes"] == "standing by a window"
    assert b["disposition"] == "REJECT" and b["tier"] == "REJECTED" and b["labels"] == ["check-fail:face"]
    assert c["disposition"] == "ACCEPT_REFERENCE_ONLY" and c["split"] == "reference_only"
    code, out = run(["images", "list", "--untriaged"], capsys)
    assert not any("batch/" in line for line in out)
    assert_project_valid(project)
    # applying the same sheet again changes nothing
    lines = read_jsonl(project / "images.jsonl")
    code, out = run(["review", "apply", "--sheet", str(sheet)], capsys)
    assert code == 0
    assert out[0].startswith("0 verdicts recorded, 0 other changes, 3 unchanged")
    assert read_jsonl(project / "images.jsonl") == lines


def test_blank_rows_change_nothing(project: Path, demo_workspace: Path, capsys):
    sheet = demo_workspace / _sheet(capsys)
    lines = read_jsonl(project / "images.jsonl")
    code, out = run(["review", "apply", "--sheet", str(sheet)], capsys)
    assert code == 0
    assert out[0] == f"0 verdicts recorded, 0 other changes, 0 unchanged, 3 left blank, 0 skipped (sheet {sheet.name})"
    assert read_jsonl(project / "images.jsonl") == lines


def test_a_stale_row_and_an_unknown_word_are_skipped(project: Path, demo_workspace: Path, capsys):
    sheet = demo_workspace / _sheet(capsys)
    columns, rows = _read(sheet)
    ids = _ids(project)
    assert run(["images", "label", ids["batch/a.png"], "--disposition", "REJECT"], capsys)[0] == 0
    for row in rows:
        row["verdict"] = {"batch/a.png": "train", "batch/b.png": "maybe", "batch/c.png": "hold"}[row["file"]]
    _write(sheet, columns, rows)
    code, out = run(["review", "apply", "--sheet", str(sheet)], capsys)
    assert code == 1
    assert f"skipped: {ids['batch/a.png']}: its verdict changed since the sheet was written (now REJECT); " \
           "write a fresh sheet" in out
    assert any(line.startswith(f"skipped: {ids['batch/b.png']}: verdict 'maybe' is not one of") for line in out)
    now = latest_images(project)
    assert now[ids["batch/a.png"]]["disposition"] == "REJECT"
    assert now[ids["batch/b.png"]]["disposition"] is None
    assert now[ids["batch/c.png"]]["disposition"] == "HOLD"


def test_all_lists_every_image_and_prefills_the_checks(project: Path, demo_workspace: Path, tmp_path: Path,
                                                        capsys):
    out_file = tmp_path / "every.csv"
    code, out = run(["review", "sheet", "--all", "--out", str(out_file)], capsys)
    assert code == 0, out
    _, rows = _read(out_file)
    assert len(rows) == len(latest_images(project))
    assert all(row["verdict"] == "" for row in rows)


def test_review_sheet_feeds_next(project: Path, demo_workspace: Path, capsys):
    """While images wait for a verdict, `next` stays on 03; after the sheet it moves on (no goal: legacy rows count)."""
    assert run(["project", "goal", "clear"], capsys)[0] == 0
    code, out = run(["next", "--stage"], capsys)
    assert out == ["03-dataset-images"]
    sheet = demo_workspace / _sheet(capsys)
    columns, rows = _read(sheet)
    for row in rows:
        row["verdict"] = "reject"
    _write(sheet, columns, rows)
    assert run(["review", "apply", "--sheet", str(sheet)], capsys)[0] == 0
    code, out = run(["next", "--stage"], capsys)
    assert out != ["03-dataset-images"]


def test_readout_is_planned(project: Path, capsys):
    code, out = run(["review", "readout"], capsys)
    assert code == 2 and out == ["planned: session-2 (review readout: the eval scores per checkpoint, each judge apart)"]
