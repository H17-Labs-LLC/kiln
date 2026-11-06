"""Decisions: `kiln decide open | decide | list [--open]` over `projects/<slug>/decisions/dec-NNNN.md`.

Why: some choices belong to the person, never to an agent (fresh run or continue, the holdout size, waiving a
coverage bucket). Writing the question down before asking, and the answer verbatim after, keeps the audit trail
the training needs and lets the next session see what is still open.

A decision file is YAML front matter between two `---` lines (schema `kiln/decision-frontmatter/1`), then a
Markdown body with the context. The life of one:
- `kiln decide open "<title>" --question "<q>" [--option "<o>"]...` writes the next free `dec-NNNN.md` with
  status `open` (an agent may do this: it only asks);
- `kiln decide <id> "<answer>"` records the person's answer verbatim: status `decided`, `decided_by: operator`,
  `decided_at` from the clock. A decided record is not rewritten: to change course, open a new decision with
  `--supersedes <id>`; deciding the new one marks the old one `superseded`;
- `kiln decide list` shows every decision; `list --open` shows the open ones, oldest first, with their
  question.

Every write happens under the project's store lock (`projects/<slug>/.lock`), so two sessions never claim the
same id. Example on the demo: `dec-0001` is decided, `dec-0002` is open, and the next `decide open` writes
`dec-0003`.
"""

from __future__ import annotations

import datetime as _dt
import re
from dataclasses import dataclass
from pathlib import Path

import yaml

from kiln._common import ids, store
from kiln.handoff import Refusal, schema_problems
from kiln.project.model import why

TOOL = "kiln"
SCHEMA = "kiln/decision-frontmatter/1"
SCHEMA_NAME = "decision-frontmatter"
FOLDER = "decisions"
FENCE = "---"
DEC_ID = re.compile(r"dec-[0-9]{4}")
# The order the keys are written in (the demo's order); keys a person added by hand keep their place after these.
KEYS = ("schema", "id", "legacy_id", "project", "title", "status", "question", "options", "decision", "rationale",
        "supersedes", "opened_at", "decided_at", "decided_by")
STATUSES = ("open", "decided", "superseded")
LIST_COLUMNS = ("id", "status", "title", "decision")
OPEN_COLUMNS = ("id", "opened_at", "title", "question")


@dataclass
class Decision:
    """One decision file: where it is, its front matter (a plain dict) and its Markdown body (kept verbatim)."""

    path: Path
    front: dict
    body: str

    @property
    def id(self) -> str:
        return str(self.front.get("id"))

    @property
    def status(self) -> str:
        return str(self.front.get("status"))

    def text(self) -> str:
        """The file's text: the front matter between the two fences, then the body as it was."""
        ordered = {k: self.front[k] for k in KEYS if k in self.front}
        ordered.update({k: v for k, v in self.front.items() if k not in ordered})
        return f"{FENCE}\n{store.dump_yaml(ordered)}{FENCE}\n{self.body}"


def folder_of(project_dir: Path) -> Path:
    return Path(project_dir) / FOLDER


def _plain(value: object) -> object:
    """A YAML timestamp a person typed unquoted comes back as a datetime; the record keeps it as an ISO string."""
    if isinstance(value, _dt.datetime):
        return value.isoformat(timespec="seconds")
    if isinstance(value, _dt.date):
        return value.isoformat()
    return value


def split_front(text: str) -> tuple[str, str]:
    """(front matter text, body) of a decision file; ValueError when the file does not start with front matter."""
    text = text.replace("\r\n", "\n").removeprefix("﻿")
    if not text.startswith(FENCE + "\n"):
        raise ValueError("does not start with a --- line (YAML front matter)")
    rest = text[len(FENCE) + 1:]
    if rest.startswith(FENCE + "\n") or rest == FENCE:
        return "", rest[len(FENCE) + 1:]
    end = rest.find(f"\n{FENCE}\n")
    if end == -1:
        if rest.endswith(f"\n{FENCE}"):
            return rest[: -len(FENCE)], ""
        raise ValueError("the front matter has no closing --- line")
    return rest[: end + 1], rest[end + len(FENCE) + 2:]


def read(path: Path) -> Decision:
    """Read and check one decision file; ValueError says what is wrong with it."""
    path = Path(path)
    front_text, body = split_front(path.read_text(encoding="utf-8"))
    try:
        front = yaml.safe_load(front_text) if front_text.strip() else None
    except yaml.YAMLError as exc:
        raise ValueError(f"the front matter is not YAML: {str(exc).splitlines()[0]}") from exc
    if not isinstance(front, dict):
        raise ValueError("the front matter is not a mapping")
    front = {str(k): _plain(v) for k, v in front.items()}
    if front.get("schema") != SCHEMA:
        raise ValueError(f"schema is {front.get('schema')!r}, not {SCHEMA}")
    if front.get("id") != path.stem:
        raise ValueError(f"id {front.get('id')!r} does not match the file name {path.name}")
    if front.get("status") not in STATUSES:
        raise ValueError(f"status {front.get('status')!r} is not one of {', '.join(STATUSES)}")
    problems = schema_problems(SCHEMA_NAME, front)
    if problems:
        raise ValueError("front matter " + "; ".join(problems[:3]))
    return Decision(path, front, body)


def load_all(project_dir: Path) -> tuple[list[Decision], list[str]]:
    """Every decision of the project by id, and one problem line per file that could not be read."""
    decisions, problems = [], []
    folder = folder_of(project_dir)
    if not folder.is_dir():
        return decisions, problems
    for path in sorted(folder.glob("dec-*.md")):
        if ".tmp-" in path.name:
            continue
        try:
            decisions.append(read(path))
        except (OSError, ValueError) as exc:
            problems.append(f"{FOLDER}/{path.name}: {why(exc, path)}")
    return decisions, problems


def _find(project_dir: Path, dec_id: str) -> Decision:
    if not ids_ok(dec_id):
        raise Refusal([f"not a decision id (dec-NNNN): {dec_id!r}"])
    path = folder_of(project_dir) / f"{dec_id}.md"
    if not path.is_file():
        have = ", ".join(d.id for d in load_all(project_dir)[0]) or "none"
        raise Refusal([f"no decision {dec_id} in this project (decisions: {have})"])
    try:
        return read(path)
    except ValueError as exc:
        raise Refusal([f"{FOLDER}/{path.name}: {exc}", "the file must be fixed by the person who wrote it"]) from exc


def ids_ok(dec_id: str) -> bool:
    """True for a decision id as the schema has it: `dec-` and four digits."""
    return isinstance(dec_id, str) and DEC_ID.fullmatch(dec_id) is not None


def _write(decision: Decision) -> None:
    problems = schema_problems(SCHEMA_NAME, decision.front)
    if problems:  # a bug in this module, never the person's input: refuse rather than write a bad record
        raise Refusal([f"refused to write {decision.path.name}: " + "; ".join(problems[:3])], code=1)
    store.write_text(decision.path, decision.text())


def _body(dec_id: str, context: str | None) -> str:
    closing = f'Only the person decides; `kiln decide {dec_id} "<answer>"` records the answer verbatim.'
    text = context.strip() if context and context.strip() else ""
    return "\n" + (f"{text}\n\n" if text else "") + closing + "\n"


def open_decision(project_dir: Path, slug: str, title: str, question: str, *, options: list[str] | None = None,
                  supersedes: str | None = None, context: str | None = None, tool: str = TOOL) -> Decision:
    """Write the next free `dec-NNNN.md` with status open and return it (under the project's store lock)."""
    title, question = " ".join(title.split()), question.strip()
    if not title:
        raise Refusal(["a decision needs a title"])
    if not question:
        raise Refusal(["a decision needs its question: --question \"<the question for the person>\""])
    options = [o.strip() for o in (options or []) if o.strip()]
    with store.lock(project_dir, tool):
        if supersedes is not None:
            _find(project_dir, supersedes)
        folder = folder_of(project_dir)
        folder.mkdir(parents=True, exist_ok=True)
        dec_id = ids.next_decision_id(p.stem for p in folder.glob("dec-*.md"))
        front = {"schema": SCHEMA, "id": dec_id, "legacy_id": None, "project": slug, "title": title,
                 "status": "open", "question": question, "options": options, "decision": None, "rationale": None,
                 "supersedes": supersedes, "opened_at": ids.now_iso(tool), "decided_at": None, "decided_by": None}
        decision = Decision(folder / f"{dec_id}.md", front, _body(dec_id, context))
        if decision.path.exists():
            raise Refusal([f"{dec_id}.md appeared while writing; run the command again"])
        _write(decision)
    return decision


def record_decision(project_dir: Path, dec_id: str, answer: str, *, rationale: str | None = None,
                    tool: str = TOOL) -> tuple[Decision, Decision | None]:
    """Record the person's answer to an open decision; returns (the decision, the one it superseded or None)."""
    answer = answer.strip()
    if not answer:
        raise Refusal(["the answer is empty: pass the person's words verbatim, in quotes"])
    with store.lock(project_dir, tool):
        decision = _find(project_dir, dec_id)
        if decision.status != "open":
            when = decision.front.get("decided_at") or "earlier"
            raise Refusal([f"{dec_id} is {decision.status} ({when}); a recorded answer is not rewritten",
                           f"to change course: kiln decide open \"<title>\" --question \"<q>\" --supersedes {dec_id}"])
        decision.front.update({"status": "decided", "decision": answer,
                               "rationale": rationale.strip() if rationale and rationale.strip() else None,
                               "decided_at": ids.now_iso(tool), "decided_by": "operator"})
        old = None
        target = decision.front.get("supersedes")
        if target:
            old = _find(project_dir, str(target))
            if old.status == "superseded":
                old = None
        _write(decision)
        if old is not None:
            old.front["status"] = "superseded"
            _write(old)
    return decision, old


# --- reading for the list verbs ---------------------------------------------------------------------------------

def list_rows(decisions: list[Decision]) -> list[dict]:
    """One row per decision, by id: id, status, title, decision (the answer, or None)."""
    return [{"project": d.front.get("project"), "id": d.id, "status": d.status, "title": d.front.get("title"),
             "decision": d.front.get("decision"), "opened_at": d.front.get("opened_at"),
             "decided_at": d.front.get("decided_at"), "supersedes": d.front.get("supersedes")}
            for d in sorted(decisions, key=lambda d: d.id)]


def open_rows(decisions: list[Decision]) -> list[dict]:
    """The open decisions, oldest `opened_at` first (then by id), with their question."""
    rows = [{"project": d.front.get("project"), "id": d.id, "title": d.front.get("title"),
             "question": d.front.get("question"), "opened_at": d.front.get("opened_at")}
            for d in decisions if d.status == "open"]
    return sorted(rows, key=lambda r: (str(r["opened_at"]), r["id"]))
