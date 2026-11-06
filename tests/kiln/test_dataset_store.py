"""`kiln dataset freeze|materialize|check` on the demo: the old+new count, the refusals, the read-only images_root."""

import hashlib
import json
from pathlib import Path

from kiln_project_helpers import SLUG, assert_project_valid, read_jsonl, run

from kiln.project import materialize as materialize_mod
from kiln.project import model


def _tree_digest(folder: Path) -> dict[str, str]:
    return {p.relative_to(folder).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in sorted(folder.rglob("*")) if p.is_file()}


def test_freeze_from_keeps_the_old_members_and_adds_the_new(demo_workspace: Path, capsys, frozen_clock):
    project = demo_workspace / "projects" / SLUG
    old = json.loads((project / "snapshots" / "0.2.0.json").read_text(encoding="utf-8"))
    code, out = run(["dataset", "freeze", "0.3.0", "--from", "0.2.0"], capsys)
    assert code == 0, out
    assert out[0] == f"frozen {SLUG}/0.3.0: 15 members (12 from 0.2.0 + 3 new)"
    snap = json.loads((project / "snapshots" / "0.3.0.json").read_text(encoding="utf-8"))
    assert len(snap["members"]) == len(old["members"]) + 3 == 15
    assert snap["from_version"] == "0.2.0" and snap["frozen_at"] == "2030-01-01T00:00:00+00:00"
    assert snap["counts"] == {"train": 13, "holdout": 2, "excluded": 0}
    assert {m["image"] for m in old["members"]} < {m["image"] for m in snap["members"]}
    assert [m["image"] for m in snap["members"]] == sorted(m["image"] for m in snap["members"])
    imports = {"img-26dab1e01d59", "img-787a456e8a3d", "img-678c78f063eb"}
    assert {m["image"] for m in snap["members"]} - {m["image"] for m in old["members"]} == imports
    assert all(m["caption"] and m["caption"].startswith("dmrbt") for m in snap["members"])
    ordered = sorted(snap["members"], key=lambda m: m["image"])
    want = hashlib.sha256(json.dumps(ordered, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    assert snap["members_sha256"] == want
    assert "joined train" in out[3]
    assert_project_valid(project)
    code, out = run(["dataset", "freeze", "0.3.0", "--from", "0.2.0"], capsys)
    assert code == 2 and out == ["REFUSED: snapshot 0.3.0 exists; snapshots are immutable: pick a new version number"]


def test_freeze_refuses_an_uncaptioned_train_image(demo_workspace: Path, capsys, frozen_clock):
    project = demo_workspace / "projects" / SLUG
    lines = read_jsonl(project / "captions.jsonl")
    approved = next(line for line in lines if line["image"] == "img-26dab1e01d59" and line["status"] == "approved")
    draft = dict(approved, status="draft", source="kiln:captions propose", at="2030-01-05T00:00:00+00:00")
    with (project / "captions.jsonl").open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(draft) + "\n")  # a later draft replaces the approval
    code, out = run(["dataset", "freeze", "0.3.0"], capsys)
    assert code == 2, out
    assert out[0] == "REFUSED: 1 train image(s) without an approved caption: img-26dab1e01d59"
    assert not (project / "snapshots" / "0.3.0.json").exists()
    code, out = run(["captions", "status"], capsys)
    assert "draft: 1" in out
    code, out = run(["captions", "lint"], capsys)
    assert code == 1 and "replaced by a later draft" in out[0], out
    code, out = run(["next", "--stage"], capsys)
    assert out == ["05-captions"]


def test_materialize_copies_legacy_members_read_only(demo_workspace: Path, capsys, frozen_clock):
    images_root = demo_workspace / "dataset-images"
    before = _tree_digest(images_root)
    run(["dataset", "freeze", "0.3.0"], capsys)
    code, out = run(["dataset", "materialize", "0.3.0"], capsys)
    assert code == 0, out
    assert _tree_digest(images_root) == before, "materialize wrote under images_root"
    base = demo_workspace / "projects" / SLUG / "datasets" / "0.3.0"
    assert sorted(p.name for p in base.iterdir()) == ["_meta", "holdout", "training"]
    training = sorted(p.name for p in (base / "training").iterdir())
    assert len([n for n in training if n.endswith(".png")]) == 13
    assert len([n for n in training if n.endswith(".txt")]) == 13
    assert (base / "holdout" / "demo_robot-07.txt").read_text(encoding="utf-8").startswith("dmrbt, robot, front view")
    record = json.loads((base / "_meta" / "materialized-0.3.0.json").read_text(encoding="utf-8"))
    assert list(record) == ["schema", "snapshot_id", "snapshot_manifest_sha256", "materialized_at", "caption_level",
                            "trigger_token", "counts", "directories", "note", "record_sha256_12"]
    assert record["counts"] == {"train": 13, "holdout": 2}
    assert record["directories"] == {"train": f"projects/{SLUG}/datasets/0.3.0/training",
                                     "holdout": f"projects/{SLUG}/datasets/0.3.0/holdout"}
    assert record["record_sha256_12"] == materialize_mod.record_stamp(record)
    assert_project_valid(demo_workspace / "projects" / SLUG)
    code, out = run(["dataset", "check", "0.3.0"], capsys)
    assert code == 0 and out[-1].endswith("captions 15/15; matches the snapshot"), out


def test_materialize_into_images_root_is_refused(demo_workspace: Path, capsys):
    images_root = demo_workspace / "dataset-images"
    before = _tree_digest(images_root)
    run(["dataset", "freeze", "0.3.0"], capsys)
    for target in (images_root / "datasets" / "0.3.0", images_root):
        code, out = run(["dataset", "materialize", "0.3.0", "--into", str(target)], capsys)
        assert code == 2, (target, out)
        assert out[0].startswith("REFUSED:") and "images_root" in out[0], out
    assert _tree_digest(images_root) == before


def test_materialize_refusals(demo_workspace: Path, capsys, tmp_path: Path):
    run(["dataset", "freeze", "0.3.0"], capsys)
    code, out = run(["dataset", "materialize", "0.3.0", "--into", str(tmp_path / "elsewhere")], capsys)
    assert code == 2 and "inside the instance folder" in out[0], out
    code, out = run(["dataset", "materialize", "0.9.0"], capsys)
    assert code == 2 and out[0].startswith(f"no snapshot {SLUG}/0.9.0"), out
    source = demo_workspace / "dataset-images" / "raw" / "demo_robot-01.png"
    source.write_bytes(source.read_bytes() + b"\x00")  # a changed legacy file
    code, out = run(["dataset", "materialize", "0.3.0"], capsys)
    assert code == 2, out
    assert out[0] == "REFUSED: the snapshot's sources do not check out; nothing was written"
    assert "img-010af39c9f5c: source bytes differ" in out[1]
    assert not (demo_workspace / "projects" / SLUG / "datasets").exists()


def test_check_finds_a_hand_edited_folder(demo_workspace: Path, capsys):
    run(["dataset", "freeze", "0.3.0"], capsys)
    run(["dataset", "materialize", "0.3.0"], capsys)
    base = demo_workspace / "projects" / SLUG / "datasets" / "0.3.0"
    (base / "holdout" / "demo_robot-11.txt").unlink()
    code, out = run(["dataset", "check"], capsys)
    assert code == 1, out
    assert any("holdout: 1 image(s) without a caption file: demo_robot-11.png" in line for line in out), out
    code, out = run(["next", "--stage"], capsys)
    assert out == ["07-freeze-and-gate"]
    code, out = run(["dataset", "materialize", "0.3.0"], capsys)
    assert code == 2 and out[0].startswith(f"REFUSED: projects/{SLUG}/datasets/0.3.0 already holds"), out
    assert out[1] == "  materialise into a fresh folder: kiln dataset materialize 0.3.0 --into <dir> (a new folder " \
                     "inside the instance)", out
    code, out = run(["dataset", "materialize", "0.3.0", "--into", str(demo_workspace / "fresh")], capsys)
    assert code == 0, out
    code, out = run(["dataset", "check", "--into", str(demo_workspace / "fresh")], capsys)
    assert code == 0, out
    # the newest materialisation is found without naming its folder again: check, next and the preflight agree
    code, out = run(["dataset", "check", "0.3.0"], capsys)
    assert code == 0 and out[-1].endswith("matches the snapshot"), out
    pointer = demo_workspace / "projects" / SLUG / "datasets" / "_meta" / "materialized-0.3.0.json"
    assert json.loads(pointer.read_text(encoding="utf-8"))["directories"]["train"] == "fresh/training"
    code, out = run(["next", "--stage"], capsys)
    assert out == ["08-train"], out


def test_a_record_written_outside_kiln_is_a_note_on_an_adopted_project(demo_workspace: Path, capsys):
    """A version the host workspace materialised before kiln: its record names the host's folders."""
    project = demo_workspace / "projects" / SLUG
    data = (project / "project.yaml").read_text(encoding="utf-8")
    (project / "project.yaml").write_text(data.replace("adopted: false", "adopted: true"), encoding="utf-8")
    meta = project / "datasets" / "0.2.0" / "_meta"
    meta.mkdir(parents=True)
    record = {"schema": "kiln/materialized/1", "snapshot_id": f"{SLUG}/0.2.0", "snapshot_manifest_sha256": "0" * 64,
              "materialized_at": "2030-01-02T00:00:00+00:00", "caption_level": "medium", "trigger_token": "dmrbt",
              "counts": {"train": 10, "holdout": 2},
              "directories": {"train": "host-datasets/0.2.0/training", "holdout": "host-datasets/0.2.0/holdout"},
              "note": "made by the host"}
    record["record_sha256_12"] = materialize_mod.record_stamp(record)
    (meta / "materialized-0.2.0.json").write_text(json.dumps(record), encoding="utf-8")
    code, out = run(["dataset", "check", "0.2.0"], capsys)
    assert code == 0, out
    assert any("written outside kiln (adopted project)" in line for line in out), out
    folder = materialize_mod.check_folder(model.Instance.load(demo_workspace).project(SLUG), "0.2.0")
    assert folder.found and folder.foreign and not folder.ok
    code, out = run(["dataset", "materialize", "0.2.0"], capsys)
    assert code == 2 and "already holds 1 file(s)" in out[0], "the host's record is never overwritten"
    code, out = run(["dataset", "materialize", "0.2.0", "--into", str(demo_workspace / "kiln-0.2.0")], capsys)
    assert code == 0, out
    assert materialize_mod.check_folder(model.Instance.load(demo_workspace).project(SLUG), "0.2.0").ok


def test_materialize_refuses_an_uncaptioned_train_member(demo_workspace: Path, capsys):
    """An exported old version may hold a train member without a caption: materialising it is refused."""
    path = demo_workspace / "projects" / SLUG / "snapshots" / "0.1.0.json"
    snap = json.loads(path.read_text(encoding="utf-8"))
    snap["members"][0]["caption"] = None
    path.write_text(json.dumps(snap, indent=2), encoding="utf-8")
    code, out = run(["dataset", "materialize", "0.1.0"], capsys)
    assert code == 2, out
    assert out[1].startswith(f"  {snap['members'][0]['image']}: a train member without a caption")
    assert not (demo_workspace / "projects" / SLUG / "datasets").exists()
    code, out = run(["dataset", "materialize", "0.2.0"], capsys)
    assert code == 0, out
