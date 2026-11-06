# Runbooks: the stage table (kiln)

One runbook per stage of a LoRA project. `kiln next` names the stage you are in and the runbook to read;
read only that runbook. Rules for agents: [AGENTS.md](../../AGENTS.md). When a step fails:
[STUCK.md](../STUCK.md).

## How to use the table

1. Run `kiln next --stage`. It prints one line, the stage id, for example `05-captions`.
2. Find the row whose literal line is exactly what it printed, and read only that runbook.
3. Check that runbook's section 2 (entry conditions) before its first command. Every entry condition there,
   as here, is a command plus the literal line it must print.
4. **A step marked planned is not built: report it once and stop at that stage.** A step is planned when the
   CLI prints `planned: <task> (<help>)` for it (`kiln --help` marks the verb `[planned: <task>]`), when a
   command line in a runbook ends with `# planned: <task>`, or when this table marks the runbook "planned"
   (the last column names the task). Report it in one journal note (`kiln journal note "<stage>: <command>
   is planned (<task>)"`) and in your hand-off; do not work around it.

The order is fixed: captions, then split, then freeze. Without a goal, `kiln next` derives the stage from the
project's files (fourth column). With a goal set (`kiln project goal set ...`, adopted projects), it routes
03 -> 05 -> 06 -> 07 -> 08 for that goal and ignores legacy images nobody has triaged. `kiln next` also prints
a `read:` line; when it names a runbook that this table marks planned, the file does not exist yet, and rule
4 applies.

## Stage table

| stage | runbook | entry condition: command | literal line | `next` routes here when | first command | lands with |
|---|---|---|---|---|---|---|
| 00 | [00-new-project](00-new-project.md) | `kiln next --stage` | `00-new-project` | there is no instance or no project yet, or a required intake answer is missing | `kiln project new <slug> --type character-anime` | T7 |
| 01 | 01-probe-iterations (planned) | `kiln next --stage` | `01-probe-iterations` | the intake is answered and the project has no image yet | `kiln source run <jobs.json> --dry-run` | T8 |
| 02 | 02-anchors (planned) | `kiln next --stage` | `02-anchors` | not routed by `kiln next` yet | `kiln images list` | session-2 |
| 03 | [03-dataset-images](03-dataset-images.md) | `kiln next --stage` | `03-dataset-images` | images without a disposition exist (with a goal: the goal's images are not imported yet) | `kiln images list` | T7 |
| 04 | 04-vetting (planned) | `kiln next --stage` | `04-vetting` | not routed by `kiln next` yet; the checklist verdicts go on the triage sheet of runbook 03 | `kiln review sheet` | session-2 |
| 05 | [05-captions](05-captions.md) | `kiln next --stage` | `05-captions` | kept images have no approved caption | `kiln captions status` | T7 |
| 06 | [06-split](06-split.md) | `kiln next --stage` | `06-split` | kept, captioned images have no split (`split: unassigned`) | `kiln dataset census` | T7 |
| 07 | [07-freeze-and-gate](07-freeze-and-gate.md) | `kiln next --stage` | `07-freeze-and-gate` | a kept image is in no frozen version, or the latest version is not materialised with matching counts | `kiln dataset check --leaks` | T7 |
| 08 | [08-train](08-train.md) | `kiln next --stage` | `08-train` | a version is frozen and materialised and no run on it has finished | `kiln runs list --family <family> --status ok` | T9 |
| 09 | 09-evaluate (planned) | `kiln next --stage` | `09-evaluate` | a run on the latest version finished and no checkpoint is picked | `kiln eval --help` | session-2 |
| 10 | 10-showcase (planned) | `kiln next --stage` | `10-showcase` | a run on the latest version has a picked checkpoint | `kiln showcase --help` | session-2 |
| 11 | 11-publish-by-hand (planned) | `kiln next --stage` | `11-publish-by-hand` | not routed by `kiln next` yet | `kiln release --help` | session-2 |
| 12 | 12-retrain-from-feedback (planned) | `kiln next --stage` | `12-retrain-from-feedback` | not routed by `kiln next` yet | `kiln project goal set --new-version <version> --from-easel-run <easel-run-id>` | session-2 |
| 90 | [90-adopt-existing](90-adopt-existing.md) | `kiln next --stage` | `90-adopt-existing` | the project was adopted from an existing workspace and no goal is set | `kiln project show` | T7 |

Every stage also needs a working install: `kiln --version` prints `kiln <version>` (for example
`kiln 0.1.0.dev0`; any version string matches), and `kiln doctor` prints no line starting with `FAIL`. A
`FAIL` line comes first; fix it before the stage.

## The shape of a runbook

Each runbook has the same seven sections, in this order (`kiln qa docs` checks them):

1. **Purpose and who decides.** What the stage produces; what the operator decides and what you do.
2. **Entry conditions.** Each one a command plus the literal line it prints (no `jq`, no parsing).
3. **Commands.** In order, `--dry-run` or read-only checks first. A line whose verb is not built yet ends
   with `# planned: <task>`.
4. **Expected output.** What the commands print on the shipped demo project (`examples/demo/`, the subject
   `demo_robot`) under the frozen clock (2030 dates) with `--redact-paths`. Variable parts are written `<n>`,
   `<id>`, `<version>`. A block not filled yet carries an HTML comment that reads `pending: fixture output`.
5. **Gate and operator question.** When the stage is done, and the one question to ask, as a template.
6. **Symptom, cause, action.** A table of known failures; anything else goes to [STUCK.md](../STUCK.md).
   The action column says what you do; an action that belongs to someone else starts with `operator:` or
   `maintainer:`, and your part of it is to report the line and name that person in `need:`.
7. **Record.** The journal line to write, and where a new pitfall goes.

**Match the shape, not the numbers.** The expected output comes from the demo, so your counts, ids, versions
and dates differ. Compare the lines that appear, their order and their words. A line missing, a different
line in its place, or an exit code other than the one the runbook names is a failure; another count is not.

## Where else to look

- What a record holds, field by field: [reference/formats.md](../reference/formats.md).
- Which question is answered by which command: [REGISTRY.md](../REGISTRY.md).
- How the journal works and how to hand off: [JOURNAL.md](../JOURNAL.md),
  [HANDOFF-TEMPLATE.md](../HANDOFF-TEMPLATE.md).
- Images generated with Codex or ChatGPT as training data: [sources/codex.md](../sources/codex.md).
- Known failures by topic: `docs/troubleshooting/`.
