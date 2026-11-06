"""The `comfy` source: dataset candidates rendered by your own ComfyUI from a jobs file. Planned (session 2).

A ComfyUI render is GPU work, so it is submitted through the scheduler, never started by kiln directly; that
path is not built yet. `kiln source run <jobs.json> --dry-run` already resolves and prints every job (its prompt,
references and count) for a comfy jobs file; without `--dry-run` the source refuses with a pointer to what works
today: render with easel (`easel jobs submit`, or the host runner on an adopted instance), review the outputs
there, then `kiln images import --from-easel-run <run-id> --keep-only`.
"""

from __future__ import annotations

from kiln.project.model import ProjectError

from . import base

PLANNED = "planned: session-2 (the comfy source submits its renders through the scheduler)"


class ComfySource(base.Source):
    kind = "comfy"
    makes_images = True

    def describe(self, job: base.Job, ctx: base.RunContext) -> list[str]:
        cfg = ctx.project.instance.cfg
        url = str(cfg.get("sources.comfy_url", "") or "") or str(cfg.get("comfyui.url", "") or "") or "not set"
        return [f"comfy: {job.want} render(s) on {url}, submitted through the scheduler ({PLANNED})"]

    def run(self, job: base.Job, ctx: base.RunContext) -> base.Outcome:  # noqa: ARG002 - the interface
        raise ProjectError(2, [PLANNED,
                               "render with easel now and import the kept outputs: "
                               "kiln images import --from-easel-run <run-id> --keep-only"])
