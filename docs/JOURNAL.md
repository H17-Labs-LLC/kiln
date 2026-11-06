# The journal

The journal is the day's record of what was done, what failed and what was learned. It is the one place an
agent writes prose, and it is written only through commands, so every line carries a time from the clock.

## Where it lives

`<home>/journal/<YYYY-MM-DD>.md` in your instance, one file per day; the date is the clock's, never typed.
`kiln config show` names `<home>`. The journal never goes into this repository (`.gitignore` excludes
`journal/`).

## The three commands

| command | writes |
|---|---|
| `kiln journal note "<line>"` | one bullet, `- HH:MM <line>`, in today's file: a step done, a decision quoted |
| `kiln journal note "<line>" --pitfall "<symptom and cause>" --rule "<the rule>"` | the bullet plus an indented `Pitfall: ... Rule: ...` line (a pitfall always comes with its rule) |
| `kiln journal stuck "<line>" --stage <stage> --command "<exact>" --exit-code <n>` | a STUCK block in the shape of [STUCK.md](STUCK.md), printed for the hand-off; a field left out keeps its placeholder |
| `kiln journal report --out journal/trial-<date>.md` | one file collecting the day's STUCK blocks and bullets (without `--out` it prints them) |

`kiln journal <action> --help` lists every option (`journal stuck` also takes `--expected`, `--tried`,
`--hypothesis`, `--need`, and the output through `--output-file <file>` or `--output -`). Never open a
journal file to edit it, and never delete one.

## What to write

- **One line per step**, after the step: the command that changed something, and its last line or exit
  code. Read-only commands need no line unless their answer decided something.
- **The operator's words, quoted**, whenever they decide (a verdict, a go, a choice between options):
  `kiln journal note "operator: \"freeze 0.3.0 from 0.2.0, keep the old members\""`.
- **A pitfall** with `--pitfall` and `--rule` on the note, and in the same session a row in the failure
  table of the runbook you were following (section 6), dated from `date`.
- **A STUCK note** when a step fails twice or a gate refuses: `kiln journal stuck "<one line>"`, then the
  steps of [STUCK.md](STUCK.md).

## What never goes in

- A description of an image. Image facts come from the operator's verdicts and words only.
- A secret, a token, or anything copied from a non-sfw resource's text under `policy.prompt_authoring =
  "sfw-only"` (name such a resource by its id).
- A guessed time or date. If the clock looks wrong, say so in the line instead of correcting it by hand.

## The frozen clock

Tests and the doc generator set `KILN_NOW` (and `EASEL_NOW`) to `2030-01-01T00:00:00+00:00` so their output
is the same every time; while one is set, every command warns on stderr that the clock is frozen. A journal
line dated 2030 means that variable leaked into a real session: stop, unset it, and write a line saying
which entries carry the wrong date.

## Reading it

At a cold start, read the tail of today's file (and yesterday's, if today's is short) after `kiln next`:
it says what the last session did and where it stopped. For a hand-off, run `kiln journal report` for the
day's STUCK blocks and bullets and `kiln status --handoff --into <handoff file>` for the Now block, then
fill [HANDOFF-TEMPLATE.md](HANDOFF-TEMPLATE.md) from them.
