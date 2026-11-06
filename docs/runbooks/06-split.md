# 06-split: a lineage-safe train/holdout split

After captions (05), before the freeze (07). Stage map: [README.md](README.md).

## 1. Purpose and who decides

Assign every `ACCEPT_TRAIN` image to `train` or `holdout` so that no lineage (an image, its crops, flips and
upscales) sits on both sides. The holdout measures the LoRA on images it never saw; a leak makes that
measurement worthless.

- **The operator decides** the holdout size and may move single images by name.
- **You:** preview the split, apply it after the operator's yes, and run the leak check.

## 2. Entry conditions

| command | literal line |
|---|---|
| `kiln next --stage` | `06-split` |

## 3. Commands

The split without `--apply` is the dry run: it prints the proposal and writes nothing.

```
kiln dataset census
kiln dataset split --holdout <n>
kiln dataset split --holdout <n> --apply
kiln dataset census --split
kiln dataset check --leaks
kiln next
```

- `<n>` is the number of holdout images the operator chose. The type's `thresholds.yaml` carries a dated
  default; it is a starting point, not a measurement for your subject.
- The split moves whole lineages, so the holdout can come out a little above or below `<n>`; the proposal
  says by how much.
- Only images with `split: unassigned` are placed. Images added later stay unassigned until the split runs
  again.

## 4. Expected output

On the shipped demo (three kept images from an easel run have no split yet; the operator chose 2):

```
$ kiln dataset census
tier           n  under_768  unknown_dims
CORE           2          2             0
SECONDARY     11         11             0
CANDIDATE      5          5             0
REJECTED       1          1             0
total         19         19             0

$ kiln dataset split --holdout 2
split proposal for demo_robot: 13 train, 2 holdout (as asked; 14 lineages kept whole)
  places 3 unassigned image(s); new holdout: none
dry run: nothing written; add --apply after the operator's yes

$ kiln dataset split --holdout 2 --apply
split proposal for demo_robot: 13 train, 2 holdout (as asked; 14 lineages kept whole)
  places 3 unassigned image(s); new holdout: none
applied: 3 image(s) assigned
next: kiln dataset check --leaks

$ kiln dataset census --split
snapshot          split       n
current           train       13
current           holdout     2
demo_robot/0.1.0  holdout     1
demo_robot/0.1.0  train       6
demo_robot/0.2.0  holdout     2
demo_robot/0.2.0  train       10

$ kiln dataset check --leaks
leaks: 0

$ kiln next --stage
07-freeze-and-gate
```

Match the shape, not the numbers (see [README.md](README.md)).

## 5. Gate and operator question

1. `kiln dataset census --split` shows no `ACCEPT_TRAIN` image left unassigned.
2. `kiln dataset check --leaks` exits 0.
3. `kiln next --stage` prints `07-freeze-and-gate`.

Ask before `--apply`, once, then wait:

> Proposed split for `<slug>`: `<t>` train, `<h>` holdout (asked `<n>`; lineages kept whole). Holdout:
> `<img ids or "see the proposal">`. Reply "apply" or tell me the size or the images to change.

## 6. Symptom, cause, action

| symptom | cause | action |
|---|---|---|
| the holdout differs from `<n>` | whole lineages were moved together | expected; report the numbers; the operator may change `<n>` |
| `check --leaks` lists a pair | a derived image was registered as a separate image, not a child | register it as a child (`kiln images child`, runbook 03), split again, check again |
| unassigned images after `--apply` | images arrived after the split | run the split again; it places only unassigned images |
| the holdout is empty or tiny | few lineages, or one lineage holds most images | tell the operator; a holdout of very few images gives noisy validation |
| `kiln next --stage` prints `05-captions` | some kept images have no approved caption | back to [05-captions.md](05-captions.md); the order is captions, then split |
| a holdout image later has no caption file in the dataset | an older materialiser wrote captions for the train side only | the freeze/materialise check in runbook 07 counts one caption per holdout image |
| `dry run: nothing written; add --apply after the operator's yes` | the split without `--apply` is the proposal only | expected; ask the question in section 5, then `--apply` |
| the freeze later prints `note: <n> new image(s) had no split and joined train` | the split was skipped or images arrived after it | expected when nothing should be held out; otherwise run the split before the freeze (runbook 07) |

Not in the table: follow [docs/STUCK.md](../STUCK.md).

## 7. Record

```
kiln journal note "06-split: <t> train, <h> holdout, leaks 0"
```

A new pitfall: add a row to the table in section 6, dated from `date`, and a journal line, in the same session.
Stuck: `kiln journal stuck "<one line>"`, then [docs/STUCK.md](../STUCK.md).
