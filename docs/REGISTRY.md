# Reading the registry (kiln)

kiln keeps each project as plain files in your instance (`kiln config show` names the folder). You read them
through commands, never by opening and editing them: the commands apply the rules (lineage, splits, the
latest event per key) and keep the generated views in step. This page maps the questions you will be asked
to the command that answers them. A command whose verb is not built yet prints `planned: <task> (<help>)`
and exits 2; the last column names that task.

## Question -> command

| question | command | lands with |
|---|---|---|
| Where does the project stand, and what is next? | `kiln status`, then `kiln next` | T7 |
| Which images does the project have, with which disposition and split? | `kiln images list` | T7 |
| Which images still wait for the operator's verdict? | `kiln images list --untriaged`, then `kiln review sheet` | T8 |
| Which kept images lack a checklist verdict? | the `check:<key>` columns of `kiln review sheet --all` | T8 |
| Which kept images still need an approved caption? | `kiln captions status` | T7 |
| Does a derived image sit on the other side of the split from its parent (a leak)? | `kiln dataset check --leaks` | T7 |
| How many images per reference, label or split? | `kiln dataset census`, `kiln dataset census --split` | T7 |
| Which images came from an easel run, and with whose verdict? | `kiln images list` (the `verdict_source` field) | T7 |
| Which base do I train on? | `kiln runs list --family <f> --status ok`, then `kiln train plan <version> --family <f> --base-from-run <run-id> --steps <n>` | T9 |
| Continue the existing LoRA or start fresh? | the operator decides; record it with `--continue-from <sha16\|path>` on `kiln train plan` | T9 |
| Which licence does the base carry into the LoRA? | the `licence` block that `kiln train plan` copies into the run's `run.json` | T9 |
| Is the run ready to start? | `kiln train preflight <version>` | T9 |
| Which decisions are open? | `kiln decide list --open` | T7b |
| Which coverage gaps are open? | `kiln coverage needs` | T7b |
| Which checkpoint scores best? | `kiln runs leaderboard` | session-2 |
| Which checkpoints are not evaluated yet? | `kiln runs unevaluated` | session-2 |
| Which models do I have, and which LoRAs fit a checkpoint? | `easel registry list`, `easel registry compatible <sha16>` (easel) | T2 |

When the answer needs a judgement (is this image good, is this split right, fresh run or continue), the
command gives the facts and the operator decides. Never fill the gap yourself.

**Fresh or continue.** `--continue-from` starts from an existing LoRA's weights (sd-scripts'
`--network_weights` on the native path). On an adopted instance the host's launcher template has no
slot for it until cutover, so `kiln train plan --emit-legacy --continue-from ...` prints
`continue-from: unsupported by the host template until cutover` and exits 2, and the operator chooses a
fresh run from the base or waits for the cutover.

## Rules the commands keep

- **Lineage:** an image and every image derived from it sit on the same side of the train/holdout split.
  `kiln dataset split` never separates a lineage, and `kiln dataset freeze` refuses a version with a leak
  or an uncaptioned train image.
- **Latest event wins:** in every `*.jsonl` log the latest line per key is the current state; changing a
  verdict or a caption appends a line, it never rewrites one.

## Where the files live (in the instance, never in this repo)

Per project, under `projects/<slug>/`:

| what | file |
|---|---|
| the project: type, trigger, roots of its images | `project.yaml` |
| the project's state, including the goal that `next` routes by (set with `kiln project goal set`) | `state.json` |
| every image event (source, root, disposition, split, labels, verdict source) | `images.jsonl` |
| one caption per image, as events | `captions.jsonl` |
| the coverage board | `coverage.yaml` |
| a frozen dataset version: its members and their hashes | `snapshots/<version>.json` |
| a materialised version, ready for the trainer | `datasets/<version>/training/`, `holdout/`, `_meta/materialized-<version>.json` |
| a planned or finished run (base, its licence, `continue_from`) | `runs/<run-id>/run.json` |
| evaluation suites | `evals/suites/<id>.yaml` |
| scores, pairwise preferences and picks | `review/scores.jsonl`, `review/pairs.jsonl`, `review/picks.jsonl` |
| decisions | `decisions/dec-<nnnn>.md` |
| triage and caption sheets the operator fills | `sheets/review-<date>-<nnn>.csv`, `sheets/captions-<date>-<nnn>.csv` |
| generated views (rebuilt by `kiln index`, planned for session 2; do not edit) | `index.jsonl`, `INDEX.md` |

Each image line carries `root` beside `file`: `images` (the configured `images_root`, read-only on an
adopted project), `imports` (copies made by `kiln images import`, under `projects/<slug>/images/`) or
`legacy_output` (an adopted workspace's output folder, read-only). Three
kinds of file: **records** (one YAML or JSON file per thing), **event logs** (append-only `*.jsonl`; a torn
last line is skipped) and **generated views**. Human-owned and tool-owned fields never share a file. Field
by field: [reference/formats.md](reference/formats.md).
