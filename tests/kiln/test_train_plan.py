"""`kiln train plan` on the demo: run.json planned with the base's licence copied and continue_from recorded."""

import hashlib
import json
from pathlib import Path

import pytest
from kiln_project_helpers import SLUG, errors, run

from kiln.train import plan as plan_mod

DEMO_RUN = "run-20300102-001"
NEW_RUN = "run-20300101-001"  # the first run id of the frozen day
PICKED = "cf56db17414bcb55"  # the demo run's picked step-800 checkpoint


def _run_json(home: Path, run_id: str = NEW_RUN) -> dict:
    return json.loads((home / "projects" / SLUG / "runs" / run_id / "run.json").read_text(encoding="utf-8"))


def _demo_run(home: Path) -> dict:
    return _run_json(home, DEMO_RUN)


def test_plan_from_run_copies_the_base_and_its_licence(demo_workspace: Path, capsys, frozen_clock):
    code, out = run(["train", "plan", "0.2.0", "--family", "illustrious", "--base-from-run", DEMO_RUN,
                     "--steps", "1000"], capsys)
    assert code == 0, out
    record = _run_json(demo_workspace)
    assert errors("run", record) == []
    assert record["status"] == "planned" and record["snapshot"] == f"{SLUG}/0.2.0"
    assert record["planned_at"] == "2030-01-01T00:00:00+00:00"
    demo_base = _demo_run(demo_workspace)["base"]
    assert record["base"]["licence"] == demo_base["licence"]
    assert record["base"]["sha256"] == demo_base["sha256"] and record["base"]["from_run"] == DEMO_RUN
    assert record["continue_from"] is None
    assert record["steps"] == {"planned": 1000, "done": None, "save_every": 200, "validate_every": 200}
    assert record["estimate"]["wall_clock_minutes"] == 110  # 2 min load + 1000 x 6.5 s, inherited from sdxl
    assert record["eval_suite"] == "core" and record["checkpoints"] == []
    assert record["adapter"] == {"algo": "lora", "target_preset": None, "rank": 32, "alpha": 32}
    assert out[0] == (f"planned {NEW_RUN}: {SLUG}/0.2.0 on illustrious, 1000 steps (save every 200, validate "
                      "every 200)")
    assert "licence Demo Open Licence (unverified)" in out[1]
    assert out[2] == "  continue   none: a fresh run from the base"
    assert out[3].startswith("  wall clock about 110 min (1.8 h): families/sdxl.yaml (inherited by illustrious)")
    assert out[4] == "  host RAM   5.5 GB to train, 8.5 GB for the eval render (includes the ComfyUI server)"
    assert out[-1] == "next: kiln train preflight 0.2.0"


def test_continue_from_a_checkpoint_lands_in_run_json(demo_workspace: Path, capsys, frozen_clock):
    code, out = run(["train", "plan", "0.2.0", "--family", "illustrious", "--base-from-run", DEMO_RUN,
                     "--continue-from", PICKED, "--steps", "600", "--save-every", "100"], capsys)
    assert code == 0, out
    record = _run_json(demo_workspace)
    assert errors("run", record) == []
    picked = next(c for c in _demo_run(demo_workspace)["checkpoints"] if c["step"] == 800)
    assert record["continue_from"] == {"resource": PICKED, "path": picked["file"], "sha256": picked["sha256"],
                                       "from_run": DEMO_RUN, "step": 800}
    assert record["steps"]["save_every"] == 100 and record["steps"]["validate_every"] == 100
    assert any(line.startswith(f"  continue   {PICKED}") and "--network_weights" in line for line in out), out
    family, _ = plan_mod.load_family("illustrious")
    argv = plan_mod.native_args(record, family, dataset_config="dataset.toml", base_file="base.safetensors",
                                output_dir="output", continue_file="lora.safetensors")
    assert argv[argv.index("--network_weights") + 1] == "lora.safetensors"
    assert argv[argv.index("--max_train_steps") + 1] == "600"
    assert argv[argv.index("--save_every_n_steps") + 1] == "100"
    assert "--max_train_epochs" not in argv and "--mem_eff_attn" not in argv
    fresh = dict(record, continue_from=None)
    assert "--network_weights" not in plan_mod.native_args(fresh, family, dataset_config="d", base_file="b",
                                                           output_dir="o")


def test_continue_from_a_file_and_base_from_a_file(demo_workspace: Path, capsys, frozen_clock):
    lora = demo_workspace / "loras" / "demo-lora.safetensors"
    base = demo_workspace / "models" / "checkpoints" / "demo-base.safetensors"
    for path, body in ((lora, b"demo lora bytes"), (base, b"demo base bytes")):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(body)
    code, out = run(["train", "plan", "0.2.0", "--family", "illustrious", "--base", str(base),
                     "--continue-from", str(lora), "--steps", "200"], capsys)
    assert code == 0, out
    record = _run_json(demo_workspace)
    assert errors("run", record) == []
    sha = hashlib.sha256(b"demo lora bytes").hexdigest()
    assert record["continue_from"] == {"resource": sha[:16], "path": "loras/demo-lora.safetensors", "sha256": sha,
                                       "from_run": None, "step": None}
    assert record["base"]["sha256"] == hashlib.sha256(b"demo base bytes").hexdigest()
    assert record["base"]["licence"]["status"] == "unknown"  # no resource record: never invented
    assert any(line.startswith("  warn: the base's licence is unknown") for line in out), out


def test_base_by_resource_id_reuses_a_known_base(demo_workspace: Path, capsys, frozen_clock):
    resource = _demo_run(demo_workspace)["base"]["resource"]
    code, out = run(["train", "plan", "0.2.0", "--family", "illustrious", "--base", resource, "--steps", "300"],
                    capsys)
    assert code == 0, out
    record = _run_json(demo_workspace)
    assert record["base"]["licence"] == _demo_run(demo_workspace)["base"]["licence"]
    assert record["base"]["from_run"] is None


@pytest.mark.parametrize(("argv", "first"), [
    (["0.9.0", "--family", "illustrious", "--base-from-run", DEMO_RUN, "--steps", "10"], "no snapshot demo_robot/0.9.0"),
    (["0.2.0", "--family", "moss", "--base-from-run", DEMO_RUN, "--steps", "10"], "REFUSED: family moss"),
    (["0.2.0", "--family", "nosuch", "--base-from-run", DEMO_RUN, "--steps", "10"], "no family 'nosuch'"),
    (["0.2.0", "--family", "illustrious", "--base", "0123456789abcdef", "--steps", "10"], "unknown base 0123456789abcdef"),
    (["0.2.0", "--family", "illustrious", "--base-from-run", "run-20300109-001", "--steps", "10"], "no run run-20300109-001"),
    (["0.2.0", "--family", "illustrious", "--base-from-run", DEMO_RUN, "--continue-from", "fedcba9876543210",
      "--steps", "10"], "unknown LoRA fedcba9876543210"),
])
def test_refusals_write_nothing(demo_workspace: Path, capsys, frozen_clock, argv, first):
    code, out = run(["train", "plan", *argv], capsys)
    assert code == 2, out
    assert out[0].startswith(first), out
    assert not (demo_workspace / "projects" / SLUG / "runs" / NEW_RUN).exists()


def test_usage_errors(demo_workspace: Path, capsys):
    both = ["train", "plan", "0.2.0", "--family", "illustrious", "--base", "x", "--base-from-run", DEMO_RUN,
            "--steps", "10"]
    assert run(both, capsys)[0] == 2
    assert run(["train", "plan", "0.2.0", "--family", "illustrious", "--base-from-run", DEMO_RUN, "--steps", "0"],
               capsys)[0] == 2
    code, out = run(["train", "plan", "0.2.0", "--family", "illustrious", "--base-from-run", DEMO_RUN, "--steps",
                     "10", "--legacy-run-id", "X1"], capsys)
    assert code == 2 and out == ["--legacy-run-id and --legacy-root go with --emit-legacy"]


def test_two_plans_get_two_ids(demo_workspace: Path, capsys, frozen_clock):
    for _ in range(2):
        code, out = run(["train", "plan", "0.2.0", "--family", "pony", "--base-from-run", DEMO_RUN, "--steps", "50"],
                        capsys)
        assert code == 0, out
    assert _run_json(demo_workspace, "run-20300101-002")["family"] == "pony"


@pytest.mark.parametrize("action", ["launch", "queue", "smoke", "tune"])
def test_later_train_actions_say_planned(capsys, action):
    for argv in (["train", action], ["train", action, "--help"]):
        code, out = run(argv, capsys)
        assert code == 2 and out[0].startswith("planned: session-2 ("), (argv, out)
