"""`kiln captions propose [--store] [--all]`: drafts from each kept image's source prompt (or the operator's note),
never from looking at the image; `--store` keeps only the clean ones, never over an approved caption."""

from pathlib import Path

import pytest
from kiln_project_helpers import assert_project_valid, latest_images, read_jsonl, run
from kiln_source_helpers import RUN, easel_home, fixtures, strip_imports

DRAFT = "dmrbt, robot, standing in a tidy workshop, soft daylight, full body, clean anime illustration"


@pytest.fixture
def project(demo_workspace: Path, frozen_clock) -> Path:
    return strip_imports(demo_workspace)


def _import(capsys, tmp_path: Path, **kw) -> None:
    home = easel_home(tmp_path, **kw)
    code, out = run(["images", "import", "--from-easel-run", RUN, "--keep-only", "--easel-home", str(home)], capsys)
    assert code == 0, out


def _imported(project: Path) -> dict[str, dict]:
    return {i: r for i, r in latest_images(project).items() if r["root"] == "imports"}


def test_propose_prints_drafts_and_writes_nothing(project: Path, tmp_path: Path, capsys):
    _import(capsys, tmp_path)
    before = (project / "captions.jsonl").read_bytes()
    code, out = run(["captions", "propose"], capsys)
    assert code == 0, out
    for image_id in _imported(project):
        assert f"{image_id}  prompt  14w  {DRAFT}" in out
    assert "3 image(s) proposed: from a prompt 3, from a note 0, nothing to go on 0; 3 clean" in out
    assert "  left alone: 12 approved" in out
    assert out[-1] == "dry run: nothing written; add --store to keep the clean ones as drafts"
    assert (project / "captions.jsonl").read_bytes() == before


def test_store_keeps_prompt_drafts_once(project: Path, tmp_path: Path, capsys):
    _import(capsys, tmp_path)
    before = read_jsonl(project / "captions.jsonl")
    code, out = run(["captions", "propose", "--store"], capsys)
    assert code == 0, out
    assert "3 image(s) proposed: from a prompt 3, from a note 0, nothing to go on 0; 3 clean, 3 stored as drafts" in out
    new = read_jsonl(project / "captions.jsonl")[len(before):]
    assert sorted(line["image"] for line in new) == sorted(_imported(project))
    for line in new:
        assert line["status"] == "draft" and line["basis"] == "prompt"
        assert line["source"] == "kiln:captions propose" and line["by"] == "kiln captions propose --store"
        assert line["text"] == DRAFT and line["word_count"] == 14 and line["lint"] == []
    assert_project_valid(project)
    # a draft waits for the operator: no second draft, with or without --all (the text is the same)
    code, out = run(["captions", "propose", "--store"], capsys)
    assert "0 image(s) proposed" in out[0] and "3 with a draft (--all redrafts them)" in out[-2]
    code, out = run(["captions", "propose", "--store", "--all"], capsys)
    assert code == 0
    assert len(read_jsonl(project / "captions.jsonl")) == len(before) + 3
    code, out = run(["captions", "status"], capsys)
    assert "draft: 3" in out


def test_an_approved_caption_is_never_redrafted(project: Path, capsys):
    before = read_jsonl(project / "captions.jsonl")
    code, out = run(["captions", "propose", "--store", "--all"], capsys)
    assert code == 0, out
    assert out[0].startswith("0 image(s) proposed") and "left alone: 12 approved" in out[1]
    assert read_jsonl(project / "captions.jsonl") == before


def test_an_age_word_in_the_prompt_is_never_stored(project: Path, tmp_path: Path, capsys):
    _import(capsys, tmp_path, prompt="dmrbt, robot, a young robot standing in a workshop, full body")
    before = read_jsonl(project / "captions.jsonl")
    code, out = run(["captions", "propose", "--store"], capsys)
    assert code == 0
    assert any(line.strip() == "problem: age word 'young'" for line in out)
    assert "0 stored as drafts" in "\n".join(out)
    assert read_jsonl(project / "captions.jsonl") == before


def test_instructions_and_the_fixed_design_stay_out(project: Path, tmp_path: Path, capsys):
    prompt = ("dmrbt, robot, sitting on a crate, keep the pose of Image A, no hat, the visor glowing, "
              "evening light, character sheet")
    _import(capsys, tmp_path, prompt=prompt)
    code, out = run(["captions", "propose"], capsys)
    assert code == 0
    assert any(line.endswith("dmrbt, robot, sitting on a crate, evening light, clean anime illustration")
               for line in out), out


def test_a_crop_inherits_its_parents_prompt_and_waits_for_a_check(project: Path, tmp_path: Path, capsys):
    _import(capsys, tmp_path)
    parent = next(i for i, r in _imported(project).items() if r["file"].endswith("001.png"))
    crop = project / "images" / RUN / "001-crop.png"
    crop.write_bytes(fixtures().png_bytes(21))
    code, out = run(["images", "child", parent, str(crop), "--transform", "crop"], capsys)
    assert code == 0, out
    child = out[0].split()[0]
    assert run(["images", "label", child, "--disposition", "ACCEPT_TRAIN"], capsys)[0] == 0
    code, out = run(["captions", "propose", "--store"], capsys)
    assert f"{child}  prompt  14w  {DRAFT}" in out
    assert any(f"derived since its prompt ({child} is a crop of {parent})" in line for line in out)
    stored = {line["image"] for line in read_jsonl(project / "captions.jsonl") if line["status"] == "draft"}
    assert child not in stored and parent in stored


def test_a_note_is_the_basis_when_there_is_no_prompt(project: Path, tmp_path: Path, capsys):
    folder = tmp_path / "batch"
    folder.mkdir()
    (folder / "a.png").write_bytes(fixtures().png_bytes(22))
    (folder / "b.png").write_bytes(fixtures().png_bytes(23))
    (tmp_path / "notes.csv").write_text("file,note\na.png,\"sitting on a crate, waving\"\n", encoding="utf-8")
    code, out = run(["images", "import", "--from-folder", str(folder), "--notes", str(tmp_path / "notes.csv")], capsys)
    assert code == 0, out
    ids = {r["file"]: i for i, r in latest_images(project).items() if r["file"].startswith("batch/")}
    for image_id in ids.values():
        assert run(["images", "label", image_id, "--disposition", "ACCEPT_TRAIN"], capsys)[0] == 0
    code, out = run(["captions", "propose", "--store"], capsys)
    assert code == 0
    assert f"{ids['batch/a.png']}  note    10w  dmrbt, robot, sitting on a crate, waving, clean anime illustration  " \
           "[stored]" in out
    assert f"{ids['batch/b.png']}  none     0w  (nothing to go on)" in out
    by_image = {line["image"]: line for line in read_jsonl(project / "captions.jsonl")}
    assert by_image[ids["batch/a.png"]]["basis"] == "manual"
    assert ids["batch/b.png"] not in by_image
