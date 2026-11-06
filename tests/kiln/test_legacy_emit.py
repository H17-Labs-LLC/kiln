"""`kiln train plan --emit-legacy` against a synthetic host workspace in tmp_path (never a real one).

The host is made up at test time: a placeholder launcher template, a dataset template, a stand-in renderer with
the host renderer's contract (`<template> --set KEY=VALUE ... --out <file>`; exit 2 on a missing variable, 3 on
a literal absolute path in the template) and a SQLite run registry with synthetic ids. The legacy root is
tmp_path, which also holds the demo instance (`tmp_path/demo`), so every path the host reads is relative.
"""

import json
import sqlite3
from pathlib import Path

import pytest
from kiln_project_helpers import SLUG, errors, run

from kiln.train import legacy_emit

DEMO_RUN = "run-20300102-001"
NEW_RUN = "run-20300101-001"
PICKED = "cf56db17414bcb55"
EXP = "demo_exp"
CONFIGS = f"host/train/{EXP}/configs"

RENDERER = r'''"""A stand-in host renderer for the tests: ${VAR} from --set; exit 2 on a missing one, 3 on a literal path."""
import argparse
import re
import sys
from pathlib import Path

VAR = re.compile(r"(?<!\$)\$\{([A-Za-z_][A-Za-z0-9_]*)\}")
ABSOLUTE = re.compile(r"(?<![\w$])[A-Za-z]:[\\/]")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("template")
    ap.add_argument("--set", action="append", default=[])
    ap.add_argument("--out", required=True)
    a = ap.parse_args()
    text = Path(a.template).read_text(encoding="utf-8-sig")
    values = dict(pair.split("=", 1) for pair in a.set)
    missing = sorted(set(VAR.findall(text)) - set(values))
    if missing:
        print("MISSING   " + ", ".join(missing), file=sys.stderr)
        return 2
    if ABSOLUTE.search(text):
        print("REFUSED   the template holds a literal absolute path", file=sys.stderr)
        return 3
    out = Path(a.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(VAR.sub(lambda m: values[m.group(1)], text), encoding="utf-8")
    print("wrote     " + out.name)
    return 0


sys.exit(main())
'''

LAUNCHER = """# synthetic placeholder launcher template (tests only)
$RunId = "${RUN_ID}"
$Dataset = "${DATASET_ROOT}/training"
$Meta = "${DATASET_ROOT}/_meta/materialized-${DATASET_VERSION}.json"
$OutDir = "${OUTPUT_DIR}"
$LogDir = "${LOGGING_DIR}"
$Base = "${BASE_CHECKPOINT}"
trainer --max_train_steps ${MAX_TRAIN_STEPS} --save_every_n_steps ${SAVE_EVERY} --validate_every_n_steps ${VALIDATE_EVERY}
"""
WEIGHTS_LINE = '$Weights = "${NETWORK_WEIGHTS}"\n'
DATASET = """# synthetic placeholder dataset template (tests only)
[[datasets.subsets]]
image_dir = "${DATASET_ROOT}/training"
"""
LEGACY_TABLE = f"""[legacy]
root = ".."
exp = "{EXP}"
renderer = "host/tools/render.py"
launcher_template = "{CONFIGS}/launcher.ps1"
dataset_template = "{CONFIGS}/dataset.toml"
generated_dir = "{CONFIGS}/_generated"
queue_dir = "{CONFIGS}"
run_registry = "host/train/registry.db"
"""
HOST_IDS = ("HOST-0001", "HOST-0002", "SMOKE-01")


def make_host(root: Path, home: Path, *, launcher: str = LAUNCHER, dataset: str = DATASET,
              ids: tuple[str, ...] = HOST_IDS) -> dict[str, Path]:
    """Write the synthetic host under `root` and point the demo instance's [legacy] at it."""
    paths = {"renderer": root / "host" / "tools" / "render.py", "launcher": root / CONFIGS / "launcher.ps1",
             "dataset": root / CONFIGS / "dataset.toml", "generated": root / CONFIGS / "_generated",
             "queue_dir": root / CONFIGS, "registry": root / "host" / "train" / "registry.db"}
    for key in ("renderer", "launcher", "dataset", "registry"):
        paths[key].parent.mkdir(parents=True, exist_ok=True)
    paths["generated"].mkdir(parents=True, exist_ok=True)
    paths["renderer"].write_text(RENDERER, encoding="utf-8")
    paths["launcher"].write_text(launcher, encoding="utf-8")
    paths["dataset"].write_text(dataset, encoding="utf-8")
    conn = sqlite3.connect(paths["registry"])
    conn.execute("CREATE TABLE training_run (run_id TEXT PRIMARY KEY, notes TEXT)")
    conn.executemany("INSERT INTO training_run (run_id) VALUES (?)", [(i,) for i in ids])
    conn.commit()
    conn.close()
    toml = home / "kiln.toml"
    text = toml.read_text(encoding="utf-8")
    assert text.count("[legacy]\n") == 1
    toml.write_text(text.replace("[legacy]\n", LEGACY_TABLE), encoding="utf-8")
    base = home / "models" / "checkpoints" / "demo-anime-xl.safetensors"  # the demo base's file name
    base.parent.mkdir(parents=True, exist_ok=True)
    base.write_bytes(b"synthetic base checkpoint")
    return paths


def materialise(capsys, version: str = "0.2.0") -> None:
    code, out = run(["dataset", "materialize", version], capsys)
    assert code == 0, out


def emit_argv(root: Path, *extra: str, version: str = "0.2.0") -> list[str]:
    return ["train", "plan", version, "--family", "illustrious", "--base-from-run", DEMO_RUN, "--steps", "1000",
            "--emit-legacy", "--legacy-root", str(root), *extra]


def _written(paths: dict[str, Path]) -> list[str]:
    return sorted(p.name for p in paths["generated"].iterdir()) + sorted(p.name for p in paths["queue_dir"].glob("queue-*"))


def test_emit_renders_the_launcher_the_dataset_config_and_the_queue(demo_workspace, tmp_path, capsys, frozen_clock):
    paths = make_host(tmp_path, demo_workspace)
    materialise(capsys)
    code, out = run(emit_argv(tmp_path), capsys)
    assert code == 0, out
    assert "legacy run id: HOST-0003 (next free in the host registry)" in out

    launcher = demo_workspace / "projects" / SLUG / "runs" / NEW_RUN / "legacy" / "HOST-0003.ps1"
    text = launcher.read_text(encoding="utf-8")
    assert "${" not in text, text  # every variable substituted
    dataset_root = f"demo/projects/{SLUG}/datasets/0.2.0"
    for want in ('$RunId = "HOST-0003"', f'$Dataset = "{dataset_root}/training"',
                 f'$Meta = "{dataset_root}/_meta/materialized-0.2.0.json"',
                 f'$OutDir = "host/train/{EXP}/output/HOST-0003"', f'$LogDir = "host/train/{EXP}/logs/HOST-0003"',
                 '$Base = "demo/models/checkpoints/demo-anime-xl.safetensors"',
                 "--max_train_steps 1000 --save_every_n_steps 200 --validate_every_n_steps 200"):
        assert want in text, want
    assert "(9 variables substituted)" in next(line for line in out if line.startswith("  launcher"))
    rendered = (paths["generated"] / "HOST-0003.dataset.toml").read_text(encoding="utf-8")
    assert f'image_dir = "{dataset_root}/training"' in rendered and "${" not in rendered

    queue = json.loads((paths["queue_dir"] / "queue-HOST-0003.json").read_text(encoding="utf-8"))
    assert set(legacy_emit.QUEUE_KEYS) <= set(queue)
    assert queue["experiment"] == EXP and queue["min_free_ram_gb_train"] == 5.5 and queue["min_free_ram_gb_eval"] == 8.5
    (job,) = queue["jobs"]
    assert set(job) == {"run", "launcher", "steps", "eval"}
    assert job["run"] == "HOST-0003" and job["steps"] == 1000
    assert (tmp_path / job["launcher"]).resolve() == launcher.resolve()
    assert job["eval"] == {"family": "illustrious", "base": "demo-anime-xl.safetensors",
                           "steps": "200,400,600,800,1000", "suite": "core"}
    assert legacy_emit.check_queue(paths["queue_dir"] / "queue-HOST-0003.json", tmp_path, "HOST-0003") == ([], 1)

    snap = json.loads((demo_workspace / "projects" / SLUG / "snapshots" / "0.2.0.json").read_text(encoding="utf-8"))
    registry_line = next(line for line in out if line.startswith("run add "))
    assert registry_line == (f"run add {EXP} --run-id HOST-0003 --base-model demo-anime-xl.safetensors --config "
                             f"{CONFIGS}/_generated/HOST-0003.dataset.toml --steps 1000 --output-dir "
                             f"host/train/{EXP}/output/HOST-0003 --eval-suite core --notes "
                             f'"dataset: kiln {SLUG}/0.2.0 members_sha256={snap["members_sha256"]}"')
    assert not any("--snapshot " in line for line in out if line.startswith("run add")), "run add carries no --snapshot"
    assert f"run {CONFIGS}/queue-HOST-0003.json" in out
    assert out[-1] == "next: kiln train preflight 0.2.0"

    record = json.loads((launcher.parent.parent / "run.json").read_text(encoding="utf-8"))
    assert errors("run", record) == []
    assert record["legacy_id"] == "HOST-0003" and record["status"] == "planned"
    assert record["legacy"]["paths_relative_to"] == "the legacy workspace root"
    assert record["config"]["path"] == f"{CONFIGS}/_generated/HOST-0003.dataset.toml"
    assert record["output_dir"] == f"host/train/{EXP}/output/HOST-0003"
    assert record["base"]["licence"]["name"] == "Demo Open Licence"


def test_continue_from_without_a_template_variable_is_refused(demo_workspace, tmp_path, capsys, frozen_clock):
    paths = make_host(tmp_path, demo_workspace)
    materialise(capsys)
    code, out = run(emit_argv(tmp_path, "--continue-from", PICKED), capsys)
    assert code == 2, out
    assert out[0] == "continue-from: unsupported by the host template until cutover"
    assert not (demo_workspace / "projects" / SLUG / "runs" / NEW_RUN).exists()
    assert _written(paths) == []


def test_continue_from_maps_to_the_template_variable(demo_workspace, tmp_path, capsys, frozen_clock):
    make_host(tmp_path, demo_workspace, launcher=LAUNCHER + WEIGHTS_LINE)
    materialise(capsys)
    code, out = run(emit_argv(tmp_path, "--continue-from", PICKED), capsys)
    assert code == 0, out
    folder = demo_workspace / "projects" / SLUG / "runs" / NEW_RUN
    text = (folder / "legacy" / "HOST-0003.ps1").read_text(encoding="utf-8")
    want = f"demo/projects/{SLUG}/runs/{DEMO_RUN}/output/demo_robot-000800.safetensors"
    assert f'$Weights = "{want}"' in text and "${" not in text
    record = json.loads((folder / "run.json").read_text(encoding="utf-8"))
    assert record["continue_from"]["resource"] == PICKED and record["continue_from"]["step"] == 800
    assert errors("run", record) == []


def test_a_fresh_run_on_a_weights_template_passes_an_empty_value(demo_workspace, tmp_path, capsys, frozen_clock):
    make_host(tmp_path, demo_workspace, launcher=LAUNCHER + WEIGHTS_LINE)
    materialise(capsys)
    code, out = run(emit_argv(tmp_path), capsys)
    assert code == 0, out
    text = (demo_workspace / "projects" / SLUG / "runs" / NEW_RUN / "legacy" / "HOST-0003.ps1").read_text("utf-8")
    assert '$Weights = ""' in text


@pytest.mark.parametrize(("which", "code"), [("launcher", 2), ("dataset", 2), ("absolute", 3)])
def test_renderer_exit_codes_are_surfaced_and_nothing_stays(demo_workspace, tmp_path, capsys, frozen_clock, which,
                                                            code):
    launcher, dataset = LAUNCHER, DATASET
    if which == "launcher":
        launcher += "${SOMETHING_KILN_DOES_NOT_KNOW}\n"
    elif which == "dataset":
        dataset += 'extra = "${SOMETHING_KILN_DOES_NOT_KNOW}"\n'
    else:
        launcher += '$Fixed = "' + "Q" + ':/models/base.safetensors"\n'  # a literal absolute path
    paths = make_host(tmp_path, demo_workspace, launcher=launcher, dataset=dataset)
    materialise(capsys)
    got, out = run(emit_argv(tmp_path), capsys)
    assert got == code, out
    assert out[0].startswith(f"the host renderer exited {code} on ")
    assert any("SOMETHING_KILN_DOES_NOT_KNOW" in line for line in out) if code == 2 else True
    assert not (demo_workspace / "projects" / SLUG / "runs" / NEW_RUN).exists(), "the claimed run folder stayed"
    assert _written(paths) == []


def test_legacy_run_id_overrides_and_a_taken_id_is_refused(demo_workspace, tmp_path, capsys, frozen_clock):
    make_host(tmp_path, demo_workspace)
    materialise(capsys)
    code, out = run(emit_argv(tmp_path, "--legacy-run-id", "HOST-0002"), capsys)
    assert code == 2 and out[0] == "REFUSED: HOST-0002 is already in the host registry", out
    code, out = run(emit_argv(tmp_path, "--legacy-run-id", "HOST-0007"), capsys)
    assert code == 0, out
    assert "legacy run id: HOST-0007 (--legacy-run-id)" in out
    code, out = run(emit_argv(tmp_path), capsys)  # HOST-0007's files are in the generated folder now
    assert code == 0, out
    assert "legacy run id: HOST-0008 (next free in the host registry)" in out


def test_writes_outside_the_legacy_root_are_refused(demo_workspace, tmp_path, tmp_path_factory, capsys,
                                                    frozen_clock):
    paths = make_host(tmp_path, demo_workspace)
    materialise(capsys)
    elsewhere = tmp_path_factory.mktemp("elsewhere")
    toml = demo_workspace / "kiln.toml"
    toml.write_text(toml.read_text(encoding="utf-8").replace(
        f'generated_dir = "{CONFIGS}/_generated"', f"generated_dir = '{elsewhere.as_posix()}'"), encoding="utf-8")
    code, out = run(emit_argv(tmp_path), capsys)
    assert code == 2, out
    assert out[0].startswith("REFUSED: [legacy] generated_dir lies outside the legacy root")
    assert list(elsewhere.iterdir()) == [] and _written(paths) == []


def test_an_unmaterialised_version_is_refused(demo_workspace, tmp_path, capsys, frozen_clock):
    make_host(tmp_path, demo_workspace)
    code, out = run(emit_argv(tmp_path, version="0.1.0"), capsys)
    assert code == 2, out
    assert out[0] == f"REFUSED: {SLUG}/0.1.0 is not materialised; the host launcher trains on projects/{SLUG}/datasets/0.1.0"


def test_missing_legacy_keys_are_named(demo_workspace, tmp_path, capsys, frozen_clock):
    materialise(capsys)
    code, out = run(emit_argv(tmp_path), capsys)
    assert code == 2, out
    assert out[0].startswith("--emit-legacy needs [legacy] exp, renderer, launcher_template")


def test_propose_id_counts_the_main_series_and_the_generated_files():
    assert legacy_emit.propose_id(list(HOST_IDS), []) == "HOST-0003"
    assert legacy_emit.propose_id(list(HOST_IDS), ["HOST-0004.ps1", "SMOKE-09.ps1", "notes.txt"]) == "HOST-0005"
    with pytest.raises(legacy_emit.ProjectError):
        legacy_emit.propose_id(["no-number-here"], [])


def test_check_queue_names_each_problem(tmp_path):
    queue = tmp_path / "queue.json"
    queue.write_text(json.dumps({"experiment": EXP, "max_attempts": 0, "jobs": [
        {"run": "HOST-0001", "launcher": "missing.ps1", "steps": 10, "eval": {"family": "sdxl"}},
        {"run": "HOST-0001", "steps": "ten"}]}), encoding="utf-8")
    problems, jobs = legacy_emit.check_queue(queue, tmp_path, "HOST-0009")
    assert jobs == 2
    for want in ("missing key min_free_ram_gb_train", "missing key min_free_ram_gb_eval",
                 "max_attempts has the wrong type or is empty", "job 1: launcher missing (missing.ps1)",
                 "job 1: eval is missing base", "job 2: missing key launcher", "job 2: run HOST-0001 appears twice",
                 "job 2: steps must be a whole number of steps", "no job for run HOST-0009"):
        assert want in problems, (want, problems)
    queue.write_text("[1, 2", encoding="utf-8")
    assert legacy_emit.check_queue(queue, tmp_path)[0][0].startswith("queue.json does not load as JSON")
