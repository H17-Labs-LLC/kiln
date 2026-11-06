# 07-freeze-and-gate: freeze a dataset version and materialise it

After captions (05) and the split (06). Training (08) only ever reads a frozen, materialised version.
Stage map: [README.md](README.md).

## 1. Purpose and who decides

Freeze the current train/holdout members into an immutable snapshot (`snapshots/<version>.json`), then write
the dataset folder the trainer reads (`datasets/<version>/{training,holdout,_meta}` inside the project).
A version is never changed after the freeze; a new round of images is a new version.

- **The operator decides** the version number and whether the new version keeps the previous version's
  members (`--from <old-version>`) or holds only the new images (`--only-new`).
- **You:** run the checks, freeze, materialise, and report the counts.

## 2. Entry conditions

| command | literal line |
|---|---|
| `kiln next --stage` | `07-freeze-and-gate` |

## 3. Commands

The checks first: `freeze` refuses on what they find, so run them before asking the operator. Then the one
freeze line that fits (below), then the materialise and its check.

```
kiln dataset check --leaks
kiln captions status
kiln dataset census --split
kiln project show
kiln dataset freeze <version>
kiln dataset freeze <version> --from <old-version>
kiln dataset materialize <version>
kiln dataset check <version>
kiln next
```

Which freeze line:

- **First version** (`kiln project show` lists no snapshot): `kiln dataset freeze <version>`. With no goal
  on a native project this means `--only-new`, which for a first version is every kept image.
- **A later version that keeps the old members** (the usual round): `kiln dataset freeze <version> --from
  <old-version>` builds the new version from the old version's members plus every new `ACCEPT_TRAIN` image.
  On an adopted project, or when a goal names `from_version` for this version, `freeze <version>` alone
  already means `--from` (the goal's `from_version`, else the latest snapshot).
- **Only the new images**: `kiln dataset freeze <version> --only-new` holds only the images not in any
  earlier snapshot. Use it only on the operator's word.

Then:

- A kept image still `unassigned` joins train at the freeze, and the freeze prints a `note:` line saying so;
  run the split (runbook 06) first to hold some out.
- `kiln dataset materialize <version>` writes only under the project's `datasets/<version>/`; it copies each
  member there (members that live in the adopted image folder are read from it, never written to it) and
  writes `_meta/materialized-<version>.json`. It refuses a target that already holds files: the folder is
  generated, so a second materialisation goes into a fresh folder with `--into <dir>` (inside the instance).
- `kiln dataset check <version>` compares the materialised folder with the snapshot: member counts, one
  caption file per image on both sides. A folder made with `--into <dir>` is checked with
  `kiln dataset check <version> --into <dir>`.

## 4. Expected output

On the demo after runbook 06 (its goal is version 0.3.0 from 0.2.0, so `freeze 0.3.0` alone means
`--from 0.2.0`):

```
$ kiln dataset check --leaks
leaks: 0

$ kiln dataset freeze 0.3.0
frozen demo_robot/0.3.0: 15 members (12 from 0.2.0 + 3 new)
  mode: the goal: new version 0.3.0 from 0.2.0
  train 13, holdout 2; members_sha256 f07790db556e
next: kiln dataset materialize 0.3.0

$ kiln dataset materialize 0.3.0
materialised demo_robot/0.3.0
  train    13 images + captions -> projects/demo_robot/datasets/0.3.0/training
  holdout  2 images + captions -> projects/demo_robot/datasets/0.3.0/holdout
  record   projects/demo_robot/datasets/0.3.0/_meta/materialized-0.3.0.json  sha256 e118661426c7
next: kiln dataset check 0.3.0

$ kiln dataset check 0.3.0
leaks: 0
demo_robot/0.3.0: 13 train, 2 holdout; captions 15/15; matches the snapshot

$ kiln next --stage
08-train
```

Match the shape, not the numbers (see [README.md](README.md)).

## 5. Gate and operator question

1. `freeze` printed `frozen <slug>/<version>: <m> members (...)` (old plus new with `--from`).
2. `materialize` wrote the folder, and `kiln dataset check <version>` exits 0: counts match the snapshot
   and the holdout has one caption per image.
3. `kiln next --stage` prints `08-train`.

Ask before `freeze`, once, then wait. A later version:

> Ready to freeze `<slug>` version `<version>`: `<old>` members from `<old-version>` plus `<new>` new
> (`<t>` train, `<h>` holdout), leaks 0, all captioned. Reply "freeze" to keep the old members, "only new"
> for the new images alone, or give another version number.

A first version:

> Ready to freeze the first version of `<slug>`, `<version>`: `<m>` images (`<t>` train, `<h>` holdout),
> leaks 0, all captioned. Reply "freeze", or give another version number.

## 6. Symptom, cause, action

| symptom | cause | action |
|---|---|---|
| `freeze` exits 2 and names uncaptioned train images | captions not approved or not applied | back to [05-captions.md](05-captions.md) |
| `freeze` exits 2 and names a leak | a lineage sits on both sides | back to [06-split.md](06-split.md) |
| `freeze` exits 2: the version exists | snapshots are immutable | ask the operator for a new version number |
| `--from` names a version that is not found | wrong number, or the old snapshot was never exported into this instance | `kiln project show` lists the snapshots; report and ask |
| the new version is much smaller than expected | `--only-new` was used, or `--from` was left out on a native project without a goal | freeze a new version with `--from <old-version>` after the operator's yes |
| `warn: <n> holdout image(s) without a caption` after the freeze | holdout members have no approved caption | validation would score them against empty prompts; back to 05 for those images before training |
| `REFUSED: ... is inside images_root, which is read-only until cutover` | the target is inside the adopted image folder | use the default target under the project's `datasets/` |
| `REFUSED: ... already holds <n> file(s)` | the folder was materialised before | materialise into a fresh folder: `kiln dataset materialize <version> --into <dir>`, then `kiln dataset check <version> --into <dir>`; never empty or delete the old folder |
| `REFUSED: --into must lie inside the instance folder` | the fresh folder is outside the instance | pick a folder inside `<home>`, for example under the project's `datasets/` |
| `dataset check`: holdout captions missing | the folder was written by an older tool or edited by hand | materialise the version again into a fresh folder with `--into <dir>`; never write captions by hand |
| `dataset check`: counts differ from the snapshot | files were added or removed in the folder after materialising | materialise again into a fresh folder with `--into <dir>`; the folder is generated, never edited |
| `kiln next` stays on `07-freeze-and-gate` after an `--into` materialisation | `next` and `train` read only `datasets/<version>/` | report it: operator: archive the old `datasets/<version>/` folder and say which folder training uses; you never move or delete it |

Not in the table: follow [docs/STUCK.md](../STUCK.md).

## 7. Record

```
kiln journal note "07-freeze-and-gate: froze <version> from <old-version>, <m> members, materialised, check 0"
```

A new pitfall: add a row to the table in section 6, dated from `date`, and a journal line, in the same session.
Stuck: `kiln journal stuck "<one line>"`, then [docs/STUCK.md](../STUCK.md).
