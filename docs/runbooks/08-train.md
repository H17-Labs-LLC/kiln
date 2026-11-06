# 08-train: plan and preflight a training run

Reads a frozen, materialised version (07). Training is GPU work: you plan and check it; you never start it.
Stage map: [README.md](README.md).

## 1. Purpose and who decides

Write `runs/<run-id>/run.json` (status `planned`) with the dataset version, the family, the base model and
its licence, the steps and, if chosen, the LoRA to continue from; then run the preflight gates.

- **The operator decides:** the base model, a fresh run from the base or a continuation of an earlier LoRA
  (native instances only, see below), the step count, and the go ("train") for every run.
- **You:** plan, preflight, report the wall clock and the RAM the run needs, and hand over the start line.
  A training command without `--dry-run` is never typed by you.

## 2. Entry conditions

| command | literal line |
|---|---|
| `kiln next --stage` | `08-train` |

## 3. Commands

`train plan` writes only the planned record and prints the plan; it starts nothing. Use one of the three
plan lines.

```
kiln runs list --family <family> --status ok
kiln train plan <version> --family <family> --base-from-run <run-id> --steps <n>
kiln train plan <version> --family <family> --base <sha16|path> --steps <n>
kiln train plan <version> --family <family> --base-from-run <run-id> --continue-from <lora sha16|path> --steps <n>
kiln train preflight <version>
```

- `--base-from-run <run-id>` copies the base's sha256, label and `licence{}` from an earlier run;
  `--base <sha16|path>` names it directly. Pick one.
- `--continue-from <lora sha16|path>` records `continue_from` in `run.json`; on a native instance it becomes
  the trainer's starting-weights option. `--save-every` and `--validate-every` set the save and
  validation intervals.
- `train plan` prints the wall clock. The host RAM the run waits for comes from `families/<family>.yaml`
  (`host_ram_gb.train` to train, `host_ram_gb.eval` for the eval render; `eval_includes_comfyui_server`
  says whether the server is counted).
- `kiln train preflight <version>` prints one line per gate (`ok`, `TODO` with the command that fixes it, or
  `WAIT`), then `next:`. It exits 0 when every gate but the last is ok, 1 otherwise. The last gate is the
  operator's go, which reads `WAIT` until the operator says "train".
- **Adopted workspace:** add `--emit-legacy` to `train plan`. It prints `legacy run id: <host run id> (next
  free in the host registry)` (`--legacy-run-id <id>` overrides), renders the host's launcher and dataset
  config, writes the host queue file, and prints two host lines: the run-registry line (with
  `--notes "dataset: kiln <slug>/<version> members_sha256=<sha>"` and **no** `--snapshot`) and the queue
  line. Every host fact comes from the instance's `[legacy]` table. Details:
  [90-adopt-existing.md](90-adopt-existing.md).
- **Continuing an earlier LoRA on an adopted workspace is not supported until cutover.** The host launcher
  template has no starting-weights variable, so `--emit-legacy` with `--continue-from` refuses with
  `continue-from: unsupported by the host template until cutover` (exit 2). There the plan is a fresh run
  from the base; the operator may instead wait for the cutover.
- **Native instance:** the training launcher, queue, smoke and tune verbs (`kiln train launch`, `queue`,
  `smoke`, `tune`) are planned for session 2 and print `planned: session-2`, as do `kiln runs leaderboard`
  and `kiln runs unevaluated`. Stop after the preflight and hand the plan over.

## 4. Expected output

On the demo after runbook 07. The wall clock and the RAM figures come from the family file and are written
`<...>` here; free RAM and disk are the machine's (`<x>`). The RAM gate reads `ok` when enough RAM is free and
`WAIT` (with exit 1) when it is not.

```
$ kiln runs list --family illustrious --status ok
run-20300102-001 | ok | illustrious | demo_robot/0.2.0 | base Demo Anime XL (licence unverified) | steps 1000/1000 | picked 800
1 run(s) (family illustrious, status ok)
next: kiln train plan <version> --family illustrious --base-from-run run-20300102-001 --steps <n>

$ kiln train plan 0.3.0 --family illustrious --base-from-run run-20300102-001 --steps 800
planned run-20300101-001: demo_robot/0.3.0 on illustrious, 800 steps (save every 200, validate every 200)
  base       Demo Anime XL (bbbabeef2c0d57fe, from run run-20300102-001); licence Demo Open Licence (unverified)
  continue   none: a fresh run from the base
  wall clock about <n> min (<h> h): families/sdxl.yaml (inherited by illustrious) measured_8gb: <s> s per step plus <m> min load
  host RAM   <train GB> GB to train, <eval GB> GB for the eval render (includes the ComfyUI server)
  eval suite core
  record     projects/demo_robot/runs/run-20300101-001/run.json
next: kiln train preflight 0.3.0

$ kiln train preflight 0.3.0
preflight demo_robot/0.3.0 (run run-20300101-001, family illustrious)
  ok    snapshot           demo_robot/0.3.0 frozen: 15 members (13 train, 2 holdout)
  ok    triage             the version is frozen; 0 later image(s) without a disposition wait for the next version
  ok    captions           every train member has its caption
  ok    split              fixed by the frozen version
  ok    leaks              no lineage on both sides of the split
  ok    materialised       projects/demo_robot/datasets/0.3.0 matches the snapshot (13 train, 2 holdout)
  ok    disk               <x> GB free (need 20 floor + 10 for the run)
  ok    RAM                <x> GB free (training starts at <train GB>)
  ok    run planned        run-20300101-001: illustrious, 800 steps, base licence unverified
  WAIT  operator's go      training starts only on the operator's word "train"
next: all gates pass; ask the operator for the go and wait for the word "train"
```

Match the shape, not the numbers (see [README.md](README.md)).

## 5. Gate and operator question

1. `run.json` exists with status `planned`, the base's licence and, if chosen, `continue_from`.
2. Every preflight gate reads ok except the operator's go (`WAIT`).
3. The operator answered the question below with "train" in their own words, quoted in the journal.

Then hand the start over. On an adopted workspace, the two printed host lines go to the operator, who runs
them on the host with the instance's ComfyUI settings for the eval renders. You never run the queue line,
not even after the go. On a native instance, the plan and the preflight output go to the operator.

Ask once, then wait. On a native instance:

> Training plan for `<slug>` `<version>`: family `<family>`, base `<base label>` (licence `<licence name>`),
> `<n>` steps, about `<h>` h wall clock, host RAM `<train GB>` GB to train and `<eval GB>` GB for the eval
> render. Choose: fresh run from base, or continue from the previous LoRA `<sha16>`. Reply "train" to start
> it, or tell me what to change.

On an adopted workspace (continuing is not supported until cutover):

> Training plan for `<slug>` `<version>` on the host: run `<host run id>`, family `<family>`, a fresh run
> from base `<base label>` (licence `<licence name>`), `<n>` steps, about `<h>` h wall clock, host RAM
> `<train GB>` GB to train and `<eval GB>` GB for the eval render. The two host lines are below. Reply
> "train" and run them on the host, or tell me what to change.

## 6. Symptom, cause, action

| symptom | cause | action |
|---|---|---|
| `continue-from: unsupported by the host template until cutover` (exit 2) | the host launcher template has no variable for starting weights | ask the operator; with "fresh", plan again without `--continue-from`; a continuation waits for the cutover |
| `--emit-legacy needs [legacy] <keys> in kiln.toml` (exit 2) | the instance config lacks those host facts | report the keys; the operator gives each value, then `kiln config set legacy.<key> <value>` with their exact words; never guess a host path |
| `REFUSED: [legacy] generated_dir lies outside the legacy root` (or `queue_dir`) | kiln writes only inside the host workspace | report it; operator: `kiln config set legacy.generated_dir <folder inside the root>` (or `legacy.queue_dir`), or a `--legacy-root <dir>` that holds it |
| `[legacy] <key> names no file` (`renderer`, `launcher_template`, `dataset_template`) | the value points at a file that is not under the legacy root | report it; the operator names the file, then `kiln config set legacy.<key> <value>` |
| `REFUSED: <slug>/<version> is not materialised` | the host launcher trains on the materialised folder | `kiln dataset materialize <version>`, then plan again |
| `REFUSED: ... does not match its snapshot` | the materialised folder differs from the snapshot | `kiln dataset check <version>`; then runbook 07 |
| `REFUSED: the kiln instance lies outside the legacy root` | the host tools cannot reach the instance's launcher and dataset folder | report it; operator: keep the instance inside the host workspace |
| `REFUSED: family <f> has no training recipe kiln supports` | the family file does not say `training.supported: true` | pick an SDXL-based family (`kiln families check` lists each status), on the operator's word |
| `REFUSED: <host run id> is already in the host registry` | a run was registered since the plan | plan again for the next free id, or `--legacy-run-id <id>` from the operator |
| `REFUSED: the host's generated folder already holds files for <id>` or `queue-<id>.json exists` | an earlier plan for that id left its files | report it; operator: archive those host files, or give another id with `--legacy-run-id <id>` |
| `kiln runs list` prints no run | no finished run of that family in this instance | ask the operator for the base: `--base <sha16|path>` |
| the base's licence reads `status: unknown` | nobody has read the base's licence yet | tell the operator now; a later release build refuses an unknown base licence |
| a preflight gate names the dataset | the version is not materialised or its counts differ | back to [07-freeze-and-gate.md](07-freeze-and-gate.md) |
| preflight `RAM ... WAIT`, or `waiting: free <x> GB < <y> GB needed by family <family>` | not enough free host RAM yet | let it wait; if it waits long, tell the operator; never free memory or lower a threshold yourself |
| `cannot measure free RAM; set limits.skip_ram_check = true to skip` | the RAM reading failed on this machine | the operator decides; never set the key yourself |
| preflight `disk ... TODO`: `<x> GB free, need <floor> floor + <n> for the run` | free disk is below `limits.disk_floor_gb` plus room for the run's checkpoints | tell the operator; lowering the floor or freeing disk is their call |
| `family <f>: host_ram_gb.<step> is not measured; waiting for limits.host_ram_floor_gb = <n> GB instead` | the family file has no measurement for that step | expected; the operator can measure it and fill the family file |
| `waiting: free disk <x> GB < <y> GB floor (limits.disk_floor_gb)` | a submitted job waits for disk | let it wait; tell the operator |
| `cannot measure free disk space; nothing starts` | the disk reading failed | report it; nothing starts until it can be measured |
| `scheduler unreachable: <verb> ...` then `failing closed: nothing starts (scheduler.fail_open = false)` (exit 3) | the external scheduler in `[scheduler]` did not answer | report it; never set `scheduler.fail_open` yourself |
| the host renderer exits 3 | a host template (`[legacy] launcher_template` or `dataset_template`) holds a literal absolute path | report the renderer's line; operator: fix that host template, or point the key at one with relative paths (`kiln config set legacy.launcher_template <file>`); never edit a generated file |
| the host renderer exits 2 | the template uses a variable kiln does not supply | report the line with the variable's name; operator (the host template) or maintainer (kiln should supply it) |
| the printed registry line has no `--snapshot` | by design: the host registry would reject a kiln-only snapshot | never add it; the notes carry the kiln snapshot until cutover |
| eval renders went to the wrong ComfyUI | the host queue was started without the instance's ComfyUI settings | report it; operator: the queue's start must carry the instance's `comfyui.url`, `comfyui.dir` and `comfyui.python`; see [docs/troubleshooting/training.md](../troubleshooting/training.md) |
| `planned: session-2` from `kiln train launch`, `queue`, `smoke`, `tune` or `kiln runs leaderboard` | those verbs are not built yet | report it once and stop at this stage |
| two runs are compared across another rank, resolution or dataset | the comparison stops meaning anything | compare only runs that differ in one thing |

Trainer and memory failures: [docs/troubleshooting/training.md](../troubleshooting/training.md).
Not in the table: follow [docs/STUCK.md](../STUCK.md).

## 7. Record

```
kiln journal note "08-train: planned <run-id> on <version>, <family>, base <label>, <n> steps; go: <operator's words>"
```

A new pitfall: add a row to the table in section 6, dated from `date`, and a journal line, in the same session.
Stuck: `kiln journal stuck "<one line>"`, then [docs/STUCK.md](../STUCK.md).
