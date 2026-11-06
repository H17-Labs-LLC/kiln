# kiln

kiln is an AI-guided, file-based workflow for making LoRAs on one consumer GPU. It walks a person, or an AI agent
working for them, through the stages of a project (subject spec, dataset images, captions, a lineage-safe
split, a frozen dataset version, a planned and preflighted training run, evaluation, a release folder), with one
runbook per stage and `kiln next` naming the next command. Projects live as plain YAML, JSON and JSONL files in
your own instance folder, outside this repository, so they can be read, diffed and backed up; GPU work is
submitted through a scheduler, never started blind, and only a human judges images. Three LoRA types ship
(character-anime, character-human, style), and subjects are fictional adults only. This is early work (v0.1 in
development): a verb that is not built yet prints `planned: <task>` and exits 2. Agents start at
[AGENTS.md](AGENTS.md); contributors read [CONTRIBUTING.md](CONTRIBUTING.md).

## Install

```
git clone https://github.com/H17-OpenSource/kiln && cd kiln
python -m venv .venv && .venv/Scripts/pip install -e ".[dev]"      # Linux/macOS: .venv/bin/pip
kiln init ~/kiln-home && kiln doctor
kiln config set comfyui.url http://127.0.0.1:8188      # evaluation renders go through ComfyUI; this is its upstream default
# training runtime installer: session 2 (the pinned trainer packages are listed in runtime/train.lock.txt)
```

## Platforms

Developed and tested on Windows 11 with an 8 GB NVIDIA GPU; Linux runs the offline suite in CI, GPU on
Linux untested until a contributor reports; macOS metadata and review only.

## Licence

MIT, see [LICENSE](LICENSE). The trainer (kohya sd-scripts, Apache-2.0) is cloned at a pinned commit by the
installer, never vendored. Not legal advice.
