# 90-adopt-existing: work in an existing (adopted) workspace

An adopted workspace keeps its own tools, registry and training runner; kiln reads an export of it and plans
for it until cutover. Every host fact (paths, command templates, the host run registry) lives in the
instance's `kiln.toml` (`[legacy]`, `[adopt]`, `[env_aliases]`), never in this repo or its docs.
Stage map: [README.md](README.md).

## 1. Purpose and who decides

Run the normal stages (03 -> 05 -> 06 -> 07 -> 08) on an adopted project, with the differences below.

- **State lives** in the instance folder (`<home>`): `projects/<slug>/` holds the exported project, and a
  trial's protocol, if one runs, sits beside it (`TRIAL.md`). The host's own databases are never written.
- **The operator decides** the goal (which new version, from which easel run), every verdict, and the go for
  training. The host runner starts all GPU work.
- **You:** set nothing the operator did not say, follow `kiln next`, and hand over the host lines it prints.

## 2. Entry conditions

| command | literal line |
|---|---|
| `kiln next --stage` | `90-adopt-existing` |

That line means: the project was adopted from an existing workspace and no goal is set yet. Once the goal is
set, the same command prints `03-dataset-images` and the stage runbooks take over, with the differences below.

## 3. Commands

Step 1 is the easel run id; step 2 the goal. After the goal, `next` routes 03 -> 05 -> 06 -> 07 -> 08 and
ignores the legacy rows marked `legacy_untriaged`. Run `kiln next` after each block and follow the runbook it
names; the full sequence of one round is:

```
kiln project show
easel runs import-legacy <legacy-run-id> --with-reviews
kiln project goal set --new-version <version> --from-easel-run <easel-run-id>
kiln next
kiln images import --from-easel-run <easel-run-id> --keep-only
kiln next
kiln captions status
kiln captions propose --store
kiln captions sheet
kiln captions approve --sheet <sheet-file>
kiln captions apply
kiln captions lint
kiln next
kiln dataset split --holdout <n>
kiln dataset split --holdout <n> --apply
kiln dataset check --leaks
kiln next
kiln dataset freeze <version>
kiln dataset materialize <version>
kiln dataset check <version>
kiln next
kiln train plan <version> --family <family> --base-from-run <base-run-id> --steps <n> --emit-legacy
kiln train preflight <version>
kiln project goal clear
```

- **Where `<easel-run-id>` comes from.** The operator names the host run that holds the reviewed images
  (`<legacy-run-id>`, the id the host's submit or status line printed). `easel runs import-legacy
  <legacy-run-id> --with-reviews`, run in the easel instance, prints
  `imported <legacy-run-id> as <easel-run-id>: ...` (or `<easel-run-id> already holds <legacy-run-id>; ...`
  when it was imported before), then `<n> reviews imported`. That printed `<easel-run-id>` is the one for
  `goal set` and the import. `0 reviews imported` means the operator has not reviewed the run yet: stop and
  ask (easel runbook 90).
- `<base-run-id>` in `train plan` is a kiln run of this project whose base model the new run reuses
  (`kiln runs list --family <family> --status ok`); it is not the easel run.
- The import copies the kept outputs into `projects/<slug>/images/<easel-run-id>/` (`root: imports`);
  exported legacy rows keep `root: images`.
- Captions: the operator approves every row of the sheet (runbook 05). The split: `--apply` only after the
  operator's yes to the proposal (runbook 06); a new image that stays unassigned would join train at the
  freeze, with no holdout from this round.
- `freeze <version>` alone means `--from <the goal's from_version>`, else `--from <latest snapshot>`, here;
  `--only-new` only on the operator's word.
- `materialize` writes only under the project's `datasets/<version>/` and reads legacy members from the
  adopted image folder read-only; `kiln dataset check <version>` must exit 0 before the plan.
- `train plan --emit-legacy` reads every host fact from `[legacy]`: it proposes the next free host run id,
  renders the launcher and dataset config through the host's renderer, writes the queue file and prints the
  host run-registry line (no `--snapshot`) and the queue line. Both lines go to the operator, who runs them
  on the host; you never run the queue line. See [08-train.md](08-train.md).
- A continuation of an earlier LoRA is not supported by the host launcher template until cutover: the plan
  is a fresh run from the base (runbook 08).
- The host registry never learns kiln snapshots: until cutover the host run row carries the snapshot in its
  notes (`dataset: kiln <slug>/<version> members_sha256=<sha>`).
- Files that configure an agent host for one machine belong to the instance, never to this repository.

## 4. Expected output

On a copy of the demo whose `project.yaml` says `adopted: true`, with its goal cleared first. The demo's
easel run is already imported, so after the goal `next` goes straight to the split:

```
$ kiln next --stage
90-adopt-existing

$ kiln project goal set --new-version 0.3.0 --from-easel-run run-20300101-001
goal set for demo_robot: new version 0.3.0 from 0.2.0 (easel run run-20300101-001)
next: kiln next

$ kiln next
stage: 06-split
read: docs/runbooks/06-split.md
next: kiln dataset census
```

The easel side prints, in the easel instance, `imported <legacy-run-id> as <easel-run-id>: <k> of <n> outputs
copied to runs/<easel-run-id>/outputs/` and then `<n> reviews imported` (easel runbook 90). The import,
caption, split and freeze lines are the ones in runbooks 03 to 07. `train plan --emit-legacy` prints the plan
of runbook 08 plus `legacy run id: <host run id> (next free in the host registry)` and the two host lines;
its exact output needs a host workspace, which the demo does not ship.

Match the shape, not the numbers (see [README.md](README.md)).

## 5. Gate and operator question

1. The goal is set and `kiln next --stage` follows 03 -> 05 -> 06 -> 07 -> 08.
2. Each stage's own gate holds (see its runbook).
3. At 08: the preflight's operator gate reads `WAIT` until the operator says "train"; the two host lines are
   handed to the operator, not run by you.
4. When the version is trained and handed over, `kiln project goal clear` on the operator's word.

Ask for the goal once, then wait:

> This workspace is adopted. Which version should the next round become (`<version>`), and which host run
> holds the reviewed images (`<legacy-run-id>`)? Reply with both; I will import that run into easel and set
> the goal from the easel run id it prints.

## 6. Symptom, cause, action

| symptom | cause | action |
|---|---|---|
| `kiln next --stage` keeps printing `90-adopt-existing` | no goal is set; the legacy rows nobody triaged stay untriaged | ask for the goal (section 5), then `kiln project goal set ...` |
| `goal set` or the import exits 2: `an easel run id looks like ...`, or the import says `no easel run <id>` | the id given is the host's run id, or the run was never imported into easel | use the `<easel-run-id>` that `easel runs import-legacy` printed (`imported <legacy-run-id> as <easel-run-id>`); correct the goal with `kiln project goal set` and the right id |
| the import exits 2: `no easel instance named: pass --easel-home <dir>` | kiln cannot find the easel instance | add `--easel-home <dir>` with the folder the operator names (or set `[sources] easel_home` once with `kiln config set`) |
| `0 images registered` from `--from-easel-run` | the operator has not reviewed the run yet (`0 reviews imported` on the easel side) | stop; ask the operator to review in the host's own review page; continue when they say done |
| `easel runs import-legacy` says the database has no `run_review` table | the easel instance's `adopt.run_registry` names the wrong host database | report it; operator: point `adopt.run_registry` at the host database that holds the runs and their reviews |
| the freeze took the new images as train only | the split was not applied before the freeze | the version is frozen; tell the operator; the next version can hold some out (runbook 06 before 07) |
| `materialize` refuses the target | the target is inside the adopted image folder (read-only until cutover) | use the default target under the project |
| `continue-from: unsupported by the host template until cutover` | the host launcher has no starting-weights variable | ask the operator; plan a fresh run from the base without `--continue-from`, or wait for the cutover |
| `--emit-legacy needs [legacy] <keys> in kiln.toml` | the instance config lacks those host facts | report the keys; the operator gives each value; `kiln config set legacy.<key> <value>` with their exact words |
| someone wants `--snapshot` on the host registry line | the host registry's key would reject a kiln-only snapshot | never add it; the notes carry the snapshot |
| the proposed host run id is already used | a run was registered since the plan | plan again, or `--legacy-run-id <id>` on the operator's word |
| the host renderer exits 2 or 3 | a variable kiln does not supply (2) or a literal absolute path (3) in a host template | report the line; operator: fix the host template, or point `legacy.launcher_template` / `legacy.dataset_template` at a fixed one (`kiln config set legacy.<key> <value>`); never edit a generated file |
| a host-side guard refuses the queue line | GPU work on the host goes through the host's own runner | hand the line to the operator; do not route around the guard |

Not in the table: follow [docs/STUCK.md](../STUCK.md).

## 7. Record

```
kiln journal note "90-adopt-existing: goal <version> from <easel-run-id>; stage <stage>; handed over <host run id>"
```

A new pitfall: add a row to the table in section 6, dated from `date`, and a journal line, in the same session.
Stuck: `kiln journal stuck "<one line>"`, then [docs/STUCK.md](../STUCK.md).
