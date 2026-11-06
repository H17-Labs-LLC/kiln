"""The trial path end to end: `project new` -> intake -> images -> verdicts -> captions -> split -> freeze ->
materialise, with `kiln next` printing the right three lines at each state.

The test plays the operator (intake answers, verdicts, the caption sheet) and stands in for an image source
(images are registered through `kiln.project.images.register`, which the T8 sources call).
"""

import csv
import importlib.util
import shutil
from pathlib import Path

import pytest
import yaml
from kiln_project_helpers import REPO, SLUG, assert_project_valid, latest_images, next_lines, run, three

from kiln.project import images as images_mod
from kiln.project import model

CONFIG = 'schema = "kiln/config/1"\n\n[paths]\nroot = "."\nprojects = "projects"\njournal = "journal"\n'


@pytest.fixture
def instance(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, frozen_clock) -> Path:
    home = tmp_path / "home"
    home.mkdir()
    (home / "kiln.toml").write_text(CONFIG, encoding="utf-8")
    monkeypatch.setenv("KILN_HOME", str(home))
    return home


@pytest.fixture
def pngs(tmp_path: Path) -> list[Path]:
    """The 24 deterministic 8x8 fixture PNGs, generated into tmp_path."""
    spec = importlib.util.spec_from_file_location("make_fixtures", REPO / "tests" / "fixtures" / "make_fixtures.py")
    fixtures = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(fixtures)
    made = fixtures.generate(tmp_path / "_gen")
    return [made[f"png/{fixtures.png_name(n)}"] for n in range(1, 25)]


def _answer_intake(project: Path, capsys) -> None:
    """The operator dictates each answer (here: each question's example) and the agent records it verbatim."""
    data = yaml.safe_load((project / "intake.yaml").read_text(encoding="utf-8"))
    for q in data["questions"]:
        words = q["example"] if isinstance(q["example"], list) else [str(q["example"])]
        code, out = run(["project", "intake", "answer", q["id"], *map(str, words)], capsys)
        assert code == 0 and out[0].startswith(f"intake {q['n']} {q['id']}: answered ("), out


def _fill_checks(sheet: Path) -> int:
    """The operator vets every row of a review sheet: each checklist item passes."""
    with sheet.open(encoding="utf-8", newline="") as fh:
        reader = csv.DictReader(fh)
        columns, rows = list(reader.fieldnames or []), list(reader)
    for row in rows:
        row.update({c: "pass" for c in columns if c.startswith("check:")})
    with sheet.open("w", encoding="utf-8", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=columns, lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)
    return len(rows)


def _fill_sheet(sheet: Path) -> int:
    """The operator writes a caption and `approve` into every row."""
    with sheet.open(encoding="utf-8", newline="") as fh:
        rows = list(csv.DictReader(fh))
        columns = list(rows[0]) if rows else []
    for n, row in enumerate(rows, 1):
        row["caption"] = f"a robot, view {n}, standing in a plain studio, clean anime illustration"
        row["decision"] = "approve"
    with sheet.open("w", encoding="utf-8", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=columns, lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)
    return len(rows)


def test_project_new_to_materialised_snapshot(instance: Path, pngs: list[Path], capsys, monkeypatch):
    # an instance without a project
    assert next_lines(capsys) == three("00-new-project", "kiln project new <slug> --type character-anime")

    # 00: the project exists, the intake is not answered
    code, out = run(["project", "new", SLUG, "--type", "character-anime"], capsys)
    assert code == 0, out
    assert out[0] == f"created project {SLUG} (character-anime) in projects/{SLUG}"
    project_dir = instance / "projects" / SLUG
    for name in ("intake.yaml", "caption_rules.yaml", "checklist.yaml", "eval_suite.yaml", "dataset.toml",
                 "thresholds.yaml", "card.md.tmpl", "spec.json", "project.yaml", "state.json"):
        assert (project_dir / name).is_file(), name
    assert next_lines(capsys) == three("00-new-project", "kiln project intake show")
    assert next_lines(capsys, "--stage") == ["00-new-project"]
    code, out = run(["project", "lint"], capsys)
    assert code == 1 and out[-1].startswith(f"lint {SLUG}: 5 problems"), out
    code, out = run(["project", "intake", "show"], capsys)
    assert code == 0 and out[-2] == f"intake {SLUG}: 0 of 14 answered, 5 required still missing", out
    code, out = run(["project", "new", SLUG, "--type", "character-anime"], capsys)
    assert code == 2 and out[0].startswith(f"REFUSED: project {SLUG} exists"), out

    # 01: the intake is answered (dictated, recorded verbatim), no image yet
    _answer_intake(project_dir, capsys)
    code, out = run(["project", "lint"], capsys)
    assert code == 0, out
    intake = yaml.safe_load((project_dir / "intake.yaml").read_text(encoding="utf-8"))
    assert all(q["answer"] == q["example"] for q in intake["questions"])
    assert all(q["note"].endswith("[answer dictated by operator, 2030-01-01T00:00:00+00:00]")
               for q in intake["questions"])
    assert (project_dir / "intake.yaml").read_text(encoding="utf-8").startswith("# Intake for a character-anime")
    assert next_lines(capsys) == three("01-probe-iterations", "kiln source run <jobs.json> --dry-run")

    # 03: images registered by a source, no verdict yet
    project = model.Instance.load().project()
    batch = project_dir / "images" / "batch-01"
    batch.mkdir(parents=True)
    ids = []
    for n in range(1, 7):
        dest = batch / f"{n:03d}.png"
        shutil.copyfile(pngs[n - 1], dest)
        record, created = images_mod.register(project, dest, source={"kind": "local-gen", "tool": "test"},
                                              by="kiln images import", event="import")
        assert created
        ids.append(record["id"])
    crop = batch / "003-crop.png"
    shutil.copyfile(pngs[10], crop)
    code, out = run(["images", "child", ids[2], str(crop), "--transform", "crop", "--grid-cell", "0,0,4,4"], capsys)
    assert code == 0, out
    crop_id = out[0].split()[0]
    assert f"lineage root {ids[2]}" in out[1]
    code, out = run(["images", "child", ids[2], str(crop), "--transform", "crop"], capsys)
    assert code == 0 and out[0].startswith("already registered"), out
    assert next_lines(capsys) == three("03-dataset-images", "kiln images list")

    # 04: the operator's verdicts are in; the kept images still lack their checklist verdicts
    for image_id in [*ids[:5], crop_id]:
        code, out = run(["images", "label", image_id, "--disposition", "ACCEPT_TRAIN", "--label", "lighting:day"],
                        capsys)
        assert code == 0, out
    code, out = run(["images", "label", ids[5], "--disposition", "REJECT", "--note", "the colours are wrong"], capsys)
    assert code == 0, out
    code, out = run(["images", "assign", ids[5], "--split", "train"], capsys)
    assert code == 2 and "rejected" in out[0], out
    assert next_lines(capsys) == three("04-vetting", "kiln review sheet")
    sheet = project_dir / "sheets" / "review-20300101-001.csv"
    code, out = run(["review", "sheet"], capsys)
    assert code == 0 and out[0] == f"review sheet: projects/{SLUG}/sheets/{sheet.name} (6 rows)", out
    assert _fill_checks(sheet) == 6
    code, out = run(["review", "apply", "--sheet", str(sheet)], capsys)
    assert code == 0 and out[0].startswith("0 verdicts recorded, 6 other changes"), out

    # 05: vetted; no caption yet
    assert next_lines(capsys) == three("05-captions", "kiln captions status")

    # the caption sheet round trip: sheet -> the operator approves -> approve --sheet -> apply
    code, out = run(["captions", "sheet"], capsys)
    assert code == 0, out
    assert out[0] == f"caption sheet: projects/{SLUG}/sheets/captions-20300101-001.csv (6 rows)"
    sheet = project_dir / "sheets" / "captions-20300101-001.csv"
    assert _fill_sheet(sheet) == 6
    code, out = run(["captions", "approve", "--sheet", str(sheet)], capsys)
    assert code == 0, out
    assert "6 rows approved, 0 rejected, 0 left blank, 0 skipped" in out[-2], out
    code, out = run(["captions", "status"], capsys)
    assert "approved in a sheet, not applied: 6" in out, out
    assert next_lines(capsys) == three("05-captions", "kiln captions status")
    code, out = run(["captions", "apply"], capsys)
    assert code == 0 and out[-1].startswith("6 approved captions applied"), out
    code, out = run(["captions", "apply"], capsys)
    assert out[-1] == "0 approved captions applied, 0 rejections recorded (6 sheet rows applied before)", out
    code, out = run(["captions", "status"], capsys)
    assert "approved: 6" in out, out

    # 06: captioned, not split
    assert next_lines(capsys) == three("06-split", "kiln dataset census")
    code, out = run(["dataset", "split", "--holdout", "1"], capsys)
    assert code == 0 and out[0].startswith(f"split proposal for {SLUG}: 5 train, 1 holdout (as asked"), out
    assert next_lines(capsys)[0] == "stage: 06-split"  # the dry run wrote nothing
    code, out = run(["dataset", "split", "--holdout", "1", "--apply"], capsys)
    assert code == 0 and "applied: 6 image(s) assigned" in out, out
    latest = latest_images(project_dir)
    assert latest[crop_id]["split"] == latest[ids[2]]["split"], "a lineage was split"

    # 07: split, not frozen
    assert next_lines(capsys) == three("07-freeze-and-gate", "kiln dataset check --leaks")
    code, out = run(["dataset", "check", "--leaks"], capsys)
    assert (code, out) == (0, ["leaks: 0"])
    code, out = run(["dataset", "freeze", "0.1.0"], capsys)
    assert code == 0, out
    assert out[0] == f"frozen {SLUG}/0.1.0: 6 members (6 new)"
    assert out[1].startswith("  mode: native project")

    # 07: frozen, not materialised
    assert next_lines(capsys) == three("07-freeze-and-gate", "kiln dataset materialize 0.1.0")
    code, out = run(["dataset", "check"], capsys)
    assert code == 1 and "not materialised" in out[-1], out
    code, out = run(["dataset", "materialize", "0.1.0"], capsys)
    assert code == 0, out
    assert out[1] == f"  train    5 images + captions -> projects/{SLUG}/datasets/0.1.0/training"
    assert out[2] == f"  holdout  1 images + captions -> projects/{SLUG}/datasets/0.1.0/holdout"

    # 08: frozen and materialised with matching counts
    assert next_lines(capsys) == three("08-train", "kiln runs list --family illustrious --status ok")
    code, out = run(["dataset", "check"], capsys)
    assert code == 0 and out[-1] == f"{SLUG}/0.1.0: 5 train, 1 holdout; captions 6/6; matches the snapshot", out

    # every record the walk wrote follows its frozen schema
    assert_project_valid(project_dir)
    code, out = run(["project", "lint"], capsys)
    assert code == 0, out


def test_next_without_an_instance(tmp_path: Path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))
    assert next_lines(capsys) == three("00-new-project", "kiln init ~/kiln-home")
    from kiln import planned

    task = planned.verb_task("init")
    if task:  # while init is planned, the line says so
        assert next_lines(capsys)[2] == f"next: kiln init ~/kiln-home  # planned: {task}"


def test_next_json_and_stage(demo_workspace: Path, capsys):
    import json

    code, out = run(["next", "--json"], capsys)
    data = json.loads(out[0])
    assert (data["stage"], data["read"], data["project"]) == ("06-split", "docs/runbooks/06-split.md", SLUG)
    assert "split" in data["why"]
    assert next_lines(capsys, "--stage") == ["06-split"]
