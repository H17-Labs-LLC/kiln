"""The `codex` source: images from the Codex CLI's built-in image tool, one jobs-file job at a time.

What it does per job (the settings are `sources/codex.yaml`, overridable per instance in `<home>/sources/codex.yaml`):
1. refuse before spending when the job has more references than the image tool accepts (`max_references`);
2. run `<command> exec ...` without a shell, the relay instructions on stdin, `CODEX_HOME` = `[sources] codex_home`
   and no API key variable in its environment (the plan's allowance, never per-call billing);
3. collect the images from `<codex_home>/<images_dir>/<thread id>/` (the thread id comes from the event stream;
   the folder is the authority), falling back to "new since the call" when the thread folder is missing; each file
   must have a readable PNG header, nothing more is read;
4. read the saved session (when there is one) for what the image model received: the prompt (`verbatim` = it
   matched ours byte for byte), how many images each call included, the quota windows;
5. hand the files back; `kiln source run` copies, registers and writes the generation manifest.

A job with no image is classified, never retried: `blocked` (output moderation, `moderation_marker` in the
stream), `refused` (the relay's English refusal wording, `refusal_hints`; unverified), `quota` (the usage window
is spent, `quota_hints`; the batch stops), `failed` (the CLI exited non-zero or timed out) or `empty`. What is not
verified about training on such images is in docs/sources/codex.md. Ported from the earlier Codex generation tool:
the measured behaviour is kept, the code is new.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import tempfile
from datetime import datetime
from pathlib import Path

from kiln._common import image_meta
from kiln.project.model import REPO, Project, ProjectError

from . import base

SETTINGS_FILE = REPO / "sources" / "codex.yaml"
REQUIRED = {"command": list, "exec_args": list, "max_references": int, "images_dir": str, "sessions_dir": str,
            "timeout_s": int, "drop_env": list, "exit_codes": dict, "moderation_marker": str, "quota_hints": list,
            "refusal_hints": list, "relay": str}
RESULT_SCHEMA = {"type": "object", "additionalProperties": False, "required": ["path"],
                 "properties": {"path": {"type": "string"}}}


def load_settings(project: Project | None = None, base_file: Path = SETTINGS_FILE) -> dict:
    """The shipped settings with the instance's `<home>/sources/codex.yaml` laid over them, checked."""
    import yaml

    if not base_file.is_file():
        raise ProjectError(2, "sources/codex.yaml is missing: run kiln from a checkout of the repository")
    settings = yaml.safe_load(base_file.read_text(encoding="utf-8")) or {}
    if project is not None:
        own = project.instance.root / "sources" / "codex.yaml"
        if own.is_file():
            settings.update(yaml.safe_load(own.read_text(encoding="utf-8")) or {})
    wrong = [f"{key} ({kind.__name__})" for key, kind in REQUIRED.items() if not isinstance(settings.get(key), kind)]
    if wrong or not settings.get("command"):
        raise ProjectError(2, "the codex settings miss or mistype: " + ", ".join(wrong or ["command (empty)"]))
    return settings


def codex_home(project: Project) -> Path:
    value = str(project.instance.cfg.get("sources.codex_home", "") or "").strip()
    if not value:
        raise ProjectError(2, ["[sources] codex_home is not set: the folder the Codex CLI keeps its images in",
                               "set it: kiln config set sources.codex_home <dir> (docs/sources/codex.md)"])
    path = Path(value).expanduser()
    return (path if path.is_absolute() else project.instance.root / path).resolve()


def relay_text(settings: dict, prompt: str, want: int, refs: list[base.Ref]) -> str:
    """The instructions the CLI's agent receives (placeholders replaced literally; a prompt may hold braces)."""
    calls = "once" if want == 1 else f"{want} times, with the same prompt and images each time"
    if refs:
        listing = "\n".join(f"   Image {chr(ord('A') + i)}: {ref.path.name}" for i, ref in enumerate(refs))
        references = ("Pass exactly these attached images to image_gen as its referenced images, all of them, "
                      "in this order; the prompt names them by these letters:\n" + listing)
    else:
        references = "No images are attached: pass no referenced images."
    return (settings["relay"].replace("{calls}", calls).replace("{references}", references)
            .replace("{prompt}", prompt.strip()))


def parse_events(text: str) -> list[dict]:
    """The JSON objects with a `type` in the CLI's --json stream; any other line is ignored."""
    out = []
    for line in text.splitlines():
        line = line.strip()
        if not line.startswith("{"):
            continue
        try:
            obj = json.loads(line)
        except ValueError:
            continue
        if isinstance(obj, dict) and "type" in obj:
            out.append(obj)
    return out


def thread_of(events: list[dict]) -> str | None:
    for event in events:
        if event.get("type") == "thread.started" and event.get("thread_id"):
            return str(event["thread_id"])
    return None


def agent_text(events: list[dict]) -> str:
    """The agent's own messages in the stream (where a refusal would be worded)."""
    parts = []
    for event in events:
        item = event.get("item") or {}
        if isinstance(item, dict) and item.get("type") in ("agent_message", "assistant_message"):
            parts.append(str(item.get("text") or ""))
    return "\n".join(parts)


def decode(raw: bytes) -> str:
    """A log's text by its byte-order mark: some shells write UTF-16 when they tee a stream."""
    if raw[:2] == b"\xff\xfe":
        return raw.decode("utf-16-le", errors="replace")
    if raw[:2] == b"\xfe\xff":
        return raw.decode("utf-16-be", errors="replace")
    return raw.removeprefix(b"\xef\xbb\xbf").decode("utf-8", errors="replace")


def collect(images_dir: Path, thread: str | None, before: set[Path]) -> list[Path]:
    """This thread's PNGs, else every PNG new since the call (oldest first)."""
    if not images_dir.is_dir():
        return []
    if thread and (images_dir / thread).is_dir():
        found = sorted((images_dir / thread).rglob("*.png"))
        if found:
            return found
    after = set(images_dir.rglob("*.png"))
    return sorted(after - before, key=lambda p: (p.stat().st_mtime, p.name))


def read_session(sessions: Path, thread: str | None) -> dict:
    """What the saved session says happened; {} when there is none. Provenance only: a parse error never fails."""
    if not thread or not sessions.is_dir():
        return {}
    out: dict = {"generations": [], "included": [], "quota": None}
    for path in sorted(sessions.rglob(f"*{thread}*.jsonl")):
        for line in decode(path.read_bytes()).splitlines():
            try:
                payload = json.loads(line).get("payload") or {}
            except (ValueError, AttributeError):
                continue
            item = payload.get("item") or {}
            if isinstance(item, dict) and item.get("kind") == "image_gen.generation":
                out["generations"].append({"prompt": item.get("revisedPrompt"), "status": item.get("status")})
            if payload.get("type") in ("custom_tool_call", "function_call"):
                code = str(payload.get("input") or payload.get("arguments") or "")
                if "image_gen" in code or "imagegen" in code:
                    out["included"] += [int(x) for x in re.findall(r"num_last_images_to_include\s*:\s*(\d+)", code)]
            limits = payload.get("rate_limits")
            if isinstance(limits, dict) and isinstance(limits.get("primary"), dict):
                out["quota"] = {"window_used_percent": limits["primary"].get("used_percent"),
                                "window_resets_at": _when(limits["primary"].get("resets_at")),
                                "weekly_used_percent": (limits.get("secondary") or {}).get("used_percent"),
                                "weekly_resets_at": _when((limits.get("secondary") or {}).get("resets_at"))}
    return out


def _when(stamp: object) -> str | None:
    try:
        return datetime.fromtimestamp(float(stamp)).isoformat(timespec="minutes") if stamp else None
    except (TypeError, ValueError, OverflowError, OSError):
        return None


def _executable(settings: dict) -> list[str] | None:
    first = str(settings["command"][0])
    found = shutil.which(first) or (first if Path(first).is_file() else None)
    return [found, *[str(w) for w in settings["command"][1:]]] if found else None


def _version(command: list[str]) -> str | None:
    try:
        done = subprocess.run([*command, "--version"], capture_output=True, text=True, encoding="utf-8",
                              errors="replace", timeout=30)
    except (OSError, subprocess.SubprocessError):
        return None
    return (done.stdout or "").strip().splitlines()[0][:80] if (done.stdout or "").strip() else None


class CodexSource(base.Source):
    kind = "codex"
    makes_images = True

    def check(self, job: base.Job, ctx: base.RunContext) -> list[str]:
        limit = int(ctx.settings.get("max_references", 5))
        if len(job.refs) > limit:
            return [f"job {job.id}: {len(job.refs)} images attached; the image tool takes at most {limit} (the "
                    "edit target included): drop references rather than let the relay choose which to drop"]
        return []

    def describe(self, job: base.Job, ctx: base.RunContext) -> list[str]:
        model = ctx.settings.get("model") or "the CLI's default model"
        lines = [f"codex: {job.want} call(s) of the image tool, {len(job.refs)} attached, {model}"]
        lines += [f"  attach Image {chr(ord('A') + i)}: {ref.name} ({ref.role or 'identity'})"
                  for i, ref in enumerate(job.refs)]
        return lines

    def run(self, job: base.Job, ctx: base.RunContext) -> base.Outcome:
        settings, project = ctx.settings, ctx.project
        codes = settings["exit_codes"]
        command = _executable(settings)
        if command is None:
            raise ProjectError(2, [f"{settings['command'][0]} is not on PATH: install the Codex CLI and log in "
                                   "(docs/sources/codex.md)"])
        home = codex_home(project)
        images_dir = home / settings["images_dir"]
        workdir = project.instance.root / str(project.instance.cfg.get("paths.runtime", "runtime") or "runtime") \
            / "codex-wd"
        workdir.mkdir(parents=True, exist_ok=True)
        refs = job.refs
        before = set(images_dir.rglob("*.png")) if images_dir.is_dir() else set()
        manifest: dict = {"model": settings.get("model"), "tool_version": _version(command)}
        with tempfile.TemporaryDirectory(prefix="kiln-codex-") as tmp:
            schema = Path(tmp) / "result.schema.json"
            schema.write_text(json.dumps(RESULT_SCHEMA), encoding="utf-8")
            argv = [*command, *[str(a) for a in settings["exec_args"]], "-C", str(workdir),
                    "--output-schema", str(schema)]
            if settings.get("model"):
                argv += ["-m", str(settings["model"])]
            for ref in refs:
                argv += ["-i", str(ref.path)]
            argv += ["--", "-"]  # `-i` takes several values: `--` ends them, `-` = the prompt from stdin
            env = {k: v for k, v in os.environ.items() if k not in set(settings["drop_env"])}
            env["CODEX_HOME"] = str(home)
            env["PYTHONIOENCODING"] = "utf-8"
            try:
                done = subprocess.run(argv, input=relay_text(settings, job.prompt, job.want, refs), text=True,
                                      encoding="utf-8", errors="replace", env=env, capture_output=True,
                                      timeout=int(settings["timeout_s"]))
            except subprocess.TimeoutExpired:
                return base.Outcome("failed", int(codes.get("failed", 1)), [],
                                    [f"codex timed out after {settings['timeout_s']} s"],
                                    {**manifest, "exit_code": -1, "errors": ["timeout"]})
        raw = (done.stdout or "") + (done.stderr or "")
        events = parse_events(raw)
        thread = thread_of(events)
        files = []
        for path in collect(images_dir, thread, before):
            info = image_meta.probe(path, sha256=False)
            if info.get("readable") and info.get("format") == "png":
                files.append(path)
        session = read_session(home / settings["sessions_dir"], thread)
        generations = session.get("generations") or []
        manifest.update({
            "thread_id": thread, "exit_code": done.returncode,
            "verbatim": (all((g.get("prompt") or "").strip() == job.prompt.strip() for g in generations)
                         if generations else None),
            "refs_sent": [f"{n} image(s) included" for n in session.get("included") or []] or None,
            "quota": session.get("quota"),
            "failed_generations": sum(1 for g in generations if g.get("status") != "completed"),
            "moderation_blocked": raw.count(settings["moderation_marker"]),
            "errors": [line.strip()[:300] for line in raw.splitlines() if "ERROR" in line][:20],
        })
        log = ctx.jobs.out_dir / f"{job.id}.codex.jsonl"
        log.parent.mkdir(parents=True, exist_ok=True)
        log.write_text(raw, encoding="utf-8")
        lines = [f"codex exit {done.returncode}; thread {thread or 'unknown'}; {len(files)} image(s)"]
        if generations:
            lines.append(f"  prompt received verbatim: {manifest['verbatim']}")
        if files:
            return base.Outcome("ok", int(codes.get("ok", 0)), files, lines, manifest)
        low_stream, low_agent = raw.lower(), agent_text(events).lower()
        if manifest["moderation_blocked"]:
            status, why = "blocked", f"output moderation refused {manifest['moderation_blocked']} generation(s)"
        elif any(h.lower() in low_agent for h in settings["refusal_hints"]):
            status, why = "refused", "the relay refused the request (its refusal wording)"
        elif any(h.lower() in low_stream for h in settings["quota_hints"]):
            status, why = "quota", "the usage window looks spent: stop and retry after it resets"
        elif done.returncode != 0:
            status, why = "failed", f"the CLI exited {done.returncode} without an image"
        else:
            status, why = "empty", "no image came back; read the log, never retry blindly"
        manifest["note"] = why
        lines.append(f"  {status}: {why} (log {project.shown(log)})")
        return base.Outcome(status, int(codes.get(status, 1)), [], lines, manifest)
