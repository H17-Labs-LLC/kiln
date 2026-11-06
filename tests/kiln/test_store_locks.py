"""Read-decide-append under one store lock: a verdict, an approval, a draft or a split line is decided on the same
state it is written on, so a second session can never slip a line in between (the lock is re-entrant).

The test wraps the store's lock, its log reader and its append, records the lock depth at every read and append of
images.jsonl and captions.jsonl, and checks that each command's reads and appends sit inside one held lock."""

import csv
from contextlib import contextmanager
from pathlib import Path

import pytest
from kiln_project_helpers import run

from kiln._common import store

LOGS = ("images.jsonl", "captions.jsonl")


class Recorder:
    def __init__(self, monkeypatch: pytest.MonkeyPatch) -> None:
        self.depth = 0
        self.events: list[tuple] = []
        real_lock, real_read, real_append = store.lock, store.latest_by_key, store.append_bytes_locked

        @contextmanager
        def lock(root, tool, timeout=None):
            with real_lock(root, tool, timeout) as held:
                self.depth += 1
                if self.depth == 1:
                    self.events.append(("enter",))
                try:
                    yield held
                finally:
                    self.depth -= 1
                    if self.depth == 0:
                        self.events.append(("exit",))

        def read(path, key):
            if Path(path).name in LOGS:
                self.events.append(("read", Path(path).name, self.depth))
            return real_read(path, key)

        def append(path, data):
            if Path(path).name in LOGS:
                self.events.append(("append", Path(path).name, self.depth))
            return real_append(path, data)

        monkeypatch.setattr(store, "lock", lock)
        monkeypatch.setattr(store, "latest_by_key", read)
        monkeypatch.setattr(store, "append_bytes_locked", append)

    def reset(self) -> None:
        self.events.clear()

    def assert_one_region(self, log: str) -> None:
        """Every read and append of `log` is under the lock, and the first read and the last append share one
        held lock (no release in between)."""
        touched = [n for n, e in enumerate(self.events) if e[0] in ("read", "append") and e[1] == log]
        assert touched, f"nothing read or appended in {log}: {self.events}"
        assert all(self.events[n][2] >= 1 for n in touched), self.events
        appends = [n for n in touched if self.events[n][0] == "append"]
        assert appends, f"nothing appended to {log}"
        between = self.events[touched[0]:appends[-1]]
        assert ("exit",) not in between, f"the lock was released between the read and the append: {self.events}"


@pytest.fixture
def recorder(demo_workspace: Path, frozen_clock, monkeypatch) -> Recorder:
    return Recorder(monkeypatch)


def _rewrite(path: Path, change) -> None:
    with path.open(encoding="utf-8", newline="") as fh:
        reader = csv.DictReader(fh)
        columns, rows = list(reader.fieldnames or []), list(reader)
    for row in rows:
        change(row)
    with path.open("w", encoding="utf-8", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=columns, lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)


def test_review_apply_decides_and_writes_under_one_lock(recorder: Recorder, demo_workspace: Path, capsys):
    assert run(["project", "goal", "clear"], capsys)[0] == 0  # the legacy row waits for a verdict again
    code, out = run(["review", "sheet"], capsys)
    assert code == 0, out
    sheet = demo_workspace / out[0].split()[2]
    _rewrite(sheet, lambda row: row.update(verdict="reject"))
    recorder.reset()
    code, out = run(["review", "apply", "--sheet", str(sheet)], capsys)
    assert code == 0 and out[0].startswith("1 verdicts recorded"), out
    recorder.assert_one_region("images.jsonl")


def test_captions_approve_and_apply_hold_the_lock(recorder: Recorder, demo_workspace: Path, capsys):
    code, out = run(["captions", "sheet", "--all"], capsys)
    assert code == 0, out
    sheet = demo_workspace / out[0].split()[2]
    first: list[str] = []

    def approve_one(row: dict) -> None:
        if not first:
            first.append(row["image"])
            row.update(caption=row["draft"] + ", a second look", decision="approve")
    _rewrite(sheet, approve_one)
    recorder.reset()
    code, out = run(["captions", "approve", "--sheet", str(sheet)], capsys)
    assert code == 0, out
    reads = [e for e in recorder.events if e[0] == "read"]
    assert reads and all(e[2] >= 1 for e in reads), recorder.events
    recorder.reset()
    code, out = run(["captions", "apply"], capsys)
    assert code == 0 and out[-1].startswith("1 approved captions applied"), out
    recorder.assert_one_region("captions.jsonl")


def test_split_apply_and_label_hold_the_lock(recorder: Recorder, capsys):
    recorder.reset()
    code, out = run(["dataset", "split", "--holdout", "2", "--apply"], capsys)
    assert code == 0, out
    recorder.assert_one_region("images.jsonl")
    recorder.reset()
    code, out = run(["images", "label", "img-27daf07ca88f", "--label", "lighting:night"], capsys)
    assert code == 0, out
    recorder.assert_one_region("images.jsonl")


def test_propose_store_reads_under_the_lock(recorder: Recorder, capsys):
    recorder.reset()
    code, out = run(["captions", "propose", "--store"], capsys)
    assert code in (0, 1), out
    reads = [e for e in recorder.events if e[0] == "read" and e[1] == "captions.jsonl"]
    assert reads and all(e[2] >= 1 for e in reads), recorder.events
