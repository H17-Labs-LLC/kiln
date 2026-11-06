# LoRA type: character-anime

A LoRA that teaches a base model one **fictional adult character drawn in an anime style**: the same face,
head, colours and special features in any pose, outfit and setting. The character must be fictional, must
read as an adult in every image, and must not be a real person; kiln refuses anything else.

`kiln project new <slug> --type character-anime` copies this folder into the new project. The project's
copies are then filled in and approved; the files here stay as they are. `kiln types check character-anime`
checks that every file below is present and valid.

## The files, in the order they are filled

| file | what it holds | who fills it |
|---|---|---|
| `intake.yaml` | 14 questions with a default and an example answer each (synthetic demo subject `demo_robot`, trigger `dmrbt`) | the person, about ten minutes |
| `checklist.yaml` | 6-10 hard rules every training image is judged by; `body` is required and adult | an assistant drafts from intake question 3, the person approves |
| `spec.template.json` | the subject spec skeleton: summary, trigger, outfit block, caption block, scene hints | `kiln spec draft` from the two files above, the person approves |
| `caption_rules.yaml` | caption levels and length, order, view tags, forbidden words, the dataset settings captions assume | shipped; a project adds its outfit tag, `not_in_captions` and vocabulary |
| `eval_suite.yaml` | 12 scene-only prompts x 2 seeds for checkpoint triage | shipped; the runner adds trigger, class word and outfit |
| `dataset.toml` | the sd-scripts dataset template with five variables | rendered by kiln at train-plan time |
| `thresholds.yaml` | dated starting numbers per stage | shipped; **validate on your subject first** |
| `card.md.tmpl` | the model card template, with the `{{repo_links}}` slot | rendered at release time; edited by hand before any upload |
| `README.md` | this page | |

## Defaults that differ between types

| | character-anime | character-human | style |
|---|---|---|---|
| checklist kind | identity, with `body` | identity, with `body` | style: line, shading, palette, finish |
| families (intake 12) | illustrious, pony | sdxl | sdxl, illustrious |
| style tag (intake 13) | anime illustration | realistic digital painting | none: the trigger is the style |
| mouth rule (intake 9) | allowed (never_teeth on request) | allowed (never_teeth on request) | does not apply |
| identity thresholds | none shipped: measure your references first | none: the person's eye | none |

## dataset.toml variables

`${DATASET_ROOT}` (the materialised version, `datasets/<version>`), `${FLIP_AUG}` (intake question 10),
`${RANK}`, `${ALPHA}` and `${LR}` (the family's training recipe unless the project overrides them). The
`[kiln]` table is read by kiln and removed before sd-scripts sees the file.

## Rules that never change

- Fictional adults only: the age is asked in words and the `body` item states adult geometry; words that
  read young never describe the subject.
- Only a person judges images; an assistant drafts text for approval and labels any read of its own as
  "assistant's read, not the verdict".
- GPU work is submitted through the scheduler, never started directly.
