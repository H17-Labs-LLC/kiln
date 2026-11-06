"""`kiln dataset split --holdout N --apply` after an easel import: every new kept image gets a side, and a
lineage (an imported render and its crop) is never split. The dry run writes nothing."""

from pathlib import Path

import pytest
from kiln_project_helpers import assert_project_valid, latest_images, read_jsonl, run
from kiln_source_helpers import RUN, easel_home, fixtures, strip_imports

from kiln.project import lineage


@pytest.fixture
def project(demo_workspace: Path, frozen_clock, tmp_path: Path, capsys) -> Path:
    """The demo with the easel run imported (3 kept renders) and a kept crop of render 1: 4 unassigned images."""
    folder = strip_imports(demo_workspace)
    home = easel_home(tmp_path)
    code, out = run(["images", "import", "--from-easel-run", RUN, "--keep-only", "--easel-home", str(home)], capsys)
    assert code == 0, out
    parent = next(i for i, r in latest_images(folder).items() if r["file"] == f"{RUN}/001.png")
    crop = folder / "images" / RUN / "001-crop.png"
    crop.write_bytes(fixtures().png_bytes(21))
    code, out = run(["images", "child", parent, str(crop), "--transform", "crop"], capsys)
    assert code == 0, out
    assert run(["images", "label", out[0].split()[0], "--disposition", "ACCEPT_TRAIN"], capsys)[0] == 0
    return folder


def _new(project: Path) -> dict[str, dict]:
    return {i: r for i, r in latest_images(project).items() if r["root"] == "imports"}


def test_split_apply_assigns_all_new_images_lineage_safe(project: Path, capsys):
    new = _new(project)
    assert len(new) == 4 and all(r["split"] == "unassigned" for r in new.values())
    held = sum(1 for r in latest_images(project).values() if r["split"] == "holdout")
    code, out = run(["dataset", "split", "--holdout", str(held + 2), "--apply"], capsys)
    assert code == 0, out
    assert out[1].startswith("  places 4 unassigned image(s)")
    assert out[-2] == "applied: 4 image(s) assigned"
    images = latest_images(project)
    after = {i: images[i] for i in new}
    assert all(r["split"] in ("train", "holdout") for r in after.values()), after
    by_root = lineage.groups(sorted(after), images)
    family = next(members for members in by_root.values() if len(members) == 2)
    assert len({images[i]["split"] for i in family}) == 1, "the render and its crop sit on one side"
    code, out = run(["dataset", "check", "--leaks"], capsys)
    assert code == 0 and out[-1] == "leaks: 0"
    code, out = run(["dataset", "census", "--split"], capsys)
    assert code == 0
    assert_project_valid(project)


def test_split_without_apply_writes_nothing(project: Path, capsys):
    lines = read_jsonl(project / "images.jsonl")
    code, out = run(["dataset", "split", "--holdout", "1"], capsys)
    assert code == 0
    assert out[-1] == "dry run: nothing written; add --apply after the operator's yes"
    assert read_jsonl(project / "images.jsonl") == lines


def test_the_split_follows_the_imported_verdicts_only(project: Path, capsys):
    """A rejected render never gets a side: only kept images are placed."""
    rejected = next(i for i, r in _new(project).items() if r["file"] == f"{RUN}/002.png")
    assert run(["images", "label", rejected, "--disposition", "REJECT"], capsys)[0] == 0
    code, out = run(["dataset", "split", "--holdout", "0", "--apply"], capsys)
    assert code == 0, out
    images = latest_images(project)
    assert images[rejected]["split"] == "unassigned"
    assert all(images[i]["split"] == "train" for i in _new(project) if i != rejected)
