# LoRA type: style

A LoRA that teaches a base model one **look**: line, shading, palette and finish, carried over to any
subject. There is no identity checklist; the checklist judges the rendering only, and the trigger stands
for the whole look. Where training or example images show people, every one of them is an adult, and no
real person is depicted; kiln refuses anything else.

`kiln project new <slug> --type style` copies this folder into the new project. The project's copies are
then filled in and approved; the files here stay as they are. `kiln types check style` checks that every
file below is present and valid.

## The files, in the order they are filled

| file | what it holds | who fills it |
|---|---|---|
| `intake.yaml` | the 14 questions; the character-only ones (outfit, mouth, pronoun) say `applies: false`; examples for the synthetic `demo_style`, trigger `dmstl` | the person, about ten minutes |
| `checklist.yaml` | 3-8 style rules: line, shading, palette, finish; review-only checks for adults only and variety | an assistant drafts from intake question 3, the person approves |
| `spec.template.json` | the spec skeleton: summary of the look, trigger, no outfit, caption block without class word or style tag | `kiln spec draft` from the two files above, the person approves |
| `caption_rules.yaml` | captions describe content only; style words are forbidden in captions | shipped; a project adds its vocabulary |
| `eval_suite.yaml` | 12 prompts over varied subjects x 2 seeds | shipped; the runner adds the trigger |
| `dataset.toml` | the sd-scripts dataset template with five variables | rendered by kiln at train-plan time |
| `thresholds.yaml` | dated starting numbers per stage; no identity bands | shipped; **validate on your subject first** |
| `card.md.tmpl` | the model card template, with the `{{repo_links}}` slot | rendered at release time; edited by hand before any upload |
| `README.md` | this page | |

## How it differs from the character types

Captions name what is shown and never how it is drawn, because whatever a caption names is what the trigger
does not have to learn. Variety matters more than count: no single subject should fill more than about a
quarter of the set. Families default to `sdxl` and `illustrious`; flips are on unless the images hold text
or a composition whose direction matters.

## dataset.toml variables

`${DATASET_ROOT}` (the materialised version, `datasets/<version>`), `${FLIP_AUG}` (intake question 10),
`${RANK}`, `${ALPHA}` and `${LR}` (the family's training recipe unless the project overrides them). The
`[kiln]` table is read by kiln and removed before sd-scripts sees the file.
