# Changelog

Notable changes to kiln, newest first. The format follows Keep a Changelog
(https://keepachangelog.com/en/1.1.0/), and versions will follow Semantic Versioning from v0.1.0 on. Every pull
request adds one line under "Unreleased"; the release commit moves those lines under the new version and its
date.

## Unreleased

### Added
- Packaging, the `kiln` command table (a verb that is not built yet prints `planned: <task>` and exits 2), the
  MIT licence, the README and the rule file for AI agents (`AGENTS.md`).
- The shared core in `src/kiln/_common/`, a byte-identical copy of easel's (ADR-0001): configuration, the file
  store (atomic writes, OS locks, event logs, generated indexes), ids that honour the frozen clock, hashing, the
  journal with its one STUCK template, and the privacy scanner (`kiln privacy scan`).
- JSON Schemas for every record type, with examples, and the synthetic demo project in `examples/demo/`.
- Three LoRA types (`character-anime`, `character-human`, `style`) and `kiln types check`; model family files
  with licence summaries, host RAM needs and training blocks, and `kiln families check`.
- The hand-pinned training runtime lock (`runtime/train.lock.txt`).
- The stage table, the first runbooks, `docs/STUCK.md`, `docs/REGISTRY.md`, `docs/sources/codex.md`, the format
  reference and the training and publishing troubleshooting pages.
- The contributor kit: `CONTRIBUTING.md`, `CODE_OF_CONDUCT.md` (Contributor Covenant 2.1), `SECURITY.md`, issue
  and pull request templates, `CODEOWNERS`, ADR-0002 on secret scanning and `docs/lessons/privacy.md`.
- CI on Linux and Windows with every gate, including the pip-licenses `--allow-only` licence gate (proven by
  `tests/test_licence_gate.py`) and a gitleaks job pinned by version and checksum.
- `tools/pre-push-gate` with `tools/install-hooks.py`, and `tools/gen_notices.py` with `THIRD_PARTY_NOTICES.md`.
- The logo (#1): the concept redrawn as a parametric vector mark, with the word mark, the lockups, a small cut for
  48 px and below, the icons and its construction, in `docs/brand/`; the README header shows it.
