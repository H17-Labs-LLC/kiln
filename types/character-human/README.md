# LoRA type: character-human

A LoRA that teaches a base model one **invented adult human**, drawn or painted realistically: the same face,
hair, eyes, skin and special features in any pose, outfit and setting.

**Refusal, before anything else:** the subject must be fictional and adult. kiln refuses a subject that is a
real person, is named after one or is made to resemble one (yourself, someone you know, a public figure), and
a subject that is or reads as a minor. A photo of a real person is never a reference or a training image;
references are drawings, paintings or generated images of the invented person. A reviewer rejects any image
that looks like a specific real person (the `no_real_likeness` check).

`kiln project new <slug> --type character-human` copies this folder into the new project. The project's
copies are then filled in and approved; the files here stay as they are. `kiln types check character-human`
checks that every file below is present and valid.

## The files, in the order they are filled

| file | what it holds | who fills it |
|---|---|---|
| `intake.yaml` | 14 questions with a default and an example answer each (synthetic invented subject `demo_pilot`, trigger `dmplt`) | the person, about ten minutes |
| `checklist.yaml` | 6-10 hard rules every training image is judged by; `body` is required and adult | an assistant drafts from intake question 3, the person approves |
| `spec.template.json` | the subject spec skeleton: summary, trigger, outfit block, caption block, scene hints | `kiln spec draft` from the two files above, the person approves |
| `caption_rules.yaml` | caption levels and length, order, forbidden words incl. likeness words, the dataset settings captions assume | shipped; a project adds its outfit tag, `not_in_captions` and vocabulary |
| `eval_suite.yaml` | 12 scene-only prompts x 2 seeds for checkpoint triage | shipped; the runner adds trigger, class word and outfit |
| `dataset.toml` | the sd-scripts dataset template with five variables | rendered by kiln at train-plan time |
| `thresholds.yaml` | dated starting numbers per stage; no automatic identity score for this type | shipped; **validate on your subject first** |
| `card.md.tmpl` | the model card template, with the invented-person sentence and the `{{repo_links}}` slot | rendered at release time; edited by hand before any upload |
| `README.md` | this page | |

## How it differs from character-anime

Families default to `sdxl`; the style tag defaults to "realistic digital painting"; the mouth rule defaults
to `allowed` in both types; booru view tags are off (realistic families read plain view words); captions also
forbid likeness words; neither type ships identity bands, and anime identity models such as CCIP do not
apply to realistic people.

## dataset.toml variables

`${DATASET_ROOT}` (the materialised version, `datasets/<version>`), `${FLIP_AUG}` (intake question 10),
`${RANK}`, `${ALPHA}` and `${LR}` (the family's training recipe unless the project overrides them). The
`[kiln]` table is read by kiln and removed before sd-scripts sees the file.
