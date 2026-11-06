"""`kiln project new <slug> --type <type>`: a project folder made from a shipped LoRA type.

The type folder `types/<type>/` is copied into `projects/<slug>/`: the intake (14 questions with example answers,
none answered), the caption rules, the checklist, the eval suite, the dataset template, the thresholds, the card
template, and the subject spec skeleton as `spec.json`. Then `project.yaml` (person-owned from here on) and
`state.json` (status `planning`, no goal) are written.

Defaults come from the type's intake: the families of question `families`, the rating of question `content`. The
trigger stays empty until the spec is approved; the slug is lowercase letters, digits and underscores.
"""

from __future__ import annotations

from pathlib import Path

from kiln._common import store

from .model import REPO, SCHEMA, SLUG, TYPES_DIR, Instance, ProjectError, now

COPIED = ("intake.yaml", "caption_rules.yaml", "checklist.yaml", "eval_suite.yaml", "dataset.toml",
          "thresholds.yaml", "card.md.tmpl")
SPEC_TEMPLATE = "spec.template.json"
SPEC = "spec.json"
TYPES = ("character-anime", "character-human", "style")  # the types project.yaml accepts (schema kiln/project/1)


def _intake_default(intake: dict, question_id: str) -> object:
    for q in intake.get("questions") or []:
        if q.get("id") == question_id:
            return q.get("default")
    return None


def create(instance: Instance, slug: str, type_id: str, *, title: str | None = None,
           families: list[str] | None = None, types_dir: Path | None = None) -> tuple[Path, dict, list[str]]:
    """Create the project; returns (folder, project.yaml data, the files copied)."""
    import yaml

    if not SLUG.match(slug or ""):
        raise ProjectError(2, f"a project slug is lowercase letters, digits and underscores, starting with a letter: "
                              f"{slug!r}")
    if type_id not in TYPES:
        raise ProjectError(2, f"a type is one of {', '.join(TYPES)}: {type_id!r}")
    source = (Path(types_dir) if types_dir else TYPES_DIR) / type_id
    if not source.is_dir():
        raise ProjectError(2, [f"the type folder {type_id} is not found", "run `kiln types check` in a checkout"])
    folder = instance.projects_dir / slug
    if (folder / "project.yaml").exists():
        raise ProjectError(2, [f"REFUSED: project {slug} exists ({instance.shown(folder)})",
                               "use it (`kiln project show --project " + slug + "`) or pick another slug"])
    imports = instance.relpath(folder / "images")
    if imports is None:
        raise ProjectError(2, "[paths] projects must lie inside the instance folder")
    intake = yaml.safe_load((source / "intake.yaml").read_text(encoding="utf-8")) or {}
    chosen = families or list(_intake_default(intake, "families") or [])
    if not chosen:
        raise ProjectError(2, "no family: pass --family <id> (the stems of families/*.yaml)")
    unknown = [f for f in chosen if (REPO / "families").is_dir() and not (REPO / "families" / f"{f}.yaml").is_file()]
    if unknown:
        raise ProjectError(2, f"unknown family {', '.join(unknown)}: the families are the stems of families/*.yaml")
    rating = _intake_default(intake, "content") or "unknown"
    copied = []
    folder.mkdir(parents=True, exist_ok=True)
    with store.lock(folder, "kiln"):
        for name in COPIED:
            if (source / name).is_file():
                store.write_bytes(folder / name, (source / name).read_bytes())
                copied.append(name)
        if (source / SPEC_TEMPLATE).is_file():
            store.write_bytes(folder / SPEC, (source / SPEC_TEMPLATE).read_bytes())
            copied.append(SPEC)
        data = {
            "slug": slug, "legacy_id": None, "title": title or slug.replace("_", " ").title(), "type": type_id,
            "created_at": now(), "trigger": None, "class_word": None,
            "content_rating": rating if rating in ("sfw", "suggestive", "explicit", "unknown") else "unknown",
            "families": chosen, "base": None, "spec": SPEC, "intake": "intake.yaml",
            "roots": {"images": "{images_root}" if instance.images_root else None, "imports": imports,
                      "legacy_output": None},
            "adopted": False, "notes": None,
        }
        store.write_record(folder / "project.yaml", SCHEMA["project"], data)
        store.write_record(folder / "state.json", SCHEMA["state"],
                           {"project": slug, "status": "planning", "phase": None, "goal": None,
                            "updated_at": now(), "legacy_id": None})
    (folder / "images").mkdir(exist_ok=True)
    return folder, data, copied
