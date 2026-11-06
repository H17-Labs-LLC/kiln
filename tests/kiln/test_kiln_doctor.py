"""`kiln doctor`: the line shapes, FAIL vs warn on native and adopted instances, the trainer runtime, the disk
floor, the shipped families and types, the projects, and `next:`."""

from __future__ import annotations

from pathlib import Path

import pytest

from kiln import cli
from kiln import doctor as doctor_mod
from kiln._common import sysinfo

BASE = """schema = "kiln/config/1"

[paths]
root = "."
models_root = "models"
projects = "projects"
journal = "journal"
runtime = "runtime"
"""


def run(argv: list[str], capsys) -> tuple[int, list[str]]:
    """Run `kiln <argv>` in-process: (exit code, stdout lines)."""
    capsys.readouterr()
    try:
        code = cli.main(argv)
    except SystemExit as exc:
        code = exc.code if isinstance(exc.code, int) else 2
    return code, capsys.readouterr().out.splitlines()


def make_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, extra: str = "", folders: bool = True) -> Path:
    """A small instance found through KILN_HOME; `extra` is appended to the TOML."""
    home = tmp_path / "home"
    home.mkdir()
    (home / "kiln.toml").write_text(BASE + extra, encoding="utf-8")
    if folders:
        for name in ("models", "projects", "journal", "runtime"):
            (home / name).mkdir()
    monkeypatch.setenv("KILN_HOME", str(home))
    return home


def fake_runtime(home: Path) -> None:
    python = sysinfo.venv_python(home / "runtime" / "venv-train")
    python.parent.mkdir(parents=True)
    python.write_bytes(b"")
    (home / "runtime" / "sd-scripts").mkdir()
    (home / "runtime" / "sd-scripts" / "sdxl_train_network.py").write_text("# stand-in\n", encoding="utf-8")


def test_no_instance_points_at_init(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("USERPROFILE", str(tmp_path))
    code, lines = run(["doctor"], capsys)
    assert code == 1
    assert lines[0].startswith("kiln: ") and lines[0].endswith(" ... ok")
    assert "FAIL: config: no kiln.toml here, in a parent folder or in the user config folder" in lines
    assert lines[-1] == "next: kiln init ~/kiln-home"
    assert not any(str(tmp_path) in line for line in lines), "doctor printed an absolute path"


def test_a_ready_native_instance(tmp_path, monkeypatch, capsys):
    home = make_home(tmp_path, monkeypatch)
    fake_runtime(home)
    code, lines = run(["doctor"], capsys)
    assert code == 0, lines
    assert "config: kiln.toml (found by KILN_HOME) ... ok" in lines
    assert "instance: native ... ok" in lines
    assert "models: <models_root> exists ... ok" in lines
    assert "folders: projects/, journal/, runtime/ exist ... ok" in lines
    assert "train runtime: runtime/venv-train has a python (train.venv_dir) ... ok" in lines
    assert "sd-scripts: runtime/sd-scripts (sdxl_train_network.py found) ... ok" in lines
    assert "projects: none yet ... ok" in lines
    assert any(line.startswith("families: ") and line.endswith(" ... ok") for line in lines)
    assert any(line.startswith("types: ") and "character-anime" in line for line in lines)
    assert any(line.startswith("disk: free ") for line in lines)
    assert lines[-1] == "next: kiln next"
    assert not any(str(tmp_path) in line for line in lines), "doctor printed an absolute path"


def test_a_missing_runtime_is_a_warn_naming_the_installer(tmp_path, monkeypatch, capsys):
    make_home(tmp_path, monkeypatch)
    code, lines = run(["doctor"], capsys)
    assert code == 0, lines
    assert ("train runtime: no python in runtime/venv-train (train.venv_dir); training runtime installer: "
            "session 2 ... warn") in lines
    assert ("sd-scripts: runtime/sd-scripts does not exist (train.sd_scripts); training runtime installer: "
            "session 2 ... warn") in lines
    assert sum(line.startswith("  fix: ") for line in lines) == 1, "one fix line for the runtime"
    assert not any(line.startswith("FAIL") for line in lines)


def test_train_python_wins_and_outside_paths_are_tokens(tmp_path, monkeypatch, capsys):
    outside = tmp_path / "trainer" / "python.exe"
    outside.parent.mkdir()
    outside.write_bytes(b"")
    monkeypatch.setenv("KILN_TRAIN__PYTHON", str(outside))
    make_home(tmp_path, monkeypatch)
    code, lines = run(["doctor"], capsys)
    assert code == 0, lines
    assert "train runtime: python <train_python> (train.python) ... ok" in lines
    outside.unlink()
    code, lines = run(["doctor"], capsys)
    assert code == 0
    assert ("train runtime: train.python names no file (<train_python>); training runtime installer: session 2 "
            "... warn") in lines


def test_an_unset_variable_is_a_fail_and_the_next_line(tmp_path, monkeypatch, capsys):
    name = "KILN_DOCTOR_TEST_UNSET_ROOT"
    monkeypatch.delenv(name, raising=False)
    home = make_home(tmp_path, monkeypatch)
    text = (home / "kiln.toml").read_text(encoding="utf-8").replace('models_root = "models"',
                                                                     'models_root = "${' + name + '}"')
    (home / "kiln.toml").write_text(text, encoding="utf-8")
    code, lines = run(["doctor"], capsys)
    assert code == 1
    assert f"FAIL: ${{{name}}} unset (paths.models_root)" in lines
    assert lines[-1] == "next: set the variable, or kiln config set paths.models_root <value>"
    assert not any(line.startswith("models:") for line in lines)


def test_native_missing_images_root_is_a_fail_adopted_is_a_warn(tmp_path, monkeypatch, capsys):
    home = make_home(tmp_path, monkeypatch, folders=False, extra='images_root = "dataset-images"\n')
    code, lines = run(["doctor"], capsys)
    assert code == 1
    assert "FAIL: images_root: paths.images_root does not exist (<images_root>)" in lines
    assert "models: paths.models_root does not exist (<models_root>); training and evaluation need it ... warn" in lines
    assert any(line.startswith("folders: projects/, journal/, runtime/ not there yet") for line in lines)
    assert lines[-1] == "next: kiln config set paths.images_root <images_root>"

    (home / "kiln.toml").write_text((home / "kiln.toml").read_text(encoding="utf-8")
                                    + "\n[instance]\nadopted = true\n", encoding="utf-8")
    code, lines = run(["doctor"], capsys)
    assert code == 0, lines
    assert "instance: adopted (instance.adopted = true) ... ok" in lines
    assert not any(line.startswith("FAIL") for line in lines)
    assert any(line.startswith("images_root: paths.images_root does not exist") and line.endswith("... warn")
               for line in lines)
    assert any(line.startswith("legacy: ") and line.endswith("... warn") for line in lines)
    assert lines[-1] == "next: kiln next"


def test_the_disk_floor_default_is_20_and_a_low_disk_is_a_warn(tmp_path, monkeypatch, capsys):
    make_home(tmp_path, monkeypatch)
    monkeypatch.setattr(sysinfo, "disk_free_gb", lambda path=".": 5.0)
    code, lines = run(["doctor"], capsys)
    assert code == 0
    assert ("disk: free 5 GB < 20 GB floor (limits.disk_floor_gb); downloads and training wait ... warn") in lines
    monkeypatch.setenv("KILN_LIMITS__DISK_FLOOR_GB", "4")
    code, lines = run(["doctor"], capsys)
    assert "disk: free 5 GB >= 4 GB floor ... ok" in lines


def test_a_broken_family_file_is_a_fail(tmp_path, monkeypatch, capsys):
    make_home(tmp_path, monkeypatch)
    families = tmp_path / "families"
    families.mkdir()
    (families / "broken.yaml").write_text("schema: kiln/family/1\nid: broken\nname: Broken\n", encoding="utf-8")
    monkeypatch.setattr(doctor_mod, "FAMILIES_DIR", families)
    code, lines = run(["doctor"], capsys)
    assert code == 1
    assert any(line.startswith("FAIL: families: ") and line.endswith("in 1 family file(s)") for line in lines)
    assert lines[-1] == "next: kiln families check (it names them); report them to the maintainer"


def test_a_broken_instance_file_is_a_fail(tmp_path, monkeypatch, capsys):
    home = make_home(tmp_path, monkeypatch)
    (home / "kiln.toml").write_text("schema = \n", encoding="utf-8")
    code, lines = run(["doctor"], capsys)
    assert code == 1
    assert any(line.startswith("FAIL: config: ") and "not readable TOML" in line for line in lines)
    assert lines[-1] == "next: fix the config line above, then `kiln doctor`"


def test_the_demo_instance(demo_workspace, frozen_clock, capsys):
    code, lines = run(["doctor"], capsys)
    assert code == 0, lines
    assert "projects: 1 (demo_robot) ... ok" in lines
    assert "images_root: <images_root> exists (kiln only reads it) ... ok" in lines
    assert lines[-1] == "next: kiln next"
