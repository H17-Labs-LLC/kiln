"""`kiln captions propose [--store] [--all]`: a draft caption per kept image, from what its source asked for.

Nobody looks at an image to draft these. The basis is, in order:
1. `prompt`: the image's `caption_basis` (an easel import keeps the run's prompt there, a jobs-file image its
   scene), else the first line of `source.prompt`. A derived image (crop, flip, edit) whose own basis says nothing
   descriptive inherits its parent's, and the proposal names the derivation (`via`) as a problem to check;
2. `note`: the operator's own words in the image's `notes` (from `--notes` at import, or `images label --note`),
   with review remarks left out ("too dark", "the hand is wrong");
3. `none`: nothing to go on; the proposal says so and is never stored.

A prompt says what was asked for, not what is there, so only its description becomes caption text: clauses that
instruct the generator ("keep the pose", "Image A"), negations ("no hat"), talk about the frame or the sheet, and
anything about the fixed design (`not_in_captions`: the trigger learns that) are dropped; the trigger, the class
word and the style tag are put in their places; the view and framing words come from the image's labels when the
text lacks them; the default outfit tag is added where the image is labelled `outfit:default`. Then the caption
rules' lint runs (`kiln captions lint`).

`--store` keeps the clean proposals as `draft` lines in `captions.jsonl` (`source: kiln:captions propose`,
`basis: prompt`, or `manual` for a note's words): clean means no lint problem except being under the word minimum
(a true short caption beats a padded one; padding would mean inventing), no derivation to check and no operator
note to merge. An approved caption is never redrafted; a draft is redrafted only with `--all`. Without `--store`
nothing is written. The operator still approves every caption in a sheet (`kiln captions sheet`).
"""

from __future__ import annotations

import dataclasses
import re
from collections.abc import Mapping
from dataclasses import dataclass, field

from kiln.project import captions as captions_mod
from kiln.project import images as images_mod
from kiln.project.model import SCHEMA, Project, event_line

from . import base

SOURCE = "kiln:captions propose"
BY = "kiln captions propose --store"
_MAX_CHAIN = 12

# Clause filters (whole clauses are dropped, never words inside one).
INSTRUCTION = re.compile(
    r"\b(keep|keeps|keeping|change|changes|fix|fixes|correct|redraw|preserve|match|make sure|ensure|do not|don't|"
    r"never|must|should|image [a-f]|references?|checklist|specification|edit|replace|remove)\b", re.I)
NEGATION = re.compile(r"\b(no|not|without|nothing|none)\b", re.I)
META = re.compile(
    r"\b(one figure|one image|the image|the picture|character sheet|turnaround|camera|centred|centered|framed|"
    r"symmetrical|the subject|the character|aspect ratio|resolution)\b", re.I)
EVALUATIVE = re.compile(
    r"\b(good|bad|great|fine|ok|okay|nice|perfect|wrong|weird|strange|odd|too|should|messed|broken|malformed|"
    r"second look|on second thought)\b", re.I)
FRAMING_SEEN = re.compile(r"\b(close-up|close up|head and shoulders|upper body|waist up|medium shot|full body|"
                          r"full-body|wide shot|portrait)\b", re.I)
VIEW_SEEN = re.compile(r"\b(view|profile|from behind|from side|from the side)\b", re.I)
OUTFIT_SEEN = re.compile(r"\b(outfit|wearing|wears|dress|jacket|coat|cloak|sweater|shirt|skirt|armou?r|uniform|"
                         r"hoodie|robe|vest|suit)\b", re.I)


@dataclass
class Proposal:
    image: str
    basis: str
    text: str
    problems: list[str] = field(default_factory=list)
    via: list[str] = field(default_factory=list)
    storable: bool = False
    stored: bool = False


@dataclass
class Result:
    proposals: list[Proposal] = field(default_factory=list)
    approved_left: int = 0
    drafts_left: int = 0
    unchanged: int = 0

    @property
    def stored(self) -> int:
        return sum(p.stored for p in self.proposals)

    def count(self, basis: str) -> int:
        return sum(p.basis == basis for p in self.proposals)


def clauses(text: str) -> list[str]:
    return [c.strip().strip(".").strip() for c in re.split(r"[.;:,\n]\s*", text or "") if c.strip().strip(".")]


def _fixed(words: list[str]) -> re.Pattern | None:
    words = [w for w in words if w]
    return re.compile(r"\b(" + "|".join(re.escape(w) for w in words) + r")\b", re.I) if words else None


def prompt_fragments(text: str, *, skip: set[str], fixed: re.Pattern | None) -> list[str]:
    """The descriptive clauses of a prompt, lower-cased at the start; the rest is dropped."""
    out = []
    for clause in clauses(text):
        if clause.lower() in skip:
            continue
        if INSTRUCTION.search(clause) or NEGATION.search(clause) or META.search(clause):
            continue
        if fixed is not None and fixed.search(clause):
            continue
        out.append(clause[0].lower() + clause[1:])
    return out


def note_fragments(note: str, *, skip: set[str], fixed: re.Pattern | None) -> tuple[list[str], int]:
    """A note's descriptive clauses and how many were left out as review remarks."""
    keep, dropped = [], 0
    lines = [line for line in (note or "").splitlines() if not line.lstrip().startswith("[")]  # tool-written entries
    for clause in clauses("\n".join(lines)):
        if clause.lower() in skip:
            continue
        if EVALUATIVE.search(clause) or INSTRUCTION.search(clause) or NEGATION.search(clause) or \
                (fixed is not None and fixed.search(clause)):
            dropped += 1
            continue
        keep.append(clause[0].lower() + clause[1:])
    return keep, dropped


def apply_vocabulary(text: str, vocabulary: Mapping[str, str]) -> str:
    """The rules' preferred words (longest first), whole words only."""
    for bad, good in sorted(vocabulary.items(), key=lambda kv: -len(kv[0])):
        text = re.sub(rf"(?<![\w-]){re.escape(bad)}(?![\w-])", good, text, flags=re.I)
    return text


def _own_basis(record: Mapping) -> str:
    basis = record.get("caption_basis")
    if isinstance(basis, str) and basis.strip():
        return basis
    prompt = (record.get("source") or {}).get("prompt")
    return prompt.strip().splitlines()[0] if isinstance(prompt, str) and prompt.strip() else ""


def propose_one(record: Mapping, images: Mapping[str, dict], rules: captions_mod.Rules, *, class_word: str | None,
                default_outfit: str | None) -> Proposal:
    skip = {w.lower() for w in (rules.trigger, class_word, rules.style_tag) if w}
    fixed = _fixed(rules.not_in_captions)
    via: list[str] = []
    basis, frags = "none", []
    current: Mapping = record
    for _ in range(_MAX_CHAIN):
        text = _own_basis(current)
        if text:
            frags = prompt_fragments(text, skip=skip, fixed=fixed)
            if frags:
                basis = "prompt"
                break
        lin = current.get("lineage") or {}
        parent = lin.get("parent")
        if not parent or parent not in images:
            break
        via.append(f"{current['id']} is a {lin.get('transform') or 'derivation'} of {parent}")
        current = images[parent]
    own = record.get("lineage") or {}
    if basis == "prompt" and not via and own.get("parent"):  # a child that carries its parent's prompt itself
        via.append(f"{record['id']} is a {own.get('transform') or 'derivation'} of {own['parent']}")
    note = str(record.get("notes") or "")
    note_frags, note_dropped = note_fragments(note, skip=skip, fixed=fixed)
    if basis == "none" and note_frags:
        basis, frags, via = "note", note_frags, []
    if basis == "none":
        return Proposal(image=record["id"], basis="none", text="",
                        problems=["nothing to go on: ask the operator for a note (kiln images label "
                                  f"{record['id']} --note \"...\"); never guess"])
    core = apply_vocabulary(", ".join(frags), rules.vocabulary)
    low = core.lower()
    framing, viewpoint = record.get("framing"), record.get("viewpoint")
    extra = []
    if base.FRAMING_WORDS.get(framing) and not FRAMING_SEEN.search(low):
        extra.append(base.FRAMING_WORDS[framing])
    if base.VIEW_WORDS.get(viewpoint) and not VIEW_SEEN.search(low):
        extra.append(base.VIEW_WORDS[viewpoint])
    if (default_outfit and "outfit:default" in (record.get("labels") or []) and framing != "close"
            and default_outfit.lower() not in low and not OUTFIT_SEEN.search(low)):
        extra.append(default_outfit)
    text = ", ".join(x for x in (rules.trigger, class_word, core, *extra, rules.style_tag) if x)
    problems = captions_mod.lint_text(text, rules)
    relaxed = captions_mod.lint_text(text, dataclasses.replace(rules, min_words=0))
    checks = []
    if via:
        checks.append("derived since its prompt (" + "; ".join(via) + "): check the text still describes it")
    if basis == "prompt" and note.strip() and note_frags:
        checks.append("the operator wrote a note: merge what it describes (the note wins)")
    if basis == "note" and note_dropped:
        checks.append(f"{note_dropped} part(s) of the note read as review remarks and were left out: check that "
                      "nothing descriptive was lost")
    return Proposal(image=record["id"], basis=basis, text=text, problems=problems + checks, via=via,
                    storable=bool(text) and not relaxed and not checks)


def propose(project: Project, *, store: bool = False, every: bool = False) -> Result:
    """Proposals for the kept images that wait for a caption (`every`: drafts too); `store` keeps the clean ones.

    With `store`, the captions are read and the drafts appended under the project lock, so an approval recorded
    by another session at the same time is never covered by a draft built from the state before it."""
    if not store:
        return _propose(project, store=False, every=every)
    with project.lock():
        return _propose(project, store=True, every=every)


def _propose(project: Project, *, store: bool, every: bool) -> Result:
    rules = captions_mod.load_rules(project)
    spec = base.load_spec(project)
    word = base.class_word(project, spec)
    outfit = base.real((spec.get("caption") or {}).get("default_outfit"))
    images = images_mod.records(project)
    last = captions_mod.latest(project)
    result = Result()
    new_lines = []
    for image_id, record in sorted(captions_mod.training_images(images).items()):
        line = last.get(image_id)
        status = (line or {}).get("status")
        if status == "approved":
            result.approved_left += 1
            continue
        if status == "draft" and not every:
            result.drafts_left += 1
            continue
        proposal = propose_one(record, images, rules, class_word=word, default_outfit=outfit)
        result.proposals.append(proposal)
        if not (store and proposal.storable):
            continue
        if line is not None and line.get("status") == "draft" and line.get("text") == proposal.text:
            result.unchanged += 1
            continue
        data = {"image": image_id, "text": proposal.text, "word_count": len(proposal.text.split()),
                "status": "draft", "basis": "prompt" if proposal.basis == "prompt" else "manual", "sheet": None,
                "lint": proposal.problems, "legacy_id": None}
        new_lines.append(event_line(SCHEMA["caption"], data, source=SOURCE, by=BY))
        proposal.stored = True
    if new_lines:
        project.append(project.captions_log, new_lines)
    return result
