"""`kiln types check`: the shipped types pass; a planted type with a missing or broken file fails, one line per problem."""

import shutil
from pathlib import Path

import pytest

from kiln import cli, types_check

REPO = Path(__file__).resolve().parents[2]
TYPES = REPO / "types"
SCHEMAS = REPO / "schemas"
SHIPPED = ("character-anime", "character-human", "style")
PLANTED = "planted-type"
TYPED_FILES = ("intake.yaml", "checklist.yaml", "caption_rules.yaml", "thresholds.yaml")


def check(types_dir: Path, type_id: str | None = None, *, schemas_dir: Path = SCHEMAS, strict: bool = False) -> tuple[int, list[str]]:
    """Run the check the way the CLI does and collect its output lines."""
    lines: list[str] = []
    code = types_check.run(type_id, types_dir=types_dir, schemas_dir=schemas_dir, strict=strict, out=lines.append)
    return code, lines


def problem_lines(lines: list[str]) -> list[str]:
    return [line for line in lines if not line.startswith(("warn:", "note:", "ok:", "FAIL:"))]


@pytest.fixture
def planted(tmp_path: Path) -> Path:
    """A types folder in tmp_path holding one valid copy of the shipped character-anime type, renamed."""
    types_dir = tmp_path / "types"
    shutil.copytree(TYPES / "character-anime", types_dir / PLANTED)
    for name in TYPED_FILES:
        path = types_dir / PLANTED / name
        path.write_text(path.read_text(encoding="utf-8").replace("type: character-anime", f"type: {PLANTED}"), encoding="utf-8")
    return types_dir


def edit(types_dir: Path, name: str, old: str, new: str) -> None:
    path = types_dir / PLANTED / name
    text = path.read_text(encoding="utf-8")
    assert old in text, f"{old!r} not in {name}"
    path.write_text(text.replace(old, new, 1), encoding="utf-8")


# --- the shipped data ---------------------------------------------------------------------------------------------

def test_shipped_types_pass():
    code, lines = check(TYPES)
    assert code == 0, lines
    assert problem_lines(lines) == []
    assert lines[-1].startswith(f"ok: {len(SHIPPED)} type(s) checked: {', '.join(SHIPPED)}"), lines


def test_shipped_types_are_the_three_promised():
    assert tuple(f.name for f in types_check.type_folders(TYPES)) == SHIPPED


@pytest.mark.parametrize("type_id", SHIPPED)
def test_each_shipped_type_holds_every_type_file(type_id):
    present = {p.name for p in (TYPES / type_id).iterdir()}
    assert set(types_check.TYPE_FILES) <= present


@pytest.mark.parametrize("type_id", SHIPPED)
def test_one_type_by_id(type_id):
    code, lines = check(TYPES, type_id)
    assert code == 0, lines
    assert lines[-1].startswith(f"ok: 1 type(s) checked: {type_id}")


def test_cli_types_check_exits_0(capsys):
    assert cli.main(["types", "check"]) == 0
    assert "ok: 3 type(s) checked" in capsys.readouterr().out


def test_cli_help_has_no_parentheses(capsys):
    for argv in (["types", "--help"], ["types", "check", "--help"]):
        with pytest.raises(SystemExit) as exited:
            cli.main(argv)
        assert exited.value.code == 0
        out = capsys.readouterr().out
        assert "(" not in out and ")" not in out, out


def test_unknown_type_id_exits_2():
    code, lines = check(TYPES, "no-such-type")
    assert code == 2
    assert lines == ["unknown type: no-such-type; known: character-anime, character-human, style"]


def test_no_types_folder_exits_2(tmp_path):
    code, lines = check(tmp_path / "absent")
    assert code == 2
    assert len(lines) == 1


# --- planted types ------------------------------------------------------------------------------------------------

def test_planted_copy_passes(planted):
    code, lines = check(planted)
    assert code == 0, lines


@pytest.mark.parametrize("name", list(types_check.TYPE_FILES))
def test_planted_type_missing_a_file_fails(planted, name):
    (planted / PLANTED / name).unlink()
    code, lines = check(planted)
    assert code == 1
    assert f"types/{PLANTED}/{name}: missing" in lines
    assert lines[-1] == "FAIL: 1 problem(s) in 1 type(s)"


def test_intake_with_13_questions_fails(planted):
    path = planted / PLANTED / "intake.yaml"
    text = path.read_text(encoding="utf-8")
    path.write_text(text[: text.index("  - n: 14")], encoding="utf-8")
    code, lines = check(planted)
    assert code == 1
    problems = problem_lines(lines)
    assert any(p.startswith(f"types/{PLANTED}/intake.yaml: questions:") for p in problems), problems
    assert f"types/{PLANTED}/intake.yaml: questions must be numbered 1 to 14 in order" in problems


def test_intake_with_an_answer_fails(planted):
    edit(planted, "intake.yaml", "    example: Demo Robot\n    answer: null", "    example: Demo Robot\n    answer: Demo Robot")
    code, lines = check(planted)
    assert code == 1
    assert any("question 1: answer must be null" in line for line in lines), lines


def test_default_outside_its_choices_fails(planted):
    edit(planted, "intake.yaml", "    choices: [allowed, never_teeth]\n    default: allowed",
         "    choices: [allowed, never_teeth]\n    default: sometimes")
    code, lines = check(planted)
    assert code == 1
    assert any("question 9: default 'sometimes' is not one of its choices" in line for line in lines), lines


def test_type_field_must_match_the_folder(planted):
    edit(planted, "thresholds.yaml", f"type: {PLANTED}", "type: character-anime")
    code, lines = check(planted)
    assert code == 1
    assert f"types/{PLANTED}/thresholds.yaml: type is 'character-anime' but the folder is '{PLANTED}'" in lines


def test_identity_checklist_without_body_fails(planted):
    edit(planted, "checklist.yaml", "  - key: body\n", "  - key: build\n")
    code, lines = check(planted)
    assert code == 1
    assert any(line.startswith(f"types/{PLANTED}/checklist.yaml: items:") for line in lines), lines


def test_thresholds_without_the_validate_first_status_fails(planted):
    edit(planted, "thresholds.yaml", "status: validate on your subject first", "status: final")
    code, lines = check(planted)
    assert code == 1
    assert any(line.startswith(f"types/{PLANTED}/thresholds.yaml: status:") for line in lines), lines


def test_caption_example_too_short_fails(planted):
    edit(planted, "caption_rules.yaml", "    full-body three-quarter view from the left, clean anime illustration with flat colours\n", "\n")
    edit(planted, "caption_rules.yaml", "    small wave, head tilted slightly to the side, warm station lamps and a soft blue evening sky behind,\n", "\n")
    code, lines = check(planted)
    assert code == 1
    assert any("example caption has" in line and "35-75" in line for line in lines), lines


def test_dataset_toml_unknown_variable_fails(planted):
    edit(planted, "dataset.toml", "min_bucket_reso = 640", "min_bucket_reso = ${MIN_RESO}")
    code, lines = check(planted)
    assert code == 1
    assert any(line.startswith(f"types/{PLANTED}/dataset.toml: unknown variable ${{MIN_RESO}}") for line in lines), lines


def test_dataset_toml_unused_variable_fails(planted):
    edit(planted, "dataset.toml", "learning_rate = ${LR}", "learning_rate = 0.0001")
    code, lines = check(planted)
    assert code == 1
    assert f"types/{PLANTED}/dataset.toml: variable ${{LR}} is never used" in lines


def test_dataset_toml_variable_in_a_comment_does_not_count(planted):
    edit(planted, "dataset.toml", "[kiln.network]\n", "# ${SOMETHING_ELSE} in a comment is documentation\n[kiln.network]\n")
    code, lines = check(planted)
    assert code == 0, lines


def test_dataset_toml_image_dir_outside_the_dataset_root_fails(planted):
    edit(planted, "dataset.toml", 'image_dir = "${DATASET_ROOT}/holdout"', 'image_dir = "elsewhere/holdout"')
    code, lines = check(planted)
    assert code == 1
    assert any("image_dir must start with ${DATASET_ROOT}/" in line for line in lines), lines


def test_dataset_toml_with_a_sd_scripts_flag_fails(planted):
    edit(planted, "dataset.toml", "[general]\n", "max_data_loader_n_workers = 0\n[general]\n")
    code, lines = check(planted)
    assert code == 1
    assert any(line.startswith(f"types/{PLANTED}/dataset.toml:") for line in problem_lines(lines)), lines


def test_card_without_the_repo_links_slot_fails(planted):
    edit(planted, "card.md.tmpl", "\n{{repo_links}}\n", "\n")
    code, lines = check(planted)
    assert code == 1
    assert f"types/{PLANTED}/card.md.tmpl: the {{{{repo_links}}}} slot is missing" in lines


def test_card_with_a_bad_field_name_fails(planted):
    edit(planted, "card.md.tmpl", "{{title}}", "{{Title Case}}")
    code, lines = check(planted)
    assert code == 1
    assert any("is not a lowercase_with_underscores name" in line for line in lines), lines


def test_readme_without_a_heading_fails(planted):
    (planted / PLANTED / "README.md").write_text("no heading here\n", encoding="utf-8")
    code, lines = check(planted)
    assert code == 1
    assert f"types/{PLANTED}/README.md: must start with a '# ' heading" in lines


def test_yaml_that_does_not_parse_fails(planted):
    (planted / PLANTED / "checklist.yaml").write_text("items: [unclosed\n", encoding="utf-8")
    code, lines = check(planted)
    assert code == 1
    assert any(line.startswith(f"types/{PLANTED}/checklist.yaml: does not parse as YAML") for line in lines), lines


def test_extra_file_warns_and_fails_only_when_strict(planted):
    (planted / PLANTED / "notes.txt").write_text("scratch\n", encoding="utf-8")
    code, lines = check(planted)
    assert code == 0, lines
    assert f"warn: types/{PLANTED}/notes.txt: not a type file; types check ignores it" in lines
    code, lines = check(planted, strict=True)
    assert code == 1
    assert f"types/{PLANTED}/notes.txt: not a type file; types check ignores it" in lines


def test_bad_type_id_fails(planted):
    (planted / PLANTED).rename(planted / "Bad_Type")
    code, lines = check(planted)
    assert code == 1
    assert any(line.startswith("types/Bad_Type: a type id is") for line in lines), lines


def test_missing_own_schema_fails(planted, tmp_path):
    schemas = tmp_path / "schemas"
    shutil.copytree(SCHEMAS, schemas)
    (schemas / "type-intake.schema.json").unlink()
    code, lines = check(planted, schemas_dir=schemas)
    assert code == 1
    assert "schemas/type-intake.schema.json: missing" in lines


def test_absent_shared_record_schema_is_a_note(planted, tmp_path):
    schemas = tmp_path / "schemas"
    schemas.mkdir()
    for path in SCHEMAS.glob("type-*.schema.json"):
        shutil.copy2(path, schemas / path.name)
    code, lines = check(planted, schemas_dir=schemas)
    assert code == 0, lines
    assert any(line.startswith("note: schemas/eval-suite.schema.json not present yet") for line in lines), lines
    assert any(line.startswith("note: schemas/subject-spec.schema.json not present yet") for line in lines), lines
