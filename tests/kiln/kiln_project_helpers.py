"""Helpers for the project-store tests: run the CLI in-process, read and validate a project's records.

Not a test module (no test_ prefix); the test files beside it import it by name (pytest puts this folder on
sys.path for them).
"""

from __future__ import annotations

import json
from functools import cache
from pathlib import Path

import yaml
from jsonschema import Draft202012Validator

from kiln import cli

REPO = Path(__file__).resolve().parents[2]
SCHEMAS = REPO / "schemas"
SLUG = "demo_robot"


def run(argv: list[str], capsys) -> tuple[int, list[str]]:
    """Run `kiln <argv>` in-process; returns (exit code, stdout lines). stderr (the clock line) is dropped."""
    capsys.readouterr()
    try:
        code = cli.main(argv)
    except SystemExit as exc:  # argparse usage errors and store.Held
        code = exc.code if isinstance(exc.code, int) else 2
    out = capsys.readouterr().out
    return code, out.splitlines()


def next_lines(capsys, *extra: str) -> list[str]:
    code, lines = run(["next", *extra], capsys)
    assert code == 0, lines
    return lines


def three(stage: str, command: str) -> list[str]:
    """The three lines `kiln next` prints for a stage: `read:` names the runbook or says it is planned, and a
    command that names something not built yet ends with `# planned: <task>`."""
    from kiln import planned

    written = (REPO / "docs" / "runbooks" / f"{stage}.md").is_file()
    read = f"docs/runbooks/{stage}.md" if written else f"{stage} is planned"
    return [f"stage: {stage}", f"read: {read}", f"next: {planned.mark(command)}"]


@cache
def validator(name: str) -> Draft202012Validator:
    return Draft202012Validator(json.loads((SCHEMAS / f"{name}.schema.json").read_text(encoding="utf-8")))


def errors(name: str, data: object) -> list[str]:
    return [f"{'.'.join(map(str, e.absolute_path)) or 'top'}: {e.message}" for e in validator(name).iter_errors(data)]


def read_jsonl(path: Path) -> list[dict]:
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def assert_project_valid(project: Path) -> None:
    """Every record and log line of a project folder validates against its frozen schema."""
    problems = []
    problems += [f"project.yaml {e}" for e in errors("project", yaml.safe_load((project / "project.yaml").read_text("utf-8")))]
    if (project / "state.json").exists():
        problems += [f"state.json {e}" for e in errors("state", json.loads((project / "state.json").read_text("utf-8")))]
    for n, line in enumerate(read_jsonl(project / "images.jsonl"), 1):
        problems += [f"images.jsonl:{n} {e}" for e in errors("image-event", line)]
    for n, line in enumerate(read_jsonl(project / "captions.jsonl"), 1):
        problems += [f"captions.jsonl:{n} {e}" for e in errors("caption-event", line)]
    for path in sorted((project / "snapshots").glob("*.json")):
        problems += [f"{path.name} {e}" for e in errors("snapshot", json.loads(path.read_text("utf-8")))]
    for path in sorted((project / "datasets").glob("*/_meta/materialized-*.json")):
        problems += [f"{path.name} {e}" for e in errors("materialized", json.loads(path.read_text("utf-8")))]
    assert not problems, "\n".join(problems)


def latest_images(project: Path) -> dict[str, dict]:
    out: dict[str, dict] = {}
    for line in read_jsonl(project / "images.jsonl"):
        out[line["id"]] = line
    return out
