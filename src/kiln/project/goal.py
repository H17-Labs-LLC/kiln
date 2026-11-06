"""`kiln project goal set|clear`: the person's current intent, which `kiln next` routes by.

A goal is human-owned intent stored in `state.json`: "build version <new> from version <old> plus the kept images
of easel run <run-id>". With a goal set, `next` routes 03 -> 05 -> 06 -> 07 -> 08 for that version and ignores
legacy rows nobody triaged; `dataset freeze <new>` defaults to `--from <old>`. On an adopted project the goal is
step 1 (runbook 90). Clearing it returns `next` to deriving the stage from the files.
"""

from __future__ import annotations

import re

from . import snapshot as snapshot_mod
from .model import Project, ProjectError, check_version, now

RUN_ID = re.compile(r"^run-\d{8}-[a-z0-9][a-z0-9_-]*$")


def set_goal(project: Project, new_version: str, *, from_version: str | None = None,
             from_easel_run: str | None = None, note: str | None = None, only_new: bool = False) -> dict:
    """Write the goal; `from_version` defaults to the latest snapshot (none with `only_new`)."""
    check_version(new_version)
    if snapshot_mod.snapshot_path(project, new_version).exists():
        raise ProjectError(2, f"REFUSED: version {new_version} is already frozen; a goal names a new version")
    if from_version and only_new:
        raise ProjectError(2, "--from-version and --only-new exclude each other")
    if from_version:
        check_version(from_version)
        snapshot_mod.load(project, from_version)  # exit 2 when it does not exist
    elif not only_new:
        from_version = snapshot_mod.latest_version(project)
    if from_easel_run and not RUN_ID.match(from_easel_run):
        raise ProjectError(2, f"an easel run id looks like run-20300101-001: {from_easel_run!r}")
    goal = {"kind": "new-version", "new_version": new_version, "from_version": from_version,
            "from_easel_run": from_easel_run, "set_at": now(), "note": note}
    with project.lock():  # read, change and write state.json as one step
        state = project.state()
        state["goal"] = goal
        if state.get("status") == "planning":
            state["status"] = "curating"
        project.write_state(state)
    return goal


def clear_goal(project: Project) -> dict | None:
    """Remove the goal; returns the goal that was set (None when there was none)."""
    with project.lock():
        state = project.state()
        old = state.get("goal")
        state["goal"] = None
        project.write_state(state)
    return old
