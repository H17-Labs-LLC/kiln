"""`kiln project intake show|answer`: the operator's dictated answers reach intake.yaml verbatim, through the store,
with a note of who dictated them; nothing is guessed, mapped or written by hand."""

from pathlib import Path

import pytest
import yaml
from kiln_project_helpers import SLUG, run

CONFIG = 'schema = "kiln/config/1"\n\n[paths]\nroot = "."\nprojects = "projects"\njournal = "journal"\n'


@pytest.fixture
def project(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, frozen_clock, capsys) -> Path:
    home = tmp_path / "home"
    home.mkdir()
    (home / "kiln.toml").write_text(CONFIG, encoding="utf-8")
    monkeypatch.setenv("KILN_HOME", str(home))
    code, out = run(["project", "new", SLUG, "--type", "character-anime"], capsys)
    assert code == 0, out
    assert out[-1] == ("next: ask the operator each question of `kiln project intake show`; record each reply with "
                       'kiln project intake answer <question-id> "<words>"; then kiln project lint')
    return home / "projects" / SLUG


def _questions(project: Path) -> dict[str, dict]:
    data = yaml.safe_load((project / "intake.yaml").read_text(encoding="utf-8"))
    return {q["id"]: q for q in data["questions"]}


def test_an_answer_is_stored_verbatim_with_who_dictated_it(project: Path, capsys):
    words = "  A tall lighthouse keeper robot,  rust-red, with a brass lamp for a head  "
    code, out = run(["project", "intake", "answer", "description", words], capsys)
    assert code == 0, out
    assert out == [f"intake 3 description: answered ({words}), dictated by operator",
                   "next: kiln project intake show --open"]
    q = _questions(project)["description"]
    assert q["answer"] == words, "verbatim: not stripped, not reworded"
    assert q["note"] == "[answer dictated by operator, 2030-01-01T00:00:00+00:00]"
    text = (project / "intake.yaml").read_text(encoding="utf-8")
    assert text.startswith("# Intake for a character-anime LoRA"), "the leading comment block is kept"
    assert b"\r" not in (project / "intake.yaml").read_bytes()


def test_a_second_answer_replaces_the_first_and_keeps_the_types_note(project: Path, capsys):
    before = _questions(project)["references"]["note"]
    assert run(["project", "intake", "answer", "references", "three sketches", "--by", "operator"], capsys)[0] == 0
    assert run(["project", "intake", "answer", "4", "four sketches, the first is the cleanest"], capsys)[0] == 0
    q = _questions(project)["references"]
    assert q["answer"] == "four sketches, the first is the cleanest"
    assert q["note"] == f"{before} [answer dictated by operator, 2030-01-01T00:00:00+00:00]"
    assert q["note"].count("[answer dictated by") == 1


def test_a_list_question_takes_one_item_per_argument(project: Path, capsys):
    code, out = run(["project", "intake", "answer", "families", "illustrious", "pony"], capsys)
    assert code == 0 and out[0] == "intake 12 families: answered (illustrious, pony), dictated by operator", out
    assert _questions(project)["families"]["answer"] == ["illustrious", "pony"]


@pytest.mark.parametrize(("argv", "first"), [
    (["mouth", "no teeth please"], "REFUSED: mouth takes one of: allowed, never_teeth"),
    (["name", "Demo", "Robot"], "REFUSED: name takes one answer, got 2 words as separate arguments"),
    (["name", "   "], "REFUSED: an empty answer for name: ask the operator again"),
    (["colour", "red"], "no intake question 'colour'"),
])
def test_refusals_write_nothing(project: Path, capsys, argv: list[str], first: str):
    before = (project / "intake.yaml").read_bytes()
    code, out = run(["project", "intake", "answer", *argv], capsys)
    assert code == 2 and out[0] == first, out
    assert (project / "intake.yaml").read_bytes() == before


def test_show_lists_the_questions_and_what_is_missing(project: Path, capsys):
    assert run(["project", "intake", "answer", "name", "Demo Robot"], capsys)[0] == 0
    code, out = run(["project", "intake", "show", "--open"], capsys)
    assert code == 0, out
    assert "1  name  answered" not in out, "--open hides answered questions"
    assert "3  description  MISSING (required)" in out
    assert "    choices: allowed, never_teeth" in out
    assert out[-2] == f"intake {SLUG}: 1 of 14 answered, 4 required still missing"
    assert out[-1] == 'next: ask the operator; then kiln project intake answer <question-id> "<words>"'
    code, out = run(["project", "lint"], capsys)
    assert code == 1 and out[-1] == f"lint {SLUG}: 4 problems, 9 warnings", out
