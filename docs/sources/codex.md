# Codex and ChatGPT images as training data

Recorded 2026-11-06. This page says what kiln's `codex` image source does, how to use it, and, plainly, what
nobody on this project has verified about using such images to train and share a LoRA.

## What it is

kiln reads candidate dataset images through one source interface with four kinds: `files` (a folder you
already have), `codex` (images generated through the Codex CLI's built-in image tool from a jobs file),
`comfy` (images rendered by your ComfyUI) and `easel-run` (the kept outputs of an easel run). The `codex`
source turns each job of a jobs file into one request to the CLI, collects the images from `<codex_home>`
(`[sources] codex_home` in your `kiln.toml`) and registers each image in the project as an untriaged
candidate with its prompt and job as lineage, plus a generation manifest. Nothing it produces counts until a
human triages it (`docs/runbooks/03-dataset-images.md`).

Status: the source interface (`src/kiln/sources/`) and `kiln source run <jobs.json>` are built. The `comfy`
source does a dry run only; its run prints `planned: session-2`.

## Settings

- The shipped settings are `sources/codex.yaml`: the CLI command and its fixed arguments, the most references
  one job may carry (a job with more is refused before anything is spent), where the CLI writes its images
  and session records under `<codex_home>`, the timeout, the environment variables never passed to the CLI
  (API keys: the source spends the account's plan allowance, never per-call billing), the exit code of each
  outcome, and the words that mark a moderation block, a spent quota and a refusal. The file says which CLI
  release its values were checked against.
- An instance overrides any key in `<home>/sources/codex.yaml` (for example `command` or `model`). A missing
  or mistyped key stops the run with exit 2 and names the key.
- `[sources] codex_home` in `kiln.toml` is required: it is the CLI's home folder, where the images arrive.

## How to use it (method)

1. The operator says this project may use generated images, and for which part of the dataset (for example
   poses or backgrounds the base model already handles well). Record their words with `kiln journal note`.
2. Write a jobs file: one job per request, with the prompt, the size and the count. Prompts follow the
   project's subject spec and caption rules, and `policy.subjects = "fictional-adults-only"`: never a
   prompt that makes the subject read as a minor, never a real person. A reference written `@<role>` means
   the images the operator labelled `anchor:<role>`.
3. `kiln source run <jobs.json> --dry-run` and read what it would send and how many images it would ask for.
4. Only after a clean dry run and the operator's go, the same command without `--dry-run`. This spends the
   account's generation quota, not your GPU.
5. The images arrive as untriaged candidates with `source.kind: codex`. The operator triages them like any
   other candidate; you never look at them.

## Outcomes of a job

A job that produced no image is classified and recorded, never retried silently:

| outcome | what happened | the batch | what you do |
|---|---|---|---|
| `blocked` | output moderation refused the generation (its marker word was in the CLI's event stream) | goes on | report it; one rewrite of the scene in more modest words, if the operator allows it |
| `refused` | the service's English refusal wording came back instead of an image | goes on | as for `blocked` |
| `quota` | the account's usage window is spent | stops, exit 3 | stop; tell the operator; never switch to an API key |
| `failed` | the CLI exited non-zero or timed out | goes on | report the job id and the line |
| `empty` | no image and no known reason | goes on | report it; it may be a refusal in new wording |

**Refusals.** One rewrite of the scene in more modest words is allowed when the operator allows it, recorded
in the journal; never a third attempt of the same job. In v0.1 the source detects a refusal by matching the
service's English refusal text, so a change in that wording can make a refusal look like an empty result: an
empty result is reported, never retried blindly.

## What is not verified

None of the following was checked by this project. Read the current terms yourself before you train on or
share anything made this way, and record what you read, with its date, in your journal.

- **The generator's terms.** Whether OpenAI's terms for ChatGPT and Codex allow using their image outputs as
  training data for a model, and whether they allow sharing that model, commercially or not.
- **The publishing site's rules.** Whether a model-sharing site accepts LoRAs trained on AI-generated images,
  and whether it requires a disclosure that the training data was generated.
- **Embedded provenance.** Whether the generated files carry provenance metadata (for example C2PA content
  credentials), what it says, and whether a dataset or a model card must keep or mention it. kiln v0.1 does
  not read it or rely on it.
- **Refusal detection.** That the English-text matcher catches every refusal. It was written against the
  wording seen when it was built and may miss a new one.
- **Repeatability.** That the same jobs file gives the same images twice. Assume it does not: the image is
  the record, the job is its lineage, not a recipe for an exact rerun.
- **Fitness for training.** That generated images teach a LoRA what the operator wants. Generated images can
  share one look (lighting, skin, line weight) that the LoRA then learns as part of the subject. Keep them a
  minority unless the operator decides otherwise (a prior, not a measurement), and let the evaluation show
  the effect.

## What kiln does regardless

- Every generated image keeps its lineage in `images.jsonl`: the source kind, the job and the prompt.
- Only the operator's verdict moves a generated image into the dataset.
- The model card should say where the training images came from; saying that some were generated is
  recommended even where no rule asks for it (the card template is filled by the operator).
- No tool uploads or publishes anything; the operator does that by hand.
