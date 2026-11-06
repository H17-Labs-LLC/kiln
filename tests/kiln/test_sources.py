"""The image sources behind one interface: `kiln source run <jobs.json> [--dry-run]` (codex through a fake CLI,
comfy planned), `kiln images import --from-folder`, and the rules every source shares (copy into the imports
root, sha256, one id per bytes, refuse before spending). No network, no real CLI, no GPU."""

import json
import os
import sys
from pathlib import Path

import pytest
from kiln_project_helpers import assert_project_valid, errors, latest_images, read_jsonl, run
from kiln_source_helpers import fixtures, strip_imports

from kiln import sources
from kiln.sources import base

FAKE_CODEX = r'''
import json, os, sys
from pathlib import Path
if "--version" in sys.argv:
    print("codex-cli 0.0.0-test")
    sys.exit(0)
home = Path(os.environ["CODEX_HOME"])
log = Path(os.environ["FAKE_CODEX_LOG"])
calls = (int(log.read_text()) if log.exists() else 0) + 1
log.write_text(str(calls))
text = sys.stdin.read()
prompt = text.split("<<<PROMPT\n", 1)[1].split("\nPROMPT>>>", 1)[0]
attached = sys.argv.count("-i")
thread = f"thread-{calls:03d}"
print(json.dumps({"type": "thread.started", "thread_id": thread}))
mode = os.environ.get("FAKE_CODEX_MODE", "ok")
if mode == "ok":
    pngs = os.environ["FAKE_CODEX_PNGS"].split(os.pathsep)
    out = home / "generated_images" / thread
    out.mkdir(parents=True, exist_ok=True)
    (out / "exec-1.png").write_bytes(Path(pngs[(calls - 1) % len(pngs)]).read_bytes())
    sessions = home / "sessions" / "2030" / "01" / "01"
    sessions.mkdir(parents=True, exist_ok=True)
    lines = [{"payload": {"item": {"kind": "image_gen.generation", "revisedPrompt": prompt, "status": "completed"}}},
             {"payload": {"type": "function_call",
                          "arguments": "tools.image_gen({num_last_images_to_include: %d})" % attached}},
             {"payload": {"rate_limits": {"primary": {"used_percent": 12.5, "resets_at": 1893456000}}}}]
    (sessions / f"rollout-x-{thread}.jsonl").write_text("".join(json.dumps(x) + "\n" for x in lines))
    print(json.dumps({"type": "item.completed", "item": {"type": "agent_message", "text": "{\"path\": \"x\"}"}}))
elif mode == "blocked":
    print(json.dumps({"type": "error", "message": "moderation_blocked: output stage"}))
elif mode == "refused":
    print(json.dumps({"type": "item.completed", "item": {"type": "agent_message",
                                                          "text": "Sorry, I can't help with that request."}}))
elif mode == "quota":
    print(json.dumps({"type": "error", "message": "You have hit your usage limit; try again in 3 hours"}))
    sys.exit(1)
print(json.dumps({"type": "turn.completed"}))
'''


@pytest.fixture
def project(demo_workspace: Path, frozen_clock, capsys) -> Path:
    folder = strip_imports(demo_workspace)
    # the demo jobs file names @front: the demo's operator labelled the front anchor
    assert "anchor:front" in latest_images(folder)["img-010af39c9f5c"]["labels"]
    return folder


@pytest.fixture
def fake_codex(project: Path, demo_workspace: Path, tmp_path: Path, monkeypatch) -> dict:
    """A fake Codex CLI named by the instance's own sources/codex.yaml; returns its knobs."""
    script = tmp_path / "fake_codex.py"
    script.write_text(FAKE_CODEX, encoding="utf-8")
    (demo_workspace / "sources").mkdir(exist_ok=True)
    (demo_workspace / "sources" / "codex.yaml").write_text(
        json.dumps({"command": [sys.executable, str(script)], "timeout_s": 60}), encoding="utf-8")
    gen = tmp_path / "pngs"
    gen.mkdir()
    pngs = []
    for n in (19, 22, 23, 24):
        path = gen / f"p{n}.png"
        path.write_bytes(fixtures().png_bytes(n))
        pngs.append(str(path))
    knobs = {"log": tmp_path / "calls.txt", "home": tmp_path / "codex-home"}
    monkeypatch.setenv("KILN_SOURCES__CODEX_HOME", str(knobs["home"]))
    monkeypatch.setenv("FAKE_CODEX_LOG", str(knobs["log"]))
    monkeypatch.setenv("FAKE_CODEX_PNGS", os.pathsep.join(pngs))
    monkeypatch.setenv("OPENAI_API_KEY", "sk-not-a-key")
    return knobs


def _jobs(project: Path, jobs: list[dict], name: str = "batch-02", source: str = "codex") -> Path:
    data = {"schema": "kiln/jobs-file/1", "name": name, "project": "demo_robot", "source": source,
            "out_dir": f"projects/demo_robot/images/{name}",
            "groups": {"A": {"title": "Views", "question": "Does the visor hold?"}}, "jobs": jobs}
    path = project / "jobs" / f"{name}.json"
    path.write_text(json.dumps(data, indent=2), encoding="utf-8")
    return path


def _job(job_id: str = "A1", **kw) -> dict:
    return {"id": job_id, "group": "A", "prefix": "front_full", "mode": "generate", "intent": "generate_new",
            "viewpoint": "front", "framing": "full", "outfit": "default", "want": 1,
            "tests": "the visor, front on", "scene": "standing relaxed, plain studio backdrop",
            "refs": ["img-010af39c9f5c"], **kw}


# --- dry run -----------------------------------------------------------------------------------------------------

def test_dry_run_prints_every_job_and_writes_nothing(project: Path, capsys):
    lines = read_jsonl(project / "images.jsonl")
    code, out = run(["source", "run", "jobs/batch-01.json", "--dry-run"], capsys)
    assert code == 0, out
    assert out[0] == "jobs file jobs/batch-01.json: 3 job(s), source codex, out projects/demo_robot/images/batch-01"
    assert "    prompt| dmrbt, robot, front view, full body, standing relaxed, plain studio backdrop" in out
    assert "      attach Image A: img-f4fae49d1cf6 (edit target)" in out, "an edit target is attached first"
    assert "      attach Image B: img-010af39c9f5c (identity)" in out
    assert out[-1] == "dry run: nothing sent, nothing written; 5 image(s) would be asked for"
    assert read_jsonl(project / "images.jsonl") == lines
    assert not (project / "images" / "batch-01").exists()


def test_the_prompt_leaves_out_what_this_view_cannot_show(project: Path, capsys):
    code, out = run(["source", "run", "jobs/batch-01.json", "--dry-run"], capsys)
    a2 = out[out.index(next(x for x in out if x.startswith("  A2 "))):]
    a2 = a2[:a2.index(next(x for x in a2 if x.startswith("  B1 ")))]
    assert not any(x.startswith("    prompt| VISOR:") for x in a2), "skip_when viewpoint back"
    assert any(x.startswith("    prompt| OUTFIT: ") for x in a2)


def test_problems_are_shown_and_refused_before_spending(project: Path, fake_codex: dict, capsys):
    jobs = _jobs(project, [_job(refs=["img-000000000000"]),
                           _job("A2", scene="a young robot at a desk"),
                           _job("A3", refs=["@nobody"]),
                           _job("A4", mode="edit", intent="identity_correct")])
    code, out = run(["source", "run", str(jobs), "--dry-run"], capsys)
    assert code == 2
    text = "\n".join(out)
    assert "job A1: reference img-000000000000 is not an image of demo_robot" in text
    assert "job A2: the word 'young' makes the subject read young" in text
    assert "job A3: reference @nobody: no image is labelled anchor:nobody" in text
    assert "job A4: an edit job names its parent image" in text
    code, out = run(["source", "run", str(jobs)], capsys)
    assert code == 2 and out[0] == "REFUSED: jobs/batch-02.json has 4 problem(s); nothing was sent"
    assert not fake_codex["log"].exists(), "the CLI was never called"


def test_too_many_attachments_are_refused(project: Path, fake_codex: dict, capsys):
    ids = sorted(latest_images(project))[:6]
    jobs = _jobs(project, [_job(refs=ids)])
    code, out = run(["source", "run", str(jobs)], capsys)
    assert code == 2
    assert any("6 images attached; the image tool takes at most 5" in line for line in out)
    assert not fake_codex["log"].exists()


def test_a_jobs_file_for_another_project_or_source_is_refused(project: Path, capsys):
    jobs = _jobs(project, [_job()])
    data = json.loads(jobs.read_text(encoding="utf-8"))
    data["project"] = "other_robot"
    jobs.write_text(json.dumps(data), encoding="utf-8")
    code, out = run(["source", "run", str(jobs), "--dry-run"], capsys)
    assert code == 2 and "is for project other_robot, not demo_robot" in out[0]
    files = _jobs(project, [_job()], name="batch-03", source="files")
    code, out = run(["source", "run", str(files), "--dry-run"], capsys)
    assert code == 2 and out[1] == "register a folder: kiln images import --from-folder <dir>"
    easel = _jobs(project, [_job()], name="batch-04", source="easel-run")
    code, out = run(["source", "run", str(easel), "--dry-run"], capsys)
    assert code == 2 and "--from-easel-run" in out[1]


def test_comfy_is_planned_but_dry_runs(project: Path, capsys):
    jobs = _jobs(project, [_job()], source="comfy")
    code, out = run(["source", "run", str(jobs), "--dry-run"], capsys)
    assert code == 0, out
    assert any("submitted through the scheduler" in line for line in out)
    code, out = run(["source", "run", str(jobs)], capsys)
    assert code == 2 and out[0].startswith("planned: session-2")


# --- codex through the fake CLI ------------------------------------------------------------------------------------

def test_codex_jobs_register_candidates_with_a_manifest(project: Path, fake_codex: dict, capsys):
    parent = "img-f4fae49d1cf6"
    jobs = _jobs(project, [_job(),
                           _job("A2", prefix="fix", mode="edit", intent="identity_correct", parent=parent,
                                scene="keep the scene", refs=[{"ref": "img-010af39c9f5c", "role": "identity"}])])
    before = len(read_jsonl(project / "images.jsonl"))
    code, out = run(["source", "run", str(jobs)], capsys)
    assert code == 0, out
    assert out[-2] == "2 images registered from jobs/batch-02.json (codex); 0 job(s) without images"
    assert fake_codex["log"].read_text() == "2"
    new = read_jsonl(project / "images.jsonl")[before:]
    assert [line["event"] for line in new] == ["register", "register"], "viewpoint and framing ride on the register"
    images = latest_images(project)
    a1, a2 = (images[line["id"]] for line in new)
    assert a1 == new[0]
    assert a1["root"] == "imports" and a1["file"] == "batch-02/A1-front_full-1.png"
    assert a1["source"]["kind"] == "codex" and a1["source"]["note"] == "batch-02/A1"
    assert a1["source"]["manifest"] == "projects/demo_robot/images/batch-02/A1.manifest.json"
    assert a1["labels"] == ["job:batch-02/A1", "outfit:default"]
    assert a1["viewpoint"] == "front" and a1["framing"] == "full"
    assert a1["disposition"] is None and a1["tier"] == "CANDIDATE", "untriaged: only the operator judges"
    assert a1["caption_basis"] == "standing relaxed, plain studio backdrop"
    assert a2["lineage"]["parent"] == parent and a2["lineage"]["synthetic_detail_added"] == 1
    manifest = json.loads((project / "images" / "batch-02" / "A1.manifest.json").read_text(encoding="utf-8"))
    assert errors("generation-manifest", manifest) == []
    assert manifest["verbatim"] is True and manifest["thread_id"] == "thread-001"
    assert manifest["tool_version"] == "codex-cli 0.0.0-test"
    assert manifest["images"][0]["image"] == a1["id"]
    assert manifest["refs_sent"] == ["1 image(s) included"]
    assert manifest["quota"]["window_used_percent"] == 12.5
    second = json.loads((project / "images" / "batch-02" / "A2.manifest.json").read_text(encoding="utf-8"))
    assert second["references"] == [parent, "img-010af39c9f5c"]
    assert_project_valid(project)


@pytest.mark.parametrize(("mode", "code", "status"), [("blocked", 1, "blocked"), ("refused", 1, "refused"),
                                                       ("quota", 3, "quota")])
def test_a_job_without_images_is_classified_never_retried(project: Path, fake_codex: dict, capsys, monkeypatch,
                                                          mode: str, code: int, status: str):
    monkeypatch.setenv("FAKE_CODEX_MODE", mode)
    jobs = _jobs(project, [_job(), _job("A2")])
    before = read_jsonl(project / "images.jsonl")
    got, out = run(["source", "run", str(jobs)], capsys)
    assert got == code, out
    assert any(line.startswith(f"A1:   {status}: ") for line in out)
    assert read_jsonl(project / "images.jsonl") == before
    manifest = json.loads((project / "images" / "batch-02" / "A1.manifest.json").read_text(encoding="utf-8"))
    assert errors("generation-manifest", manifest) == [] and manifest["images"] == []
    calls = int(fake_codex["log"].read_text())
    assert calls == (1 if mode == "quota" else 2), "quota stops the batch; the others go on, each job once"


def test_the_api_key_never_reaches_the_cli(project: Path, fake_codex: dict, capsys, tmp_path: Path):
    from kiln.sources import codex

    settings = codex.load_settings()
    assert "OPENAI_API_KEY" in settings["drop_env"]
    seen = tmp_path / "env.txt"
    script = tmp_path / "fake_codex.py"
    script.write_text(script.read_text(encoding="utf-8").replace(
        'home = Path(os.environ["CODEX_HOME"])',
        f'Path({str(seen)!r}).write_text(str("OPENAI_API_KEY" in os.environ))\nhome = Path(os.environ["CODEX_HOME"])'),
        encoding="utf-8")
    jobs = _jobs(project, [_job()])
    assert run(["source", "run", str(jobs)], capsys)[0] == 0
    assert seen.read_text() == "False"


# --- folders -------------------------------------------------------------------------------------------------------

def test_folder_import_copies_and_registers_once(project: Path, tmp_path: Path, capsys):
    batch = tmp_path / "shoot"
    batch.mkdir()
    (batch / "x.png").write_bytes(fixtures().png_bytes(22))
    (batch / "y.png").write_bytes(fixtures().png_bytes(23))
    (batch / "dup.png").write_bytes(fixtures().png_bytes(1))  # already in the demo as a dataset image
    (tmp_path / "notes.csv").write_text("file,note,viewpoint,framing,labels\n"
                                        "x.png,waving at the door,front,full,light:day\n"
                                        "gone.png,a file that is not there,,,\n", encoding="utf-8")
    before = len(read_jsonl(project / "images.jsonl"))
    code, out = run(["images", "import", "--from-folder", str(batch), "--notes", str(tmp_path / "notes.csv"),
                     "--kind", "upload"], capsys)
    assert code == 0, out
    assert "already registered: dup.png is img-010af39c9f5c; nothing written" in out
    assert "note unused: gone.png is not an image of the folder" in out
    assert out[-2] == "2 images registered from shoot (untriaged); 1 already registered, 0 problem(s)"
    images = latest_images(project)
    x = next(r for r in images.values() if r["file"] == "shoot/x.png")
    assert x["root"] == "imports" and x["source"] == {"kind": "upload", "tool": "kiln images import", "note": "shoot"}
    assert x["notes"] == "waving at the door" and x["viewpoint"] == "front" and x["labels"] == ["light:day"]
    assert x["disposition"] is None
    assert (project / "images" / "shoot" / "x.png").read_bytes() == (batch / "x.png").read_bytes()
    assert not (project / "images" / "shoot" / "dup.png").exists()
    code, out = run(["images", "import", "--from-folder", str(batch)], capsys)
    assert out[-1] == "0 images registered from shoot (untriaged); 3 already registered, 0 problem(s)"
    assert len(read_jsonl(project / "images.jsonl")) == before + 2  # two registers; the viewpoint rides on one
    assert_project_valid(project)


def test_folder_import_never_replaces_a_different_file(project: Path, tmp_path: Path, capsys):
    batch = tmp_path / "shoot"
    batch.mkdir()
    (batch / "x.png").write_bytes(fixtures().png_bytes(22))
    taken = project / "images" / "shoot" / "x.png"
    taken.parent.mkdir(parents=True)
    taken.write_bytes(b"not the same bytes")
    code, out = run(["images", "import", "--from-folder", str(batch)], capsys)
    assert code == 1
    assert "problem: projects/demo_robot/images/shoot/x.png exists with different bytes; nothing replaced" in out
    assert taken.read_bytes() == b"not the same bytes"


def test_a_folder_inside_the_imports_root_is_registered_in_place(project: Path, capsys):
    folder = project / "images" / "drop"
    folder.mkdir(parents=True)
    (folder / "z.png").write_bytes(fixtures().png_bytes(24))
    code, out = run(["images", "import", "--from-folder", str(folder)], capsys)
    assert code == 0, out
    assert any(line.endswith("imports:drop/z.png") for line in out)
    assert sorted(p.name for p in folder.iterdir()) == ["z.png"]


def test_folder_import_refuses_bad_notes_and_empty_folders(project: Path, tmp_path: Path, capsys):
    empty = tmp_path / "empty"
    empty.mkdir()
    code, out = run(["images", "import", "--from-folder", str(empty)], capsys)
    assert code == 1 and out[0] == "no image files (png, jpeg, webp) in empty"
    batch = tmp_path / "shoot"
    batch.mkdir()
    (batch / "x.png").write_bytes(fixtures().png_bytes(22))
    (tmp_path / "bad.csv").write_text("file,note,viewpoint\nx.png,hi,sideways\n", encoding="utf-8")
    code, out = run(["images", "import", "--from-folder", str(batch), "--notes", str(tmp_path / "bad.csv")], capsys)
    assert code == 1 and any("viewpoint 'sideways' is not one of" in line for line in out)
    code, out = run(["images", "import", "--from-folder", str(batch), "--keep-only"], capsys)
    assert code == 2 and out[0] == "--keep-only and --easel-home go with --from-easel-run"


# --- the interface -------------------------------------------------------------------------------------------------

def test_every_kind_has_a_source_and_unknown_kinds_fail():
    for kind in sources.KINDS:
        source = sources.get(kind)
        assert isinstance(source, base.Source) and source.kind == kind
    assert [k for k in sources.KINDS if sources.get(k).makes_images] == ["codex", "comfy"]
    with pytest.raises(ValueError):
        sources.get("camera")


def test_the_shipped_codex_settings_load(project: Path):
    from kiln.sources import codex

    settings = codex.load_settings()
    assert settings["max_references"] == 5 and settings["command"] == ["codex"]
    assert "{prompt}" in settings["relay"] and "{references}" in settings["relay"]
    text = codex.relay_text(settings, "a {braced} prompt", 2, [])
    assert "<<<PROMPT\na {braced} prompt\nPROMPT>>>" in text and "2 times" in text
