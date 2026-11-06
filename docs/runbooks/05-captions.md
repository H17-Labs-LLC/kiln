# 05-captions: one approved caption per kept image

Captions come before the split and the freeze (order fixed: 05 captions, 06 split, 07 freeze).
Stage map: [README.md](README.md).

## 1. Purpose and who decides

Give every `ACCEPT_TRAIN` image exactly one caption that follows the project's `caption_rules.yaml`
(trigger first, the class phrase, then what the image shows), approved by the operator.

- **The operator decides** and approves every caption. Approval goes through a sheet until the review app
  arrives (session 2).
- **You:** draft, write the sheet, apply what the operator approved, and lint. Drafts are built from each
  image's source prompt or the operator's note (`captions propose`); you never look at an image to write one.
- Under `policy.prompt_authoring = "sfw-only"` you may draft captions for an sfw project only. Never write a
  caption that makes the subject read as a minor or depicts a real person.

## 2. Entry conditions

| command | literal line |
|---|---|
| `kiln next --stage` | `05-captions` |

## 3. Commands

Status first, then a draft that writes nothing, then the writes.

```
kiln captions status
kiln captions propose
kiln captions propose --store
kiln captions sheet
kiln captions approve --sheet <sheet-file>
kiln captions apply
kiln captions lint
kiln captions status
kiln next
```

- `kiln captions propose` prints one draft per kept image and writes nothing. The basis, in order: the
  image's prompt (an easel import keeps the run's prompt, a jobs-file image its scene), else the operator's
  note from the import or `images label --note`, else nothing to go on (shown, never stored). Only the
  description in a prompt is kept: instructions to the generator, negations and anything about the fixed
  design are dropped, and the trigger, class word and style tag are put in their places.
- `kiln captions propose --store` keeps only the **clean** drafts as `draft` lines in `captions.jsonl`:
  clean means no lint problem except being under the word minimum (a true short caption beats a padded
  one), no derivation to check (a crop or flip that borrowed its parent's prompt) and no operator note to
  merge. An **approved caption is never redrafted**. An image that already has a draft is left alone unless
  you add `--all`, which redrafts drafts only. A rejected caption is drafted again. The summary line says
  how many were stored and what was left alone.
- `kiln captions sheet` writes the drafts into a sheet for the operator (columns `image`, `root`, `file`,
  `status`, `basis`, `draft`, `caption`, `decision`, `note`). The operator edits `caption` and writes
  `approve` or `reject` in `decision`; blank rows change nothing. You never approve a row for them.
- `kiln captions approve --sheet <sheet-file>` records the decided rows and prints
  `<n> rows approved, <r> rejected, <b> left blank, ...`; `kiln captions apply` writes the approved captions
  where the dataset will read them; `kiln captions lint` checks every caption against the project's rules.
- Count tokens before training: SDXL's text encoders cut a caption at 75 tokens **without a warning** unless
  the trainer is told otherwise (the family's flags carry `max_token_length`). `kiln captions lint` reports
  the longest captions.

## 4. Expected output

On the demo after the two images of runbook 03 were kept (run from `<home>`): the drafts come from the
operator's notes; the sheet is applied after the operator wrote `approve` in both rows.

```
$ kiln captions status
kept images: 17
approved: 15
approved in a sheet, not applied: 0
draft: 0
rejected: 0
none: 2
waiting: img-5b1e24a4054b img-fa943179ff05

$ kiln captions propose
img-5b1e24a4054b  note    16w  dmrbt, robot, sitting on a crate in a workshop, upper body, side view, clean anime illustration
img-fa943179ff05  note    16w  dmrbt, robot, standing by a workbench holding a wrench, full body, front view, clean anime illustration
2 image(s) proposed: from a prompt 0, from a note 2, nothing to go on 0; 2 clean
  left alone: 15 approved
dry run: nothing written; add --store to keep the clean ones as drafts

$ kiln captions propose --store
img-5b1e24a4054b  note    16w  dmrbt, robot, sitting on a crate in a workshop, upper body, side view, clean anime illustration  [stored]
img-fa943179ff05  note    16w  dmrbt, robot, standing by a workbench holding a wrench, full body, front view, clean anime illustration  [stored]
2 image(s) proposed: from a prompt 0, from a note 2, nothing to go on 0; 2 clean, 2 stored as drafts
  left alone: 15 approved
next: kiln captions sheet (the operator edits and approves every caption)

$ kiln captions sheet
caption sheet: projects/demo_robot/sheets/captions-20300101-001.csv (2 rows)
  the operator edits `caption` and writes approve or reject in `decision`; blank rows change nothing
  then: kiln captions approve --sheet projects/demo_robot/sheets/captions-20300101-001.csv

$ kiln captions approve --sheet projects/demo_robot/sheets/captions-20300101-001.csv
2 rows approved, 0 rejected, 0 left blank, 0 skipped (sheet captions-20300101-001.csv)
next: kiln captions apply

$ kiln captions apply
2 approved captions applied, 0 rejections recorded (0 sheet rows applied before)

$ kiln captions lint
0 image(s) with problems (rules: types/character-anime/caption_rules.yaml)

$ kiln captions status
kept images: 17
approved: 17
approved in a sheet, not applied: 0
draft: 0
rejected: 0
none: 0

$ kiln next --stage
06-split
```

Match the shape, not the numbers (see [README.md](README.md)).

## 5. Gate and operator question

1. `kiln captions status` shows every `ACCEPT_TRAIN` image with an approved caption and none waiting.
2. `kiln captions lint` exits 0.
3. `kiln next --stage` prints `06-split`.

Ask once, then wait:

> `<n>` caption drafts for `<slug>` are in `<sheet-file>` (trigger `<trigger>` first, per the caption rules).
> Please correct each caption, write approve or reject in `decision`, and tell me "done". Rows left blank stay
> out of training.

## 6. Symptom, cause, action

| symptom | cause | action |
|---|---|---|
| `propose` shows `(nothing to go on)` for an image | the image has no prompt and no operator note | ask the operator for a few words about it, record them with `kiln images label <img-id> --note "<words>"`, then propose again |
| `propose --store` stored fewer drafts than it proposed | only clean drafts are stored; the others list a `problem:` line | read the problem lines; a derived image's borrowed prompt or a note to merge is the operator's to word in the sheet |
| `left alone: <n> with a draft (--all redrafts them)` | those images already have a draft | expected; `--all` only when the operator wants the drafts redone |
| `lint` says the trigger is missing or not first | the draft came from a prompt without it | redraft (`kiln captions propose --store --all`), then a new sheet; a prompt meant to match training captions comes from the same rules as the captions |
| `lint` flags a caption above the token limit | sentence captions run long | ask the operator to shorten it, or keep it and make sure the family's `max_token_length` covers it |
| `lint` flags a non-sfw or age-down word | the source prompt carried it | never approve it; ask the operator for new wording |
| `skipped: <img-id>: decision ... is neither approve nor reject` | the `decision` cell holds another word | ask the operator which one they meant; never guess |
| `approve --sheet` applies fewer rows than the sheet has | rows left blank, or edited after the sheet was written | ask the operator to finish; write a fresh sheet if the drafts changed |
| `cannot read the sheet <file>: No such file or directory` | the sheet path `captions sheet` printed is relative to the instance folder, and the command ran from another folder | run it from `<home>` (or give the path under `<home>`) |
| `status` still shows images waiting after `approve` | `apply` was not run | `kiln captions apply`, then `status` |
| two captions for one image | a second draft was stored as a caption | one caption per image is the rule; `lint` names it; ask which one the operator keeps |
| `lint` flags many captions for their length | the word range comes from the project's spec (`caption.min_words`, `max_words`) when it sets one, else from the type's `caption_rules.yaml`; the last lint line names the source (`rules: ...`) | report which range applied; changing a range is the operator's call, recorded as a decision |
| comma-shuffled phrases in training later | caption shuffling is on for sentence captions | shuffling is for tag lists; leave it off for natural-language captions (see [docs/troubleshooting/training.md](../troubleshooting/training.md)) |

Not in the table: follow [docs/STUCK.md](../STUCK.md).

## 7. Record

```
kiln journal note "05-captions: <n> approved, <k> waiting, lint <exit code>"
```

A new pitfall: add a row to the table in section 6, dated from `date`, and a journal line, in the same session.
Stuck: `kiln journal stuck "<one line>"`, then [docs/STUCK.md](../STUCK.md).
