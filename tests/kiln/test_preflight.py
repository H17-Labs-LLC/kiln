"""`kiln train preflight <version>` on the demo: one line per gate, then next:; adopt mode adds launcher and queue."""

import json
from pathlib import Path

import pytest
from kiln_project_helpers import SLUG, run
from test_legacy_emit import emit_argv, make_host, materialise

from kiln._common import sysinfo

DEMO_RUN = "run-20300102-001"
NATIVE_GATES = ["snapshot", "triage", "captions", "split", "leaks", "materialised", "disk", "RAM", "run planned",
                "operator's go"]


@pytest.fixture
def machine(monkeypatch):
    """A machine with room: the gates must not depend on the test host's free RAM or disk."""
    state = {"ram": 20.0, "disk": 500.0}

    def ram() -> float:
        if isinstance(state["ram"], Exception):
            raise state["ram"]
        return state["ram"]

    monkeypatch.setattr(sysinfo, "free_ram_gb", ram)
    monkeypatch.setattr(sysinfo, "disk_free_gb", lambda path=".": state["disk"])
    return state


def _gates(out: list[str]) -> dict[str, tuple[str, str]]:
    """{gate: (status, detail)} from the gate lines (two-space indent, status, name)."""
    found = {}
    for line in out[1:-1]:
        status, rest = line[2:7].strip(), line[8:]
        name, detail = rest[:18].strip(), rest[18:].strip()
        found[name] = (status, detail)
    return found


def _plan(capsys, version: str = "0.2.0") -> None:
    code, out = run(["train", "plan", version, "--family", "illustrious", "--base-from-run", DEMO_RUN, "--steps",
                     "1000"], capsys)
    assert code == 0, out


def test_every_gate_one_line_then_next(demo_workspace: Path, capsys, frozen_clock, machine):
    materialise(capsys)
    _plan(capsys)
    code, out = run(["train", "preflight", "0.2.0"], capsys)
    assert code == 0, out
    assert out[0] == f"preflight {SLUG}/0.2.0 (run run-20300101-001, family illustrious)"
    gates = _gates(out)
    assert list(gates) == NATIVE_GATES and len(out) == len(NATIVE_GATES) + 2
    assert all(status == "ok" for name, (status, _) in gates.items() if name != "operator's go"), gates
    assert gates["operator's go"] == ("WAIT", 'training starts only on the operator\'s word "train"')
    assert gates["RAM"][1] == "20.0 GB free (training starts at 5.5)"
    assert out[-1] == 'next: all gates pass; ask the operator for the go and wait for the word "train"'


def test_not_materialised_and_no_run_name_the_next_command(demo_workspace: Path, capsys, machine):
    code, out = run(["train", "preflight", "0.2.0"], capsys)
    assert code == 1, out
    gates = _gates(out)
    assert gates["materialised"][0] == "TODO" and gates["run planned"][0] == "TODO"
    assert out[0] == f"preflight {SLUG}/0.2.0 (run none planned, family illustrious)"
    assert out[-1] == "next: materialised: kiln dataset materialize 0.2.0"


def test_before_the_freeze(demo_workspace: Path, capsys, machine):
    code, out = run(["train", "preflight", "0.3.0"], capsys)
    assert code == 1, out
    gates = _gates(out)
    assert list(gates) == NATIVE_GATES
    assert gates["snapshot"] == ("TODO", f"{SLUG}/0.3.0 is not frozen")
    assert gates["triage"][0] == "ok"  # the demo's one image without a disposition is a legacy row: not counted
    assert gates["split"][0] == "TODO"  # the three imports have no split yet
    assert out[-1] == "next: snapshot: kiln dataset freeze 0.3.0"
    log = demo_workspace / "projects" / SLUG / "images.jsonl"
    lines = [json.loads(x) for x in log.read_text(encoding="utf-8").splitlines() if x.strip()]
    legacy = next(x for x in lines if x.get("legacy_untriaged"))
    with log.open("a", encoding="utf-8") as fh:  # the same image, now a row someone added and nobody triaged
        fh.write(json.dumps(dict(legacy, legacy_untriaged=False, at="2030-01-05T00:00:00+00:00")) + "\n")
    code, out = run(["train", "preflight", "0.3.0"], capsys)
    assert _gates(out)["triage"] == ("WAIT", "1 image(s) without a disposition"), out


def test_ram_waits_and_an_unmeasurable_machine_fails_closed(demo_workspace: Path, capsys, frozen_clock, machine,
                                                            monkeypatch):
    materialise(capsys)
    _plan(capsys)
    machine["ram"] = 3.0
    code, out = run(["train", "preflight", "0.2.0"], capsys)
    assert code == 1 and _gates(out)["RAM"] == ("WAIT", "3.0 GB free, training starts at 5.5"), out
    machine["ram"] = sysinfo.SysinfoUnavailable("no reading")
    code, out = run(["train", "preflight", "0.2.0"], capsys)
    assert _gates(out)["RAM"] == ("WAIT", "cannot measure free RAM; set limits.skip_ram_check = true to skip")
    monkeypatch.setenv("KILN_LIMITS__SKIP_RAM_CHECK", "true")
    code, out = run(["train", "preflight", "0.2.0"], capsys)
    assert code == 0 and _gates(out)["RAM"][0] == "ok", out
    machine["disk"] = 10.0  # below the demo's limits.disk_floor_gb (20)
    code, out = run(["train", "preflight", "0.2.0"], capsys)
    assert code == 1 and _gates(out)["disk"][0] == "TODO", out


def test_adopt_mode_checks_the_launcher_and_the_queue(demo_workspace: Path, tmp_path: Path, capsys, frozen_clock,
                                                      machine):
    paths = make_host(tmp_path, demo_workspace)
    materialise(capsys)
    code, out = run(emit_argv(tmp_path), capsys)
    assert code == 0, out
    argv = ["train", "preflight", "0.2.0", "--legacy-root", str(tmp_path)]
    code, out = run(argv, capsys)
    assert code == 0, out
    gates = _gates(out)
    assert list(gates) == [*NATIVE_GATES[:-1], "launcher", "queue", "operator's go"]
    assert gates["queue"] == ("ok", "queue validated by check_queue: queue-HOST-0003.json, 1 job(s)")
    assert gates["launcher"] == ("ok", "HOST-0003.ps1 rendered for HOST-0003")
    assert gates["operator's go"][0] == "WAIT"
    queue = paths["queue_dir"] / "queue-HOST-0003.json"
    data = json.loads(queue.read_text(encoding="utf-8"))
    del data["jobs"][0]["eval"]
    queue.write_text(json.dumps(data), encoding="utf-8")
    code, out = run(argv, capsys)
    assert code == 1 and _gates(out)["queue"] == ("TODO", "queue-HOST-0003.json: job 1: missing key eval"), out
    code, out = run([*argv, "--json"], capsys)
    report = json.loads("\n".join(out))
    assert report["adopted"] is True and report["gates"][-1]["gate"] == "operator's go"
    assert report["next"].startswith("queue: plan again with --emit-legacy")
