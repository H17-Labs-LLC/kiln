"""Image sources: where a project's candidate images come from, behind one interface (`kiln.sources.base`).

Four kinds (the `source` of a jobs file, the `backend` of a generation manifest):
- `files`: a folder you already have (`kiln images import --from-folder <dir>`), see `files.py`;
- `codex`: images made by the Codex CLI's image tool from a jobs file (`kiln source run <jobs.json>`), `codex.py`;
- `comfy`: renders by your own ComfyUI from a jobs file; planned (session 2), dry run only, `comfy.py`;
- `easel-run`: the kept outputs of a reviewed easel run (`kiln images import --from-easel-run <run-id>
  --keep-only`), `easel_run.py`.

Every kind ends the same way (`base.py`): new bytes are copied, sha256 checked, into the project's imports root
and registered through `kiln.project.images.register`. Caption drafts from each image's source prompt are
`propose.py` (`kiln captions propose`).
"""

KINDS = ("files", "codex", "comfy", "easel-run")


def get(kind: str):
    """The source object for `kind` (imported lazily, so one broken source cannot break the others)."""
    if kind == "codex":
        from .codex import CodexSource

        return CodexSource()
    if kind == "comfy":
        from .comfy import ComfySource

        return ComfySource()
    if kind == "files":
        from .files import FilesSource

        return FilesSource()
    if kind == "easel-run":
        from .easel_run import EaselRunSource

        return EaselRunSource()
    raise ValueError(f"a source is one of {', '.join(KINDS)}: {kind!r}")
