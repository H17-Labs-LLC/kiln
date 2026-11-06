# 03-dataset-images: bring images in and give each a disposition

Images come from a source (a folder, Codex/ChatGPT jobs, ComfyUI renders, or a reviewed easel run) and land
in the project's `images.jsonl` with a disposition. Stage map: [README.md](README.md).

## 1. Purpose and who decides

Register every candidate image once, with where it came from and its lineage (a crop or flip is a child of
its parent), and record the operator's verdict on each as a disposition (`ACCEPT_TRAIN`, `ACCEPT_CORE`,
`ACCEPT_REFERENCE_ONLY`, `REFINE`, `REJECT`, `HOLD`).

- **The operator decides** every verdict. Only a human judges images.
- **You:** run the imports, write the triage sheet, apply the sheet the operator filled (or record a verdict
  the operator dictates), and report counts. You never open, decode or describe an image, and you never fill
  a verdict yourself.
- Codex/ChatGPT images as training data: what is unverified is in [docs/sources/codex.md](../sources/codex.md).

## 2. Entry conditions

| command | literal line |
|---|---|
| `kiln next --stage` | `03-dataset-images` |

On an adopted workspace with a goal set, `next` ignores the legacy rows marked `legacy_untriaged` and still
prints `03-dataset-images` until the goal's new images are in ([90-adopt-existing.md](90-adopt-existing.md)).

## 3. Commands

Look first, then the dry run, then the writes.

```
kiln images list
kiln source run <jobs.json> --dry-run
kiln images import --from-folder <dir> --notes <notes.csv> --kind <kind>
kiln images import --from-easel-run <easel-run-id> --keep-only
kiln review sheet
kiln review apply --sheet <sheet-file>
kiln images label <img-id> --disposition <disposition> --note "<the operator's words>"
kiln images list
kiln next
```

- **A jobs file** (`kiln source run <jobs.json> --dry-run`): prints what the file would ask each source for,
  and how many images. The same command without `--dry-run` runs only on the operator's go, and only for the
  `codex` source (it spends the account's generation quota, see [docs/sources/codex.md](../sources/codex.md)).
  The `comfy` source renders on the GPU: kiln does a dry run only, and its run prints `planned: session-2`;
  the render itself is submitted through easel (`easel jobs submit` on a native instance) and its kept
  outputs come back through `--from-easel-run`. A reference written `@<role>` in a jobs file means the image
  labelled `anchor:<role>`.
- **A folder** (`--from-folder <dir>`): registers each image of the folder as an untriaged candidate (tier
  `CANDIDATE`, no disposition) and prints
  `<n> images registered from <folder> (untriaged); <k> already registered, <p> problem(s)`. A file outside
  the project's roots is copied, sha256 checked, into the project; nothing is moved or renamed.
  `--notes <notes.csv>` (optional) is the operator's words per file, with the columns `file` (the name
  relative to the folder, forward slashes) and `note`, and optionally `viewpoint`, `framing` and `labels`
  (space-separated `ns:value`). The note is stored verbatim; `kiln captions propose` drafts from it when the
  image has no prompt. `--kind` says where the files came from: `unknown` (the default), `upload`, `scan`,
  `local-gen`, `api`, `chatgpt`, `codex` or `generated`.
- **An easel run** (`--from-easel-run <easel-run-id> --keep-only`): copies the outputs the operator kept in
  that run (sha256 checked) into `projects/<slug>/images/<easel-run-id>/` with `disposition: ACCEPT_TRAIN`,
  `split: unassigned` and `verdict_source: easel-review:<easel-run-id>`, and prints
  `<n> images registered as ACCEPT_TRAIN (verdicts from easel run <easel-run-id>)`. The verdicts come from
  that run's `reviews.jsonl` (the operator's latest line per output). `--keep-only` is required: rejected
  and undecided outputs stay in easel. Add `--easel-home <dir>` when the easel instance is not found by
  itself.
- **The triage sheet** (`kiln review sheet`): writes `sheets/review-<date>-<nnn>.csv` with one row per image
  that waits for a verdict (`--all`: every image). The columns show what is known without looking (`image`,
  `root`, `file`, `source`, `tier`, `disposition`, `split`, `viewpoint`, `framing`, `labels`), then one
  `check:<key>` column per item of the project's checklist, then `verdict` and `note`. The operator fills
  it; you only run the two commands.
  - `verdict`: `ACCEPT_TRAIN` (also `train`, `keep`, `accept`), `ACCEPT_CORE` (`core`),
    `ACCEPT_REFERENCE_ONLY` (`reference`, `ref`), `REFINE`, `REJECT` (`no`), `HOLD`; blank means no verdict
    yet.
  - `check:<key>`: `pass`, `fail` or `na`. A pass records the label `check:<key>`, a fail
    `check-fail:<key>`.
  - `note`: the operator's words, stored verbatim.
  - `kiln review apply --sheet <sheet-file>` prints `<n> verdicts recorded`; it is safe to run twice.
- **A verdict the operator dictates** (no sheet): `kiln images label <img-id> --disposition <disposition>
  --note "<the operator's words>"`, one image per call, with the operator's words quoted. The disposition is
  one of the words above in capitals; never record one the operator did not say.
- A derived image (crop, flip, upscale) is registered as a child of its parent (`kiln images child`), so the
  split keeps the family on one side. `kiln images label`, `kiln images assign` and `kiln images promote`
  record the operator's labels, split and tier; `--help` gives their arguments.

## 4. Expected output

On the demo, run from `<home>`: two new files from a folder with a notes CSV (`file,note,viewpoint,framing`),
the triage sheet, the sheet applied after the operator marked both rows `keep` with every check `pass`, the
same sheet applied a second time, and one verdict the operator dictated.

```
$ kiln images import --from-folder <dir> --notes <notes.csv> --kind upload
img-fa943179ff05  8x8  imports:incoming/pose-a.png  (note)
img-5b1e24a4054b  8x8  imports:incoming/pose-b.png  (note)
2 images registered from incoming (untriaged); 0 already registered, 0 problem(s)
next: kiln review sheet (the operator gives each image a verdict)

$ kiln review sheet
review sheet: projects/demo_robot/sheets/review-20300101-001.csv (2 rows)
  the operator writes a verdict, the checks and a note per row; blank rows change nothing
  then: kiln review apply --sheet projects/demo_robot/sheets/review-20300101-001.csv

$ kiln review apply --sheet projects/demo_robot/sheets/review-20300101-001.csv
2 verdicts recorded (2 ACCEPT_TRAIN), 0 other changes, 0 unchanged, 0 left blank, 0 skipped (sheet review-20300101-001.csv)
next: kiln next

$ kiln review apply --sheet projects/demo_robot/sheets/review-20300101-001.csv
0 verdicts recorded, 0 other changes, 2 unchanged, 0 left blank, 0 skipped (sheet review-20300101-001.csv)
next: kiln next

$ kiln images label img-f4fae49d1cf6 --disposition REJECT --note "<the operator's words>"
img-f4fae49d1cf6: disposition REJECT, note

$ kiln next --stage
05-captions
```

The demo's sheet has the columns
`image,root,file,source,tier,disposition,split,viewpoint,framing,labels,check:face,check:head,check:colours,check:body,check:feature,check:hands,verdict,note`
(one `check:` column per item of the demo's checklist).

Match the shape, not the numbers (see [README.md](README.md)).

## 5. Gate and operator question

1. Every image the operator wants considered has a disposition from a human verdict (the sheet, a dictated
   `images label --disposition`, or an easel review). An easel import printed
   `<n> images registered as ACCEPT_TRAIN (verdicts from easel run <easel-run-id>)`.
2. `kiln images list` ends with `<n> images (<k> kept, 0 without a verdict)` for this round (legacy rows
   excepted while a goal is set).
3. `kiln next --stage` prints the next stage, `05-captions`. (`04-vetting` is a planned runbook that
   `kiln next` does not route to yet; the checklist verdicts go on the triage sheet above.)

Ask once, then wait:

> `<n>` new images are registered for `<slug>` (`<source>`). The triage sheet is `<sheet-file>`: please mark
> each row keep for training, core, reference only, refine, reject or hold, fill the check columns, and tell
> me "done" when it is filled.

## 6. Symptom, cause, action

| symptom | cause | action |
|---|---|---|
| `0 images registered` after `--from-easel-run` | the easel run has no kept outputs, or its reviews were never imported | check `easel runs reviews --current` in the easel instance; on an adopted host the reviews come from `easel runs import-legacy <legacy-run-id> --with-reviews` (easel runbook 90) |
| `--from-easel-run` exits 2 without `--keep-only` | only the kept outputs are imported | add `--keep-only` |
| the import names a sha256 mismatch | the output changed after the run was recorded | stop; report the file; never re-register it under a new name |
| `already registered: <file> is <img-id>; nothing written` | the same bytes have one id (`img-<sha256[:12]>`) | nothing to do; a second import is a no-op |
| `note unused: <name> is not an image of the folder` | a `file` cell of the notes CSV names no image in the folder | ask the operator which file was meant; the other rows were used |
| the import exits 2: the notes file needs a `file` column | the CSV has no header row, or another first column | ask the operator for a CSV whose header is `file,note` (plus the optional columns) |
| `problem: <name>: viewpoint ... is not one of ...` (or framing, or a label that is not `namespace:value`) | a notes cell holds a word kiln does not know | that image is not registered; ask the operator to correct the cell with one of the listed words, then import the folder again (the images already registered are skipped) |
| `source run --dry-run` reports a problem with `@<role>` | no image carries the label `anchor:<role>` | on the operator's word, `kiln images label <img-id> --label anchor:<role>`; then the dry run again |
| `skipped: <img-id>: its verdict changed since the sheet was written` | someone recorded a verdict after the sheet was made | `kiln review sheet` again for a fresh sheet; never edit the old one to match |
| `skipped: <img-id>: ...` naming a word | the cell holds a word `apply` does not know | ask the operator what they meant; `apply` never guesses |
| `kiln next --stage` prints `90-adopt-existing` | an adopted project with no goal set | read [90-adopt-existing.md](90-adopt-existing.md); the goal comes first |
| `next` stays on `03-dataset-images` although the round's images are in | images of this round have no disposition yet | finish the sheet (`kiln review apply --sheet`); legacy rows do not count while a goal is set |
| a Codex job ends `blocked` | output moderation refused the generation | recorded, never retried silently; one rewrite of the scene in more modest words is the operator's call, then one more dry run and run |
| a Codex job ends `refused` | the service refused the prompt (read from its English refusal text) | as for `blocked`; a refusal worded in a new way can show up as `empty` instead |
| a Codex job ends `quota` and the batch stops (exit 3) | the account's usage window is spent | stop; tell the operator when the window resets; never switch to a paid API key |
| a Codex job ends `failed` or `empty` | the CLI exited non-zero or timed out, or no image came back | report the job id and the line; never resubmit it blindly |
| `cannot read the sheet <file>: No such file or directory` | the sheet path a command printed is relative to the instance folder, and the command ran from another folder | run it from `<home>` (or give the path under `<home>`); never copy the sheet elsewhere |
| the sheet applies fewer rows than it has | rows were left blank or the file was renamed | ask the operator to finish the sheet; never fill a blank row |
| a crop and its parent land in different splits later | the crop was imported as a separate image, not a child | register it with `kiln images child` before the split (runbook 06) |

Not in the table: follow [docs/STUCK.md](../STUCK.md).

## 7. Record

```
kiln journal note "03-dataset-images: <n> registered from <source>, <k> ACCEPT_TRAIN, <r> REJECT"
```

A new pitfall: add a row to the table in section 6, dated from `date`, and a journal line, in the same session.
Stuck: `kiln journal stuck "<one line>"`, then [docs/STUCK.md](../STUCK.md).
