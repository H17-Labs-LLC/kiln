"""`kiln types check [<id>]`: every LoRA type folder under `types/` has its files, and every file is valid.

Why: `kiln project new <slug> --type <id>` copies a type folder into a new project, and later stages read those
copies (intake answers, checklist, caption rules, eval suite, dataset template, thresholds, card). A missing or
broken file would surface stages later, inside somebody's project; this check catches it where the type is
written. It runs in CI and is the validator for a contributed type.

What it checks, per type folder:
- the nine type files are present (`TYPE_FILES`); any other file is a warning;
- each YAML or JSON file parses and is valid against its schema in `schemas/`. kiln's own type schemas
  (`type-*.schema.json`) must exist; the eval suite and the subject spec reuse the record schemas
  `eval-suite` and `subject-spec`, and while one of those is absent the file is only parsed (a `note:` says so);
- `dataset.toml` uses exactly the variables listed in `schemas/type-dataset-toml.schema.json` (`x-kiln-variables`),
  parses as TOML once each variable is replaced by its sample value, validates, and points every `image_dir`
  under `${DATASET_ROOT}`;
- `card.md.tmpl` carries the `{{repo_links}}` slot in its body (HTML comments do not count) and only
  well-formed `{{field}}` names;
- a few rules a schema cannot state: the `type` of each type-* file equals the folder name, the intake's
  questions are numbered 1-14 in order and unanswered, choices hold their defaults, checklist keys are unique
  and use the file's own vocabulary, the caption example has the trained length and no forbidden word.

Output: one line per problem (`<path>: <what is wrong>`), `warn:` and `note:` lines, then one summary line.
Exit 0 with no problem, 1 with at least one, 2 for a usage error (unknown type id, no types folder).
"""

from __future__ import annotations

import json
import re
import tomllib
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml
from jsonschema import Draft202012Validator
from jsonschema.exceptions import SchemaError

REPO = Path(__file__).resolve().parents[2]  # src/kiln/types_check.py -> the repository root

# type file -> the schema it is validated against (None: checked by rules in this module only)
TYPE_FILES: dict[str, str | None] = {
    "intake.yaml": "type-intake",
    "checklist.yaml": "type-checklist",
    "caption_rules.yaml": "type-caption-rules",
    "eval_suite.yaml": "eval-suite",
    "dataset.toml": "type-dataset-toml",
    "thresholds.yaml": "type-thresholds",
    "spec.template.json": "subject-spec",
    "card.md.tmpl": None,
    "README.md": None,
}
# Schemas this module owns; the other two are record schemas shared with project files and may arrive later.
OWN_SCHEMAS = frozenset({"type-intake", "type-checklist", "type-caption-rules", "type-thresholds", "type-dataset-toml"})
INTAKE_QUESTIONS = 14
REQUIRED_CARD_FIELDS = ("repo_links",)
MAX_ERRORS_PER_FILE = 20

TYPE_ID = re.compile(r"^[a-z][a-z0-9-]*$")
VARIABLE = re.compile(r"\$\{([^}]*)\}")
CARD_FIELD = re.compile(r"\{\{(.*?)\}\}")
HTML_COMMENT = re.compile(r"<!--.*?-->", re.S)
FIELD_NAME = re.compile(r"^[a-z][a-z0-9_]*$")


@dataclass
class Report:
    """What one check found. Problems fail the check; warnings fail it only under --strict; notes never do."""

    problems: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    checked: list[str] = field(default_factory=list)


class _Schemas:
    """Loads each schema once; remembers a missing or broken one so it is reported once, not per type."""

    def __init__(self, schemas_dir: Path, report: Report) -> None:
        self.dir = schemas_dir
        self.report = report
        self._cache: dict[str, Draft202012Validator | None] = {}

    def path(self, name: str) -> Path:
        return self.dir / f"{name}.schema.json"

    def label(self, name: str) -> str:
        return f"{self.dir.name}/{name}.schema.json"

    def get(self, name: str) -> Draft202012Validator | None:
        """The validator for `name`, or None when the schema is absent or broken (reported on first use)."""
        if name in self._cache:
            return self._cache[name]
        validator: Draft202012Validator | None = None
        path = self.path(name)
        if not path.is_file():
            if name in OWN_SCHEMAS:
                self.report.problems.append(f"{self.label(name)}: missing")
            else:
                self.report.notes.append(f"note: {self.label(name)} not present yet; files that use it were parsed but not schema-validated")
        else:
            try:
                schema = json.loads(path.read_text(encoding="utf-8"))
                Draft202012Validator.check_schema(schema)
                validator = Draft202012Validator(schema)
            except (OSError, ValueError) as exc:
                self.report.problems.append(f"{self.label(name)}: does not parse as JSON: {_first_line(exc)}")
            except SchemaError as exc:
                self.report.problems.append(f"{self.label(name)}: not a valid JSON Schema: {_short(exc.message)}")
        self._cache[name] = validator
        return validator


def _first_line(exc: BaseException) -> str:
    text = str(exc).strip()
    return _short(text.splitlines()[0] if text else type(exc).__name__)


def _short(text: str, limit: int = 160) -> str:
    text = " ".join(text.split())
    return text if len(text) <= limit else text[: limit - 3] + "..."


def _where(error: Any) -> str:
    path = "/".join(str(p) for p in error.absolute_path)
    return path or "top level"


def _validate(data: Any, validator: Draft202012Validator, rel: str, report: Report) -> None:
    errors = sorted(validator.iter_errors(data), key=lambda e: [str(p) for p in e.absolute_path])
    for error in errors[:MAX_ERRORS_PER_FILE]:
        report.problems.append(f"{rel}: {_where(error)}: {_short(error.message)}")
    if len(errors) > MAX_ERRORS_PER_FILE:
        report.problems.append(f"{rel}: {len(errors) - MAX_ERRORS_PER_FILE} more schema error(s) not shown")


def _load_yaml(path: Path, rel: str, report: Report) -> Any:
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError) as exc:
        report.problems.append(f"{rel}: cannot be read: {_first_line(exc)}")
        return None
    except yaml.YAMLError as exc:
        report.problems.append(f"{rel}: does not parse as YAML: {_first_line(exc)}")
        return None
    if not isinstance(data, dict):
        report.problems.append(f"{rel}: top level must be a mapping")
        return None
    return data


def _load_json(path: Path, rel: str, report: Report) -> Any:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError) as exc:
        report.problems.append(f"{rel}: cannot be read: {_first_line(exc)}")
        return None
    except ValueError as exc:
        report.problems.append(f"{rel}: does not parse as JSON: {_first_line(exc)}")
        return None
    if not isinstance(data, dict):
        report.problems.append(f"{rel}: top level must be an object")
        return None
    return data


# --- rules a schema cannot state --------------------------------------------------------------------------------

def _rule_type_matches(data: dict, type_id: str, rel: str, report: Report) -> None:
    value = data.get("type")
    if isinstance(value, str) and value != type_id:
        report.problems.append(f"{rel}: type is {value!r} but the folder is {type_id!r}")


def _rules_intake(data: dict, rel: str, report: Report) -> None:
    questions = [q for q in data.get("questions") or [] if isinstance(q, dict)]
    numbers = [q.get("n") for q in questions]
    if numbers != list(range(1, INTAKE_QUESTIONS + 1)):
        report.problems.append(f"{rel}: questions must be numbered 1 to {INTAKE_QUESTIONS} in order")
    ids = [q.get("id") for q in questions]
    for dup in sorted({i for i in ids if isinstance(i, str) and ids.count(i) > 1}):
        report.problems.append(f"{rel}: question id {dup!r} appears more than once")
    for q in questions:
        n = q.get("n")
        if q.get("answer") is not None:
            report.problems.append(f"{rel}: question {n}: answer must be null in a shipped type; answers belong in a project's copy")
        choices = q.get("choices")
        default = q.get("default")
        if isinstance(choices, list) and default is not None:
            values = default if isinstance(default, list) else [default]
            for value in values:
                if value not in choices:
                    report.problems.append(f"{rel}: question {n}: default {value!r} is not one of its choices")
        if q.get("required") is True and default is not None:
            report.warnings.append(f"warn: {rel}: question {n} is required and also has a default; the default is never used")


def _rules_checklist(data: dict, rel: str, report: Report) -> None:
    vocabulary = data.get("vocabulary") if isinstance(data.get("vocabulary"), dict) else {}
    allowed = {"viewpoint": set(vocabulary.get("viewpoints") or []), "framing": set(vocabulary.get("framings") or [])}
    entries = [e for e in (data.get("items") or []) + (data.get("review_only") or []) if isinstance(e, dict)]
    keys = [e.get("key") for e in entries]
    for dup in sorted({k for k in keys if isinstance(k, str) and keys.count(k) > 1}):
        report.problems.append(f"{rel}: key {dup!r} appears more than once across items and review_only")
    for entry in entries:
        skip = entry.get("skip_when") if isinstance(entry.get("skip_when"), dict) else {}
        for dimension, values in skip.items():
            for value in values if isinstance(values, list) else []:
                if dimension in allowed and value not in allowed[dimension]:
                    report.problems.append(f"{rel}: {entry.get('key')}: skip_when {dimension} {value!r} is not in this file's vocabulary")


def _words(text: str) -> list[str]:
    return [w for w in text.split() if w.strip(",.;:")]


def _rules_caption(data: dict, rel: str, report: Report) -> None:
    levels = data.get("levels") if isinstance(data.get("levels"), dict) else {}
    level = levels.get(data.get("train_level")) if isinstance(levels, dict) else None
    example = data.get("example") if isinstance(data.get("example"), dict) else {}
    caption = example.get("caption")
    if not isinstance(caption, str):
        return
    bounds = level.get("words") if isinstance(level, dict) else None
    count = len(_words(caption))
    if isinstance(bounds, list) and len(bounds) == 2 and all(isinstance(b, int) for b in bounds):
        low, high = bounds
        if not low <= count <= high:
            report.problems.append(f"{rel}: example caption has {count} words; the trained level allows {low}-{high}")
    forbidden = data.get("forbidden") if isinstance(data.get("forbidden"), dict) else {}
    lowered = f" {' '.join(caption.lower().replace(',', ' ').split())} "
    for group, words in forbidden.items():
        for word in words if isinstance(words, list) else []:
            if isinstance(word, str) and f" {word.lower()} " in lowered:
                report.problems.append(f"{rel}: example caption contains {word!r} from forbidden.{group}")


def _rules_thresholds(data: dict, rel: str, report: Report) -> None:
    groups = data.get("groups") if isinstance(data.get("groups"), dict) else {}
    for group, entries in groups.items():
        for name, entry in (entries.items() if isinstance(entries, dict) else []):
            value = entry.get("value") if isinstance(entry, dict) else None
            if isinstance(value, list) and len(value) == 2 and all(isinstance(v, (int, float)) for v in value):
                if value[0] > value[1]:
                    report.problems.append(f"{rel}: {group}.{name}: range {value} runs backwards")


def _rules_eval_suite(data: dict, rel: str, report: Report) -> None:
    prompts = data.get("prompts")
    if not isinstance(prompts, list) or not prompts:
        report.problems.append(f"{rel}: prompts must be a non-empty list")
        return
    ids = [p.get("id") for p in prompts if isinstance(p, dict)]
    for dup in sorted({i for i in ids if isinstance(i, str) and ids.count(i) > 1}):
        report.problems.append(f"{rel}: prompt id {dup!r} appears more than once")


# --- the dataset template -----------------------------------------------------------------------------------------

def _toml_literal(value: Any) -> str:
    """The text that replaces a variable: raw for strings (they sit inside quotes), TOML spelling otherwise."""
    if isinstance(value, bool):
        return "true" if value else "false"
    return str(value)


def _check_dataset_toml(path: Path, rel: str, schemas: _Schemas, report: Report) -> None:
    validator = schemas.get("type-dataset-toml")
    if validator is None:
        return
    variables: dict[str, Any] = validator.schema.get("x-kiln-variables") or {}
    if not variables:
        report.problems.append(f"{schemas.label('type-dataset-toml')}: x-kiln-variables is missing or empty")
        return
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        report.problems.append(f"{rel}: cannot be read: {_first_line(exc)}")
        return
    live = "\n".join(line for line in text.splitlines() if not line.lstrip().startswith("#"))
    used = set(VARIABLE.findall(live))
    for name in sorted(used - set(variables)):
        report.problems.append(f"{rel}: unknown variable ${{{name}}}; allowed: {', '.join(sorted(variables))}")
    for name in sorted(set(variables) - used):
        report.problems.append(f"{rel}: variable ${{{name}}} is never used")
    rendered = text
    for name, spec in variables.items():
        rendered = rendered.replace(f"${{{name}}}", _toml_literal(spec.get("sample")))
    try:
        data = tomllib.loads(rendered)
    except tomllib.TOMLDecodeError as exc:
        report.problems.append(f"{rel}: does not parse as TOML once the variables are filled: {_first_line(exc)}")
        return
    _validate(data, validator, rel, report)
    root = str((variables.get("DATASET_ROOT") or {}).get("sample", ""))
    for i, dataset in enumerate(data.get("datasets") or []):
        subsets = dataset.get("subsets") if isinstance(dataset, dict) else None
        for j, subset in enumerate(subsets if isinstance(subsets, list) else []):
            image_dir = subset.get("image_dir") if isinstance(subset, dict) else None
            if isinstance(image_dir, str) and root and not image_dir.startswith(root + "/"):
                report.problems.append(f"{rel}: datasets/{i}/subsets/{j}: image_dir must start with ${{DATASET_ROOT}}/")


# --- the card and the readme --------------------------------------------------------------------------------------

def _check_card(path: Path, rel: str, report: Report) -> None:
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        report.problems.append(f"{rel}: cannot be read: {_first_line(exc)}")
        return
    if text.count("{{") != text.count("}}"):
        report.problems.append(f"{rel}: unbalanced {{{{ and }}}}")
    body = HTML_COMMENT.sub("", text)  # a comment may name a field; only the card's body counts
    names = [m.strip() for m in CARD_FIELD.findall(body)]
    for name in sorted({n for n in names if not FIELD_NAME.match(n)}):
        report.problems.append(f"{rel}: field {{{{{name}}}}} is not a lowercase_with_underscores name")
    for name in REQUIRED_CARD_FIELDS:
        if name not in names:
            report.problems.append(f"{rel}: the {{{{{name}}}}} slot is missing")


def _check_readme(path: Path, rel: str, report: Report) -> None:
    try:
        lines = [line for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    except (OSError, UnicodeDecodeError) as exc:
        report.problems.append(f"{rel}: cannot be read: {_first_line(exc)}")
        return
    if not lines:
        report.problems.append(f"{rel}: empty")
    elif not lines[0].startswith("# "):
        report.problems.append(f"{rel}: must start with a '# ' heading")


# --- one type, all types ------------------------------------------------------------------------------------------

_RULES: dict[str, Callable[[dict, str, Report], None]] = {
    "intake.yaml": _rules_intake,
    "checklist.yaml": _rules_checklist,
    "caption_rules.yaml": _rules_caption,
    "thresholds.yaml": _rules_thresholds,
    "eval_suite.yaml": _rules_eval_suite,
}


def check_type(folder: Path, schemas: _Schemas, report: Report) -> None:
    """Check one type folder; findings go into `report`."""
    type_id = folder.name
    base = folder.parent.name
    if not TYPE_ID.match(type_id):
        report.problems.append(f"{base}/{type_id}: a type id is lowercase letters, digits and hyphens, starting with a letter")
    for extra in sorted(p.name for p in folder.iterdir() if p.name not in TYPE_FILES and not p.name.startswith(".")):
        report.warnings.append(f"warn: {base}/{type_id}/{extra}: not a type file; types check ignores it")
    for name, schema_name in TYPE_FILES.items():
        path = folder / name
        rel = f"{base}/{type_id}/{name}"
        if not path.is_file():
            report.problems.append(f"{rel}: missing")
            continue
        if name == "dataset.toml":
            _check_dataset_toml(path, rel, schemas, report)
            continue
        if name == "card.md.tmpl":
            _check_card(path, rel, report)
            continue
        if name == "README.md":
            _check_readme(path, rel, report)
            continue
        data = _load_json(path, rel, report) if name.endswith(".json") else _load_yaml(path, rel, report)
        if data is None:
            continue
        validator = schemas.get(schema_name) if schema_name else None
        if validator is not None:
            _validate(data, validator, rel, report)
        if schema_name in OWN_SCHEMAS:  # the record schemas govern their own `type` field
            _rule_type_matches(data, type_id, rel, report)
        rule = _RULES.get(name)
        if rule is not None:
            rule(data, rel, report)
    report.checked.append(type_id)


def type_folders(types_dir: Path) -> list[Path]:
    """The type folders under `types_dir`: every directory not starting with '.' or '_'."""
    return sorted(p for p in types_dir.iterdir() if p.is_dir() and not p.name.startswith((".", "_")))


def check_types(types_dir: Path, schemas_dir: Path, type_id: str | None = None) -> Report:
    """Check every type under `types_dir` (or only `type_id`). Raises KeyError for an unknown `type_id`."""
    report = Report()
    folders = type_folders(types_dir)
    if type_id is not None:
        folders = [f for f in folders if f.name == type_id]
        if not folders:
            raise KeyError(type_id)
    schemas = _Schemas(schemas_dir, report)
    for name in sorted(OWN_SCHEMAS):  # report a missing or broken own schema even if no file uses it
        schemas.get(name)
    for folder in folders:
        check_type(folder, schemas, report)
    return report


def print_report(report: Report, strict: bool, out: Callable[[str], None] = print) -> int:
    """Print the findings and the summary line; return the exit code (0 ok, 1 problems)."""
    problems = list(report.problems)
    if strict:
        problems += [w.removeprefix("warn: ") for w in report.warnings]
    _emit(problems, out)
    if not strict:
        _emit(report.warnings, out)
    _emit(report.notes, out)
    count = len(report.checked)
    if problems:
        out(f"FAIL: {len(problems)} problem(s) in {count} type(s)")
        return 1
    tail = f", {len(report.warnings)} warning(s)" if report.warnings else ""
    out(f"ok: {count} type(s) checked: {', '.join(report.checked) or 'none'}{tail}")
    return 0


def _emit(lines: Iterable[str], out: Callable[[str], None]) -> None:
    for line in lines:
        out(line)


def run(type_id: str | None = None, *, types_dir: Path | None = None, schemas_dir: Path | None = None,
        strict: bool = False, out: Callable[[str], None] = print) -> int:
    """The `kiln types check` command: check the shipped types (or `types_dir`) and return the exit code."""
    types_dir = types_dir or REPO / "types"
    schemas_dir = schemas_dir or REPO / "schemas"
    if not types_dir.is_dir():
        out("no types folder found: pass --dir <folder holding the type folders>")
        return 2
    try:
        report = check_types(types_dir, schemas_dir, type_id)
    except KeyError:
        known = ", ".join(f.name for f in type_folders(types_dir)) or "none"
        out(f"unknown type: {type_id}; known: {known}")
        return 2
    return print_report(report, strict, out)
