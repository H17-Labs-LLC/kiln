# 00-new-project: start a LoRA project

One project is one subject or style, one LoRA type and one folder in your instance (`<home>/projects/<slug>/`).
Stage map: [README.md](README.md). Rules for agents: [AGENTS.md](../../AGENTS.md).

## 1. Purpose and who decides

Create the project from a shipped LoRA type (`types/<type>/`) and fill its intake, so every later stage reads
the same spec, caption rules, checklist, eval suite and dataset template.

- **The operator decides:** the subject (a fictional adult, never a real person), the LoRA type
  (`character-anime`, `character-human` or `style`), the slug, the trigger word and every intake answer.
- **You:** run the commands below, record the operator's answers word for word with
  `kiln project intake answer`, and report what `kiln project lint` says. You never invent an answer: an
  unanswered question stays empty and is asked.

## 2. Entry conditions

| command | literal line |
|---|---|
| `kiln --version` | `kiln <version>` |
| `kiln next --stage` | `00-new-project` |

`kiln <version>` means any version string, for example `kiln 0.1.0.dev0`. Another stage printed: read that
runbook instead. A step marked planned is not built: report it once and stop at that stage.

## 3. Commands

Read-only checks first, then the writes.

```
kiln init ~/kiln-home
kiln doctor
kiln config set paths.models_root <models_root>
kiln config set privacy.wordlist <the operator's private word list file>
kiln doctor
kiln types check
kiln project new <slug> --type character-anime
kiln project show
kiln project lint
kiln project intake show --open
kiln project intake answer <question-id> "<the operator's words>"
kiln project lint
kiln next
```

- `kiln init` once per machine; it refuses a target inside a repository checkout. Skip it when
  `kiln doctor` already finds an instance. Run the commands from inside `<home>` (or set `KILN_HOME`):
  the sheet paths later stages print are relative to it.
- A new instance's `kiln.toml` names `${MODELS_ROOT}` and `${PRIVATE_WORDS_FILE}`, so the first
  `kiln doctor` prints two `FAIL` lines and every project command exits 2 until both are set. Set them with
  `kiln config set` and the values the operator gives: the folder that holds the models, and the private word
  list file (`""` for none, on the operator's word). An environment variable set in one shell call is gone in
  the next, so prefer `kiln config set`. The second `kiln doctor` ends with `next: kiln next`.
- `kiln project new` copies `types/<type>/` into the project: `intake.yaml` (14 questions with example
  answers), `caption_rules.yaml`, `checklist.yaml`, `eval_suite.yaml`, `dataset.toml`, `thresholds.yaml`
  and the card template. The example answers are examples, not the operator's answers.
- The intake is the one human-owned file of this stage. The operator dictates each answer; you record it with
  `kiln project intake answer <question-id> "<words>"`, one call per question, in the operator's exact words.
  `kiln project intake show --open` lists the unanswered questions with their ids (`name`, `trigger`,
  `description`, `references`, `adult`, ...; the number works too). A question with `choices` takes one of
  them; the list question `families` takes one family id per argument. The operator may also edit
  `intake.yaml` themselves; you never edit it by hand. After the answers, run `kiln project lint` again.
- The operator says "default" for a question: record nothing for it; the question's `default` applies while
  its answer stays empty (a `required` question has no default and must be answered).
- The subject spec verbs (`kiln spec draft`, `validate`, `approve`) are not part of this stage's gate; they
  arrive in session 2.

## 4. Expected output

On a machine with no instance, then a new one (`kiln init <home>`), the first `kiln doctor`, the two
`kiln config set` lines, and the new project. Machine values are written `<x>`.

```
$ kiln next
stage: 00-new-project
read: docs/runbooks/00-new-project.md
next: kiln init ~/kiln-home

$ kiln init <home>
kiln home: <home>
created: .gitignore, README.md, projects/, journal/, runtime/, kiln.toml
kiln finds it when you work inside it, when KILN_HOME names it, or with --workspace <home>
next: kiln doctor --workspace <home>

$ kiln doctor
kiln: <version> (python <python version>) ... ok
families: 15 family file(s) checked, 3 warning(s) (kiln families check lists them) ... ok
types: 3 type(s) checked: character-anime, character-human, style ... ok
config: kiln.toml (found by KILN_HOME) ... ok
FAIL: ${MODELS_ROOT} unset (paths.models_root)
  fix: set the variable, or kiln config set paths.models_root <value>
FAIL: ${PRIVATE_WORDS_FILE} unset (privacy.wordlist)
  fix: set the variable, or kiln config set privacy.wordlist <value>
instance: native ... ok
folders: projects/, journal/, runtime/ exist ... ok
train runtime: no python in runtime/venv-train (train.venv_dir); training runtime installer: session 2 ... warn
  fix: report it to the operator; they may point train.python and train.sd_scripts at an existing trainer (kiln config set)
sd-scripts: runtime/sd-scripts does not exist (train.sd_scripts); training runtime installer: session 2 ... warn
scheduler: none (this machine alone); free RAM <x> GB ... ok
disk: free <x> GB >= 20 GB floor ... ok
policy: subjects fictional-adults-only, prompt authoring sfw-only, reviewer human-only, render native ... ok
projects: none yet ... ok
next: set the variable, or kiln config set paths.models_root <value>

$ kiln config set paths.models_root <models_root>
set paths.models_root = '<models_root>' in kiln.toml (found by KILN_HOME)

$ kiln config set privacy.wordlist ""
set privacy.wordlist = "" in kiln.toml (found by KILN_HOME)

$ kiln project new demo_robot --type character-anime
created project demo_robot (character-anime) in projects/demo_robot
  copied: intake.yaml caption_rules.yaml checklist.yaml eval_suite.yaml dataset.toml thresholds.yaml card.md.tmpl spec.json
  families: illustrious, pony
  roots: images none, imports projects/demo_robot/images
next: ask the operator each question of `kiln project intake show`; record each reply with kiln project intake answer <question-id> "<words>"; then kiln project lint

$ kiln project lint
projects/demo_robot/intake.yaml: intake question 1 name has no answer (required): ask the operator
projects/demo_robot/intake.yaml: intake question 3 description has no answer (required): ask the operator
projects/demo_robot/intake.yaml: intake question 4 references has no answer (required): ask the operator
projects/demo_robot/intake.yaml: intake question 5 adult has no answer (required): ask the operator
projects/demo_robot/intake.yaml: intake question 7 default_outfit has no answer (required): ask the operator
warn: intake question 2 trigger has no answer; the default applies: None
warn: intake question 6 content has no answer; the default applies: 'sfw'
warn: intake question 8 other_outfits has no answer; the default applies: 'the shared casual, travel, formal and sport outfits; none school-coded'
warn: intake question 9 mouth has no answer; the default applies: 'allowed'
warn: intake question 10 symmetric has no answer; the default applies: 'yes'
warn: intake question 11 pronoun has no answer; the default applies: 'they'
warn: intake question 12 families has no answer; the default applies: ['illustrious', 'pony']
warn: intake question 13 style has no answer; the default applies: 'anime illustration'
warn: intake question 14 public_texts has no answer; the default applies: None
lint demo_robot: 5 problems, 9 warnings

$ kiln project intake show --open
1  name  MISSING (required)
    What is the subject's display name?
2  trigger  open (the default applies)
    Do you want a particular trigger word?
3  description  MISSING (required)
    Describe the subject in 3-6 lines. What is fixed (face, hair or head, colours, special features), and what may vary?
4  references  MISSING (required)
    Which 3-6 images show the subject? Mark the cleanest one.
5  adult  MISSING (required)
    How old is the subject, in words?
6  content  open (the default applies)
    Which content rating does this LoRA have?
    choices: sfw
7  default_outfit  MISSING (required)
    What does the subject wear by default? Name the torso, arms, legs and feet, and say how much of the back the outfit covers.
8  other_outfits  open (the default applies)
    Are there outfits or scenes the subject should never be shown in?
9  mouth  open (the default applies)
    May the mouth show teeth or fangs?
    choices: allowed, never_teeth
10  symmetric  open (the default applies)
    Is the design left-right symmetric, with no side ponytail, scar, one-sided marking or one-sided accessory?
    choices: yes, no
11  pronoun  open (the default applies)
    Which pronoun do captions, cards and review texts use for the subject?
    choices: she, he, they, it
12  families  open (the default applies)
    Which base model families should the LoRA be trained for?
    a list: one item per argument
13  style  open (the default applies)
    In which drawing style is the subject drawn?
14  public_texts  open (the default applies)
    Any wishes for the public texts of the model page?
intake demo_robot: 0 of 14 answered, 5 required still missing
next: ask the operator; then kiln project intake answer <question-id> "<words>"

$ kiln project intake answer name "Demo Robot"
intake 1 name: answered (Demo Robot), dictated by operator
next: kiln project intake show --open
```

Match the shape, not the numbers (see [README.md](README.md)).

## 5. Gate and operator question

The stage is done when all of these hold:

1. `kiln project lint` exits 0.
2. Every intake question has the operator's answer, or the operator said "default" for it.
3. The operator said yes to the slug, the type and the trigger word, in their own words.
4. `kiln next --stage` no longer prints `00-new-project`.

Ask once, then wait (fill every `<...>`):

> Project `<slug>` (`<type>`) is created. Intake: `<n>` of 14 questions answered; open: `<question ids>`.
> Proposed trigger: `<trigger>`. Please answer the open questions and reply "yes" to the slug, type and
> trigger, or tell me what to change.

## 6. Symptom, cause, action

| symptom | cause | action |
|---|---|---|
| `kiln next` prints `next: kiln init ~/kiln-home` | no instance was found (no `--workspace`, no `KILN_HOME`, no `kiln.toml` above the current folder) | `kiln init ~/kiln-home` once, then `kiln doctor` |
| `kiln next` exits 2 naming `--workspace` or `KILN_HOME` | the folder that option or variable names holds no `kiln.toml` | an explicit wrong workspace is an error, not a fresh start: report the line; the operator names the right folder |
| `kiln init` exits 2 and names a repository | the target folder is inside a repo checkout | pick a folder outside any checkout, for example `~/kiln-home` |
| `FAIL: ${MODELS_ROOT} unset (paths.models_root)` | the variable the config names is not set | `kiln config set paths.models_root <models_root>` with the folder the operator names; a variable set in one shell call is gone in the next |
| `FAIL: ${PRIVATE_WORDS_FILE} unset (privacy.wordlist)` | a new instance names the private word list through a variable | ask the operator for the list file (kept outside every repository), then `kiln config set privacy.wordlist <file>`; `""` only on their word |
| every `kiln project ...` command exits 2 and prints only `FAIL:` lines | an unset `${VAR}` in `kiln.toml` stops every command that loads the instance | fix the `FAIL` lines `kiln doctor` prints first |
| `kiln types check` exits 1 | a shipped type folder is incomplete or invalid | maintainer: fix the type folder; you report the line and never edit `types/` to make it pass |
| `kiln project new` exits 2: the slug exists | a project with that slug is already in the instance | use the existing project (`kiln project show`) or ask for another slug |
| `kiln project intake answer` exits 2 naming the question | the id is not one of the intake's ids, or the answer is not one of that question's `choices` | `kiln project intake show` lists the ids and the state of each question; ask the operator again when the answer is not one of the choices; never pick one for them |
| `kiln project lint` names a required question | a required question has no answer yet | ask the operator for it; a required question has no default |
| the trigger splits into many tokens | long words, underscores and version suffixes tokenize badly | propose a short invented word: on SDXL's CLIP-L tokenizer a name with underscores and a version suffix splits into many pieces, a short invented word into one or two. The operator picks |
| an answer would make the subject read as a minor, or names a real person | `policy.subjects = "fictional-adults-only"` | stop; tell the operator the project cannot go ahead as asked |
| `planned: <task> (<help>)` | the step is not built yet | report it once and stop at this stage |

Not in the table: follow [docs/STUCK.md](../STUCK.md).

## 7. Record

```
kiln journal note "00-new-project: created <slug> (<type>), intake <n>/14 answered"
```

A new pitfall: add a row to the table in section 6, dated from `date`, and a journal line, in the same session.
Stuck: `kiln journal stuck "<one line>"`, then [docs/STUCK.md](../STUCK.md).
