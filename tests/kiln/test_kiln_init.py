"""`kiln init <dir>`: the layout it writes, the refusals, and a home that `project new`, `next` and `doctor` use."""

from __future__ import annotations

import tomllib
from pathlib import Path

import pytest

from kiln import cli
from kiln import init as init_mod

FOLDERS = ("projects", "journal", "runtime")


def run(argv: list[str], capsys) -> tuple[int, list[str], list[str]]:
    """Run `kiln <argv>` in-process: (exit code, stdout lines, stderr lines without the frozen-clock line)."""
    capsys.readouterr()
    try:
        code = cli.main(argv)
    except SystemExit as exc:
        code = exc.code if isinstance(exc.code, int) else 2
    captured = capsys.readouterr()
    err = [line for line in captured.err.splitlines() if not line.startswith("clock frozen")]
    return code, captured.out.splitlines(), err


@pytest.fixture
def place(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """A scratch folder outside every checkout, as the current folder (skips where the temp folder is in one)."""
    if init_mod.checkout_above(tmp_path.resolve()) is not None:
        pytest.skip("the temp folder lies inside a repository checkout here")
    monkeypatch.chdir(tmp_path)
    return tmp_path


def test_init_writes_the_layout(place, frozen_clock, capsys):
    code, out, err = run(["init", "home"], capsys)
    assert code == 0, out + err
    home = place / "home"
    assert (home / ".gitignore").read_text(encoding="utf-8") == "*\n!README.md\n"
    for name in FOLDERS:
        assert (home / name).is_dir(), name
    assert out == ["kiln home: home",
                   "created: .gitignore, README.md, projects/, journal/, runtime/, kiln.toml",
                   "kiln finds it when you work inside it, when KILN_HOME names it, or with --workspace home",
                   "next: kiln doctor --workspace home"]
    text = (home / "kiln.toml").read_text(encoding="utf-8")
    assert text.splitlines()[0].startswith("# kiln.toml: this instance's configuration, written by `kiln init` (kiln ")
    assert tomllib.loads(text) == tomllib.loads(init_mod.EXAMPLE.read_text(encoding="utf-8"))
    readme = (home / "README.md").read_text(encoding="utf-8")
    assert readme.startswith("# kiln home\n") and "This folder is a kiln instance" in readme
    assert "KILN_HOME" in readme and "2030-01-01" in readme


def test_refusals_leave_nothing_behind(place, frozen_clock, capsys):
    (place / "checkout" / ".git").mkdir(parents=True)
    code, out, err = run(["init", "checkout/home"], capsys)
    assert (code, out) == (2, [])
    assert err[0].startswith("refused: checkout/home lies inside a repository checkout (checkout/, found by .git)")
    assert not (place / "checkout" / "home").exists()

    (place / "busy").mkdir()
    (place / "busy" / "a.txt").write_text("a", encoding="utf-8")
    (place / "busy" / "b.txt").write_text("b", encoding="utf-8")
    code, _, err = run(["init", "busy"], capsys)
    assert code == 2
    assert err[0] == "refused: busy is not empty (2 entries) and holds no kiln.toml; nothing was written"
    assert sorted(p.name for p in (place / "busy").iterdir()) == ["a.txt", "b.txt"]


def test_force_existing_and_an_existing_home(place, frozen_clock, capsys):
    folder = place / "busy"
    (folder / "projects").mkdir(parents=True)
    code, out, _ = run(["init", "busy", "--force-existing"], capsys)
    assert code == 0
    assert "created: .gitignore, README.md, journal/, runtime/, kiln.toml" in out
    assert "kept (already there, not changed): projects/" in out
    before = (folder / "kiln.toml").read_bytes()
    code, _, err = run(["init", "busy", "--force-existing"], capsys)
    assert code == 2
    assert err == ["refused: busy is already a kiln home (it holds kiln.toml); nothing was changed",
                   "use it: kiln doctor --workspace busy, or set KILN_HOME to it"]
    assert (folder / "kiln.toml").read_bytes() == before


def test_the_new_home_takes_a_project_and_next_routes_it(place, frozen_clock, monkeypatch, capsys):
    """init -> doctor -> project new -> next: the home init writes is one the other verbs accept."""
    assert run(["init", "home"], capsys)[0] == 0
    (place / "models").mkdir()
    monkeypatch.setenv("MODELS_ROOT", str(place / "models"))
    monkeypatch.setenv("PRIVATE_WORDS_FILE", str(place / "private-words.txt"))
    code, out, _ = run(["doctor", "--workspace", "home"], capsys)
    assert code == 0, out
    assert out[-1] == "next: kiln next"
    code, out, _ = run(["next", "--workspace", "home"], capsys)
    assert code == 0, out
    assert out[0] == "stage: 00-new-project"
    code, out, err = run(["project", "new", "demo_robot", "--type", "character-anime", "--workspace", "home"], capsys)
    assert code == 0, out + err
    assert (place / "home" / "projects" / "demo_robot" / "project.yaml").is_file()
    code, out, _ = run(["doctor", "--workspace", "home"], capsys)
    assert "projects: 1 (demo_robot) ... ok" in out


def test_the_shipped_example_names_the_folders_init_creates():
    """The help row and the README promise projects/, journal/, runtime/: the example's [paths] must name them."""
    paths = tomllib.loads(init_mod.EXAMPLE.read_text(encoding="utf-8"))["paths"]
    for key in FOLDERS:
        assert paths.get(key) == key, key
