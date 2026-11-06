"""`kiln images import --from-easel-run <run-id> --keep-only`: the kept outputs of a reviewed easel run become
ACCEPT_TRAIN images of the project, copied and sha256 checked, with every stated field; nothing else moves.

The easel side is a fixture instance (kiln_source_helpers.easel_home) mirroring the easel demo run: outputs 1, 2
kept, 3 rejected, 4 first "maybe" then kept. The kiln side is the demo with that run's imports removed.
"""

import hashlib
import json
import tomllib
from pathlib import Path

import pytest
from kiln_project_helpers import assert_project_valid, latest_images, read_jsonl, run
from kiln_source_helpers import PROMPT, RUN, easel_home, fixtures, review, strip_imports

LINE = f"3 images registered as ACCEPT_TRAIN (verdicts from easel run {RUN})"


@pytest.fixture
def project(demo_workspace: Path, frozen_clock) -> Path:
    return strip_imports(demo_workspace)


def _import(capsys, home: Path, *extra: str) -> tuple[int, list[str]]:
    return run(["images", "import", "--from-easel-run", RUN, "--keep-only", "--easel-home", str(home), *extra], capsys)


def test_kept_outputs_become_accept_train_images(project: Path, tmp_path: Path, capsys):
    home = easel_home(tmp_path)
    before = read_jsonl(project / "images.jsonl")
    code, out = _import(capsys, home)
    assert code == 0, out
    assert LINE in out
    new = read_jsonl(project / "images.jsonl")[len(before):]
    assert len(new) == 3
    assert [line["source"]["n"] for line in new] == [1, 2, 4], "kept outputs only, in output order"
    for line in new:
        n = line["source"]["n"]
        assert line["event"] == "import"
        assert line["root"] == "imports" and line["file"] == f"{RUN}/{n:03d}.png"
        assert line["tier"] == "CANDIDATE" and line["disposition"] == "ACCEPT_TRAIN"
        assert line["split"] == "unassigned"
        assert line["verdict_source"] == f"easel-review:{RUN}"
        assert line["labels"] == ["check-na:hands", "check:body", "check:colours", "check:face", "check:feature",
                                  "check:head"], "pass, fail and na are all recorded: the image counts as vetted"
        assert line["source"] == {"kind": "generated", "tool": "easel", "run": RUN, "n": n,
                                  "recipe": "rcp-20300101-001", "prompt": PROMPT}
        assert line["caption_basis"] == PROMPT
        assert line["lineage"] == {"parent": None, "root": line["id"], "transform": "none", "grid_cell": None,
                                   "synthetic_detail_added": 0}
        assert line["content_rating"] == "sfw"
        copied = project / "images" / line["file"]
        original = home / "runs" / RUN / "outputs" / f"{n:03d}.png"
        assert copied.read_bytes() == original.read_bytes()
        assert line["sha256"] == hashlib.sha256(copied.read_bytes()).hexdigest()
    assert new[2]["notes"] == "on a second look the vest is orange", "the latest review line wins"
    assert not (project / "images" / RUN / "003.png").exists(), "a rejected output stays in easel"
    assert_project_valid(project)


def test_the_ids_match_the_demo_rows(project: Path, tmp_path: Path, capsys):
    """The same bytes give the same ids as the demo's hand-written import rows."""
    code, _ = _import(capsys, easel_home(tmp_path))
    assert code == 0
    ids = sorted(i for i, r in latest_images(project).items() if r["root"] == "imports")
    assert ids == ["img-26dab1e01d59", "img-678c78f063eb", "img-787a456e8a3d"]


def test_a_second_import_registers_nothing(project: Path, tmp_path: Path, capsys):
    home = easel_home(tmp_path)
    assert _import(capsys, home)[0] == 0
    lines = read_jsonl(project / "images.jsonl")
    code, out = _import(capsys, home)
    assert code == 0, out
    assert f"0 images registered as ACCEPT_TRAIN (verdicts from easel run {RUN})" in out
    assert sum(line.startswith("already registered: output") for line in out) == 3
    assert read_jsonl(project / "images.jsonl") == lines


def test_no_kept_output_prints_zero_and_the_hint(project: Path, tmp_path: Path, capsys):
    home = easel_home(tmp_path, reviews=[review(n, "reject", n) for n in range(1, 5)])
    lines = read_jsonl(project / "images.jsonl")
    code, out = _import(capsys, home)
    assert code == 1
    assert any(line.startswith("0 images registered") for line in out)
    assert any("easel runs import-legacy <id> --with-reviews" in line for line in out)
    assert read_jsonl(project / "images.jsonl") == lines


def test_only_the_operators_latest_verdict_counts(project: Path, tmp_path: Path, capsys):
    reviews = [review(1, "keep", 0), review(1, "reject", 5),          # changed their mind: not kept
               review(2, "keep", 1, source="assistant"),             # the second source never counts
               review(3, "maybe", 2), review(3, "keep", 3),          # kept after all
               {**review(4, "keep", 4), "n": None}]                 # a whole-run line keeps nothing
    code, out = _import(capsys, easel_home(tmp_path, reviews=reviews))
    assert code == 0, out
    assert f"1 images registered as ACCEPT_TRAIN (verdicts from easel run {RUN})" in out
    assert [r["source"]["n"] for r in latest_images(project).values() if r["root"] == "imports"] == [3]


def test_a_changed_output_is_refused(project: Path, tmp_path: Path, capsys):
    home = easel_home(tmp_path)
    target = home / "runs" / RUN / "outputs" / "002.png"
    target.write_bytes(target.read_bytes() + b"\x00")
    code, out = _import(capsys, home)
    assert code == 1
    assert any("output 2: sha256 mismatch" in line for line in out)
    assert f"2 images registered as ACCEPT_TRAIN (verdicts from easel run {RUN})" in out
    assert not (project / "images" / RUN / "002.png").exists()


def test_keep_only_is_required_and_the_run_must_exist(project: Path, tmp_path: Path, capsys):
    home = easel_home(tmp_path)
    code, out = run(["images", "import", "--from-easel-run", RUN, "--easel-home", str(home)], capsys)
    assert code == 2 and "only --keep-only imports are supported" in out[0]
    code, out = run(["images", "import", "--from-easel-run", "run-20300101-009", "--keep-only",
                     "--easel-home", str(home)], capsys)
    assert code == 2 and out[0] == "no easel run run-20300101-009: <easel_home>/runs/run-20300101-009/run.json is missing"
    code, out = run(["images", "import", "--from-easel-run", "legacy-1", "--keep-only", "--easel-home", str(home)],
                    capsys)
    assert code == 2 and "looks like run-YYYYMMDD-NNN" in out[0]
    code, out = run(["images", "import", "--keep-only"], capsys)
    assert code == 2 and "name one source" in out[0]


def test_the_easel_home_comes_from_the_config(project: Path, tmp_path: Path, capsys, monkeypatch):
    home = easel_home(tmp_path)
    code, out = run(["images", "import", "--from-easel-run", RUN, "--keep-only"], capsys)
    assert code == 2 and out[0] == "no easel instance named: pass --easel-home <dir>"
    monkeypatch.setenv("KILN_SOURCES__EASEL_HOME", str(home))
    code, out = run(["images", "import", "--from-easel-run", RUN, "--keep-only"], capsys)
    assert code == 0 and LINE in out


def test_a_legacy_imported_run_is_read_where_the_host_wrote_it(project: Path, tmp_path: Path, capsys):
    """`import-legacy` does not copy outputs: they are found under [adopt] output_root by name or sha256."""
    home = easel_home(tmp_path, copy_outputs=False)
    host = tmp_path / "host-output" / "2030" / "2030-01" / "host-run-001"
    host.mkdir(parents=True)
    record = json.loads((home / "runs" / RUN / "run.json").read_text(encoding="utf-8"))
    for output in record["outputs"]:
        (host / f"host_{output['n']:05d}_.png").write_bytes(fixtures().png_bytes(16 + output["n"]))
    record["imported_from"] = {"kind": "legacy", "manifest": "2030/2030-01/host-run-001/manifest.json"}
    (home / "runs" / RUN / "run.json").write_text(json.dumps(record), encoding="utf-8")
    (home / "easel.toml").write_text('schema = "easel/config/1"\n[adopt]\noutput_root = "../host-output"\n',
                                     encoding="utf-8")
    assert tomllib.loads((home / "easel.toml").read_text(encoding="utf-8"))["adopt"]["output_root"]
    code, out = _import(capsys, home)
    assert code == 0, out
    assert LINE in out
    assert (project / "images" / RUN / "004.png").is_file()


def test_next_moves_on_once_the_goal_run_is_imported(project: Path, tmp_path: Path, capsys):
    code, out = run(["next"], capsys)
    assert out[2] == f"next: kiln images import --from-easel-run {RUN} --keep-only"
    assert _import(capsys, easel_home(tmp_path))[0] == 0
    code, out = run(["next", "--stage"], capsys)
    assert out == ["05-captions"]
