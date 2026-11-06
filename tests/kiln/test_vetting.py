"""Stage 04 (vetting): a kept image in no frozen version waits until every checklist item that applies to it has a
verdict (pass, fail or na); `kiln next` routes there on a native project without a goal, `kiln review sheet` lists
those images, and `review apply` records the checks. Also: a source registers viewpoint and framing in one line."""

import csv
from pathlib import Path

import pytest
from kiln_project_helpers import SLUG, latest_images, read_jsonl, run, three
from kiln_source_helpers import fixtures

from kiln import review_sheet
from kiln.project import images as images_mod
from kiln.project import model


@pytest.fixture
def native(demo_workspace: Path, frozen_clock, capsys) -> Path:
    """The demo without its goal, the one legacy row rejected: next derives the stage from the files."""
    assert run(["project", "goal", "clear"], capsys)[0] == 0
    assert run(["images", "label", "img-27daf07ca88f", "--disposition", "REJECT"], capsys)[0] == 0
    return demo_workspace / "projects" / SLUG


def _project() -> model.Project:
    return model.Instance.load().project(SLUG)


def _register_back_close(project_dir: Path) -> str:
    dest = project_dir / "images" / "extra" / "back-close.png"
    dest.parent.mkdir(parents=True)
    dest.write_bytes(fixtures().png_bytes(24))
    record, created = images_mod.register(_project(), dest, source={"kind": "upload", "tool": "test"},
                                          by="kiln images import", event="import", viewpoint="back", framing="close")
    assert created
    return record["id"]


def test_register_takes_viewpoint_and_framing_in_one_line(native: Path):
    before = len(read_jsonl(native / "images.jsonl"))
    image_id = _register_back_close(native)
    new = read_jsonl(native / "images.jsonl")[before:]
    assert len(new) == 1 and new[0]["id"] == image_id
    assert (new[0]["viewpoint"], new[0]["framing"]) == ("back", "close")
    with pytest.raises(model.ProjectError) as caught:
        images_mod.register(_project(), native / "images" / "extra" / "back-close.png", source={"kind": "upload"},
                            by="test", viewpoint="sideways")
    assert caught.value.lines[0].startswith("a viewpoint is one of front, ")


def test_easel_imports_count_as_vetted(native: Path, capsys):
    """The demo's three imports carry pass checks and check-na:hands from the easel review: no stage 04."""
    project = _project()
    assert review_sheet.unvetted(project, images_mod.records(project)) == []
    code, out = run(["next", "--stage"], capsys)
    assert out == ["06-split"], out


def test_a_kept_image_without_its_checks_routes_to_04_and_the_sheet_settles_it(native: Path, demo_workspace: Path,
                                                                               capsys):
    image_id = _register_back_close(native)
    assert run(["images", "label", image_id, "--disposition", "ACCEPT_TRAIN"], capsys)[0] == 0
    project = _project()
    record = images_mod.records(project)[image_id]
    items = review_sheet.checklist_items(project)
    assert review_sheet.missing_checks(record, items) == ["head", "colours"], "skip_when drops the rest"
    code, out = run(["next"], capsys)
    assert out == three("04-vetting", "kiln review sheet"), out

    code, out = run(["review", "sheet"], capsys)
    assert code == 0 and out[0].endswith("(1 rows)"), out
    sheet = demo_workspace / out[0].split()[2]
    with sheet.open(encoding="utf-8", newline="") as fh:
        reader = csv.DictReader(fh)
        columns, rows = list(reader.fieldnames or []), list(reader)
    assert [r["image"] for r in rows] == [image_id]
    rows[0].update({"check:head": "pass", "check:colours": "na"})
    with sheet.open("w", encoding="utf-8", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=columns, lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)
    code, out = run(["review", "apply", "--sheet", str(sheet)], capsys)
    assert code == 0 and out[0].startswith("0 verdicts recorded, 1 other changes"), out
    assert latest_images(native)[image_id]["labels"] == ["check-na:colours", "check:head"]
    code, out = run(["next", "--stage"], capsys)
    assert out == ["05-captions"], "vetted; its caption is next"


def test_a_frozen_member_is_never_sent_back_to_vetting(native: Path):
    """The demo's 0.1.0 and 0.2.0 members carry no check labels: frozen versions passed their gate already."""
    project = _project()
    images = images_mod.records(project)
    old = [i for i, r in images.items() if r["root"] == "images" and images_mod.is_kept(r)]
    assert old and not any(f"check:{k}" in images[i]["labels"] for i in old for k in ("head", "colours"))
    assert not set(old) & set(review_sheet.unvetted(project, images))
