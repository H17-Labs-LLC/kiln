"""`kiln project goal set|clear` and how `kiln next` routes by it.

The case the trial needs: an adopted project whose exported legacy rows were never triaged. Without a goal, `next`
says 90-adopt-existing; with one, it ignores those rows and walks 03 -> 05 -> 06 -> 07 -> 08 for the goal.
"""

import json
import shutil
from pathlib import Path

import pytest
import yaml
from kiln_project_helpers import SLUG, assert_project_valid, next_lines, read_jsonl, run, three

from kiln.project import images as images_mod
from kiln.project import model

NEW_RUN = "run-20300201-001"


def _adopt(home: Path) -> Path:
    """Make the demo an adopted project: adopted: true, no goal."""
    project = home / "projects" / SLUG
    meta = yaml.safe_load((project / "project.yaml").read_text(encoding="utf-8"))
    meta["adopted"] = True
    (project / "project.yaml").write_text(yaml.safe_dump(meta, sort_keys=False), encoding="utf-8")
    state = json.loads((project / "state.json").read_text(encoding="utf-8"))
    state["goal"] = None
    (project / "state.json").write_text(json.dumps(state, indent=2), encoding="utf-8")
    return project


def _untriaged_legacy(project: Path) -> list[str]:
    return [i for i, r in {line["id"]: line for line in read_jsonl(project / "images.jsonl")}.items()
            if r.get("legacy_untriaged") and r.get("disposition") is None]


def test_adopted_project_with_untriaged_legacy_rows_and_a_goal_routes_to_03(demo_workspace: Path, capsys, frozen_clock):
    project_dir = _adopt(demo_workspace)
    assert _untriaged_legacy(project_dir) == ["img-27daf07ca88f"]
    assert next_lines(capsys) == three("90-adopt-existing", "kiln project show")

    code, out = run(["project", "goal", "set", "--new-version", "0.3.0", "--from-easel-run", NEW_RUN], capsys)
    assert code == 0, out
    assert out[0] == f"goal set for {SLUG}: new version 0.3.0 from 0.2.0 (easel run {NEW_RUN})"
    state = json.loads((project_dir / "state.json").read_text(encoding="utf-8"))
    assert state["goal"] == {"kind": "new-version", "new_version": "0.3.0", "from_version": "0.2.0",
                             "from_easel_run": NEW_RUN, "set_at": "2030-01-01T00:00:00+00:00", "note": None}
    # the goal's run is not imported yet: 03, and the legacy row does not count
    assert next_lines(capsys) == three("03-dataset-images", f"kiln images import --from-easel-run {NEW_RUN} --keep-only")

    # the import lands (as T8's easel-run source writes it): one kept render, no caption yet
    project = model.Instance.load().project()
    dest = project_dir / "images" / NEW_RUN / "001.png"
    dest.parent.mkdir(parents=True)
    shutil.copyfile(project_dir / "images" / "run-20300101-001" / "001.png", dest)
    dest.write_bytes(dest.read_bytes() + b"\x00")  # other bytes, another id
    record, created = images_mod.register(
        project, dest, by="kiln images import", event="import",
        source={"kind": "generated", "tool": "easel", "run": NEW_RUN, "n": 1},
        disposition="ACCEPT_TRAIN", verdict_source=f"easel-review:{NEW_RUN}")
    assert created
    assert next_lines(capsys) == three("05-captions", "kiln captions status")

    # its caption is approved (through the sheet) -> 06, then split -> 07, freeze -> 07, materialise -> 08
    code, out = run(["captions", "sheet"], capsys)
    sheet = project_dir / "sheets" / "captions-20300101-001.csv"
    text = sheet.read_text(encoding="utf-8").replace(",none,,,,,", ",none,,,dmrbt a robot in a workshop,approve,")
    sheet.write_text(text, encoding="utf-8")
    code, out = run(["captions", "approve", "--sheet", str(sheet)], capsys)
    assert out[-2].startswith("1 rows approved"), out
    code, out = run(["captions", "apply"], capsys)
    assert next_lines(capsys) == three("06-split", "kiln dataset census")
    code, out = run(["dataset", "split", "--holdout", "2", "--apply"], capsys)
    assert code == 0, out
    assert next_lines(capsys) == three("07-freeze-and-gate", "kiln dataset check --leaks")
    code, out = run(["dataset", "freeze", "0.3.0"], capsys)
    assert code == 0, out
    assert out[0] == f"frozen {SLUG}/0.3.0: 16 members (12 from 0.2.0 + 4 new)"
    assert next_lines(capsys) == three("07-freeze-and-gate", "kiln dataset materialize 0.3.0")
    code, out = run(["dataset", "materialize", "0.3.0"], capsys)
    assert code == 0, out
    assert next_lines(capsys) == three("08-train", "kiln runs list --family illustrious --status ok")
    assert _untriaged_legacy(project_dir) == ["img-27daf07ca88f"]  # still untriaged, still ignored

    # clearing the goal returns the adopted project to 90
    code, out = run(["project", "goal", "clear"], capsys)
    assert code == 0 and out[0].startswith(f"goal cleared for {SLUG} (was: new version 0.3.0"), out
    assert next_lines(capsys) == three("90-adopt-existing", "kiln project show")
    assert_project_valid(project_dir)


def test_without_a_goal_an_untriaged_image_holds_a_native_project_at_03(demo_workspace: Path, capsys):
    code, out = run(["project", "goal", "clear"], capsys)
    assert code == 0, out
    assert next_lines(capsys) == three("03-dataset-images", "kiln images list")


def test_goal_refusals(demo_workspace: Path, capsys):
    code, out = run(["project", "goal", "set", "--new-version", "0.2.0"], capsys)
    assert code == 2 and out == ["REFUSED: version 0.2.0 is already frozen; a goal names a new version"]
    code, out = run(["project", "goal", "set", "--new-version", "0.3", "--from-easel-run", NEW_RUN], capsys)
    assert code == 2 and "three numbers" in out[0]
    code, out = run(["project", "goal", "set", "--new-version", "0.3.0", "--from-version", "0.9.0"], capsys)
    assert code == 2 and out[0].startswith(f"no snapshot {SLUG}/0.9.0"), out
    code, out = run(["project", "goal", "set", "--new-version", "0.3.0", "--from-easel-run", "RUN-1"], capsys)
    assert code == 2 and "easel run id" in out[0]


@pytest.mark.parametrize("flags, members", [([], 15), (["--from", "0.2.0"], 15), (["--only-new"], 3),
                                            (["--from", "0.1.0"], 10)])
def test_goal_sets_the_freeze_default(demo_workspace: Path, capsys, flags, members):
    code, out = run(["dataset", "freeze", "0.3.0", *flags], capsys)
    assert code == 0, out
    assert out[0].startswith(f"frozen {SLUG}/0.3.0: {members} members")


def test_adopted_project_freezes_from_the_latest_snapshot_by_default(demo_workspace: Path, capsys):
    _adopt(demo_workspace)
    code, out = run(["dataset", "freeze", "0.3.0"], capsys)
    assert code == 0, out
    assert out[0] == f"frozen {SLUG}/0.3.0: 15 members (12 from 0.2.0 + 3 new)"
    assert out[1] == "  mode: adopted project: --from the latest snapshot 0.2.0"
