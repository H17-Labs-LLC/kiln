"""`kiln project intake show|answer`: read the intake questions and record the operator's dictated answers.

The intake (`intake.yaml`, copied from `types/<type>/` by `kiln project new`) is the person's: every `answer` is
their words. An agent never edits the file by hand; it asks each question and records the reply with
`kiln project intake answer <question-id> "<words>"`, which writes the words verbatim through the store (atomic,
under the project's store lock) and adds a marker to the question's `note` saying who dictated the answer and
when: `[answer dictated by <who>, <time>]` (an earlier marker is replaced, the type's own note is kept).

The file is rewritten as YAML in the store's style: its leading comment block is kept, comments further down and
the hand layout (flow lists, blank lines) are not. A question whose `default` or `example` is a list takes one
item per argument; any other question takes exactly one argument. A question with `choices` takes only one of
them, so an agent never maps the operator's words to a choice itself.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from kiln._common import store

from .model import Project, ProjectError, now, why

MARK = re.compile(r"\s*\[answer dictated by [^\]]*\]")


@dataclass
class Question:
    n: int | None
    id: str
    text: str
    required: bool
    answer: object
    default: object
    choices: list[str] | None
    takes_list: bool
    applies: bool

    @property
    def state(self) -> str:
        if self.answer is not None:
            return "answered"
        if not self.applies:
            return "does not apply"
        return "MISSING (required)" if self.required else "open (the default applies)"


def intake_path(project: Project) -> Path:
    return project.folder / str(project.data.get("intake") or "intake.yaml")


def _load(project: Project) -> tuple[Path, str, dict]:
    """(path, the leading comment block, the parsed file); exit 2 when it is missing or broken."""
    import yaml

    path = intake_path(project)
    try:
        text = path.read_text(encoding="utf-8-sig")
    except OSError as exc:
        raise ProjectError(2, [f"{project.shown(path)}: cannot be read: {why(exc, path)}",
                               "project.yaml names the intake file; `kiln project lint` checks it"]) from exc
    try:
        data = yaml.safe_load(text) or {}
    except yaml.YAMLError as exc:
        raise ProjectError(2, f"{project.shown(path)}: does not parse as YAML: {why(exc, path)}") from exc
    if not isinstance(data, dict) or not isinstance(data.get("questions"), list):
        raise ProjectError(2, f"{project.shown(path)}: not an intake file (no `questions` list)")
    head = []
    for line in text.splitlines(keepends=True):
        if line.strip() and not line.lstrip().startswith("#"):
            break
        head.append(line)
    return path, "".join(head), data


def _question(raw: dict) -> Question:
    choices = raw.get("choices") if isinstance(raw.get("choices"), list) else None
    return Question(n=raw.get("n"), id=str(raw.get("id")), text=str(raw.get("question") or ""),
                    required=bool(raw.get("required")), answer=raw.get("answer"), default=raw.get("default"),
                    choices=[str(c) for c in choices] if choices else None,
                    takes_list=isinstance(raw.get("default"), list) or isinstance(raw.get("example"), list),
                    applies=raw.get("applies", True) is not False)


def questions(project: Project) -> list[Question]:
    """Every intake question in file order."""
    _, _, data = _load(project)
    return [_question(q) for q in data["questions"] if isinstance(q, dict)]


def _find(data: dict, wanted: str) -> dict:
    for raw in data["questions"]:
        if isinstance(raw, dict) and (str(raw.get("id")) == wanted or str(raw.get("n")) == wanted):
            return raw
    known = ", ".join(str(q.get("id")) for q in data["questions"] if isinstance(q, dict))
    raise ProjectError(2, [f"no intake question {wanted!r}", f"the question ids: {known}",
                           "`kiln project intake show` lists them with their questions"])


def answer(project: Project, question_id: str, words: list[str], *, by: str = "operator") -> tuple[Question, object]:
    """Record the dictated answer of one question verbatim; returns (the question, the stored answer)."""
    if not by.strip():
        raise ProjectError(2, "--by names who dictated the answer, for example operator")
    with project.lock():
        path, head, data = _load(project)
        raw = _find(data, question_id)
        question = _question(raw)
        if not question.applies:
            raise ProjectError(2, f"REFUSED: intake question {question.id} does not apply to this type: "
                                  "nothing to answer")
        if not words or any(not w.strip() for w in words):
            raise ProjectError(2, f"REFUSED: an empty answer for {question.id}: ask the operator again")
        if question.takes_list:
            value: object = list(words)
        elif len(words) == 1:
            value = words[0]
        else:
            raise ProjectError(2, [f"REFUSED: {question.id} takes one answer, got {len(words)} words as separate "
                                   "arguments", f'quote the whole answer: kiln project intake answer {question.id} '
                                   '"<the operator\'s words>"'])
        items = value if isinstance(value, list) else [value]
        if question.choices and any(item not in question.choices for item in items):
            raise ProjectError(2, [f"REFUSED: {question.id} takes one of: {', '.join(question.choices)}",
                                   "ask the operator which one they mean; never pick it for them"])
        raw["answer"] = value
        note = MARK.sub("", str(raw.get("note") or "")).strip()
        raw["note"] = f"{note} [answer dictated by {by.strip()}, {now()}]".strip()
        store.write_text(path, head + store.dump_yaml(data))
    question.answer = value
    return question, value
