# LoRA types

A LoRA type is a folder of starting files that `kiln project new <slug> --type <id>` copies into a new
project: the intake questions, the checklist skeleton, the caption rules, a core eval suite, the dataset
template, dated thresholds, the spec skeleton and the model card template. Three types ship:

| id | teaches | checklist |
|---|---|---|
| `character-anime` | one fictional adult character drawn in an anime style | identity, with an adult `body` item |
| `character-human` | one invented adult human, drawn or painted realistically; never a real person | identity, with an adult `body` item |
| `style` | one look (line, shading, palette, finish) over any subject | style only |

Every type depicts fictional adults only. `kiln types check` validates every folder here; `kiln types check
<id>` validates one.

## Adding a type

1. Copy the closest folder to `types/<new-id>/` (lowercase letters, digits and hyphens).
2. Change the defaults and the `type:` line in each YAML file; keep the 14 intake questions, numbered 1-14
   (set `applies: false` on a question that does not fit, and say what is used instead).
3. Rewrite the README, the examples and the card for the new type; use only synthetic example subjects.
4. Run `kiln types check <new-id> --strict` until it prints `ok`, then open a pull request with the label
   `lora-type`.

Traps that `kiln types check` catches, and how to avoid them:

- A YAML flow mapping splits a plain value at a comma (`{label: X, hint: a, b}` gains a key `b`): quote such
  values or use block style.
- A `required: true` question has `default: null`; a rule such as "at most one of these" goes in its `note`.
- The card's `{{repo_links}}` slot must sit in the body: HTML comments are stripped before the check.
- In `spec.template.json` leave out a key that does not apply instead of setting it to `null`.
- Defaults are generic priors marked "validate on your subject first", never values measured on one subject.

The nine files a type folder must hold, and the schema each one is checked against:

| file | schema |
|---|---|
| `intake.yaml` | `schemas/type-intake.schema.json` |
| `checklist.yaml` | `schemas/type-checklist.schema.json` |
| `caption_rules.yaml` | `schemas/type-caption-rules.schema.json` |
| `thresholds.yaml` | `schemas/type-thresholds.schema.json` |
| `dataset.toml` | `schemas/type-dataset-toml.schema.json`, after its five variables are filled with sample values |
| `eval_suite.yaml` | `schemas/eval-suite.schema.json`, the record schema project eval suites use |
| `spec.template.json` | `schemas/subject-spec.schema.json`, the record schema project specs use |
| `card.md.tmpl` | must carry the `{{repo_links}}` slot; field names are lowercase with underscores |
| `README.md` | must start with a `# ` heading |
