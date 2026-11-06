"""Helpers for the image-source tests: an easel instance holding the demo render run, and a demo project without
the run's imports yet.

Not a test module (no test_ prefix); the test files beside it import it by name. The easel fixture mirrors the easel
repository's demo run `run-20300101-001` (four outputs: kept, kept, rejected, maybe then kept), written here from
the shared fixture generator so this repository's tests never need the other checkout: outputs 1-4 are fixture
PNGs 17-20, byte for byte the media the kiln demo's imported rows hash to.
"""

from __future__ import annotations

import hashlib
import importlib.util
import json
import shutil
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
RUN = "run-20300101-001"
RECIPE = "rcp-20300101-001"
PROMPT = "dmrbt, robot, standing in a tidy workshop, soft daylight, full body"
CHECKS = {"face": "pass", "head": "pass", "colours": "pass", "body": "pass", "feature": "pass", "hands": "na"}


def fixtures():
    spec = importlib.util.spec_from_file_location("make_fixtures", REPO / "tests" / "fixtures" / "make_fixtures.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _review(n: int, verdict: str, minute: int, note: str | None = None, source: str = "operator",
            checks: dict | None = None) -> dict:
    return {"schema": "easel/run-review-event/1", "at": f"2030-01-01T12:{minute:02d}:00+00:00", "v": 1,
            "source": source, "by": "easel runs review", "run": RUN, "n": n, "verdict": verdict,
            "checks": checks or CHECKS, "note": note, "via": "app", "legacy_id": None}


def easel_home(tmp: Path, *, reviews: list[dict] | None = None, copy_outputs: bool = True,
               prompt: str = PROMPT) -> Path:
    """An easel instance with the demo run: run.json, reviews.jsonl, the recipe and (by default) the four outputs."""
    fx = fixtures()
    home = tmp / "easel-home"
    run_dir = home / "runs" / RUN
    (run_dir / "outputs").mkdir(parents=True, exist_ok=True)
    (home / "recipes").mkdir(parents=True, exist_ok=True)
    (home / "easel.toml").write_text('schema = "easel/config/1"\n', encoding="utf-8")
    outputs = []
    for n in range(1, 5):
        data = fx.png_bytes(16 + n)
        if copy_outputs:
            (run_dir / "outputs" / f"{n:03d}.png").write_bytes(data)
        outputs.append({"n": n, "file": f"outputs/{n:03d}.png", "sha256": hashlib.sha256(data).hexdigest(),
                        "bytes": len(data), "width": 8, "height": 8, "seed": 1000 + n, "metadata_stripped": False})
    run = {"schema": "easel/run/1", "id": RUN, "legacy_id": None, "recipe": RECIPE, "workflow": None, "job": None,
           "created_at": "2030-01-01T10:00:00+00:00", "status": "ok", "prompt_id": None, "seed": 1001, "count": 4,
           "prompt": prompt, "negative": "lowres, blurry, text, watermark", "width": 832, "height": 1216,
           "graph": "graph.json", "manifest": "manifest.json", "outputs": outputs, "imported_from": None,
           "error": None, "notes": None}
    (run_dir / "run.json").write_text(json.dumps(run, indent=2), encoding="utf-8")
    if reviews is None:
        reviews = [_review(1, "keep", 0), _review(2, "keep", 1),
                   _review(3, "reject", 2, "the visor split into two eyes", checks={**CHECKS, "face": "fail"}),
                   _review(4, "maybe", 3, "the vest looks red"),
                   _review(4, "keep", 9, "on a second look the vest is orange")]
    (run_dir / "reviews.jsonl").write_text("".join(json.dumps(r) + "\n" for r in reviews), encoding="utf-8")
    recipe = {"schema": "easel/recipe/1", "id": RECIPE, "content_class": "sfw", "prompt": prompt}
    (home / "recipes" / f"{RECIPE}.json").write_text(json.dumps(recipe, indent=2), encoding="utf-8")
    return home


def review(n: int, verdict: str, minute: int, **kw) -> dict:
    return _review(n, verdict, minute, **kw)


def strip_imports(demo: Path) -> Path:
    """Remove the easel run's imported images (lines, captions, files) from a demo copy; returns the project folder."""
    project = demo / "projects" / "demo_robot"
    lines = [json.loads(x) for x in (project / "images.jsonl").read_text(encoding="utf-8").splitlines() if x.strip()]
    gone = {line["id"] for line in lines if line.get("root") == "imports"}
    keep = [line for line in lines if line["id"] not in gone]
    (project / "images.jsonl").write_text("".join(json.dumps(x) + "\n" for x in keep), encoding="utf-8")
    caps = [json.loads(x) for x in (project / "captions.jsonl").read_text(encoding="utf-8").splitlines() if x.strip()]
    (project / "captions.jsonl").write_text("".join(json.dumps(x) + "\n" for x in caps if x["image"] not in gone),
                                            encoding="utf-8")
    shutil.rmtree(project / "images" / RUN, ignore_errors=True)
    return project
