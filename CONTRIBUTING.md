# Contributing to kiln

kiln is an MIT-licensed tool for building, training and evaluating LoRAs from plain-file project folders,
written so that a person or an AI agent can run it from the docs alone. Contributions are welcome from both. This guide says how
work is picked up, how a change is proposed and what it must pass before it is merged. Read it to the end once;
agents doing contributor work follow it exactly like people do.

## Start here

Do these five things, in this order, before you change a file:

1. Read [AGENTS.md](AGENTS.md), then [docs/runbooks/README.md](docs/runbooks/README.md), then this guide. The
   "Never" list in AGENTS.md binds every text an agent will read, so it binds what you write as well.
2. Install from a clone: `python -m venv .venv` and `.venv/Scripts/pip install -e ".[dev]"`
   (`.venv/bin/pip` on Linux and macOS). The first install needs network access; see the contributor notes.
3. Run the offline suite on the untouched checkout:
   `python -m pytest -q -m "not gpu and not machine and not comfy_live and not net"`. If it fails before you
   changed anything, open an issue with the output instead of working around it.
4. Install the pre-push gate (below, "Your private word list and the pre-push gate").
5. Find the issue your change belongs to, or open one with the template that fits.

## Contribution terms

- **Inbound = outbound.** Everything you contribute is licensed under this repository's MIT licence
  ([LICENSE](LICENSE)). There is no contributor licence agreement.
- **Only what you may give.** Contribute your own work, or third-party material whose licence allows it and is
  named in the pull request. Never paste code, prompts or text from a source whose licence you have not read.
- **Sign-off is optional.** `git commit -s` adds a `Signed-off-by:` line (the Developer Certificate of Origin);
  it is welcome and not required.
- **Nothing private, ever.** No personal names, account names, e-mail addresses, paths from your machine, host
  names or ports, model files, images or dataset files. The scanner and the pre-push gate look for these
  shapes, and a maintainer refuses a pull request that carries one, however small.
- **Who decides.** The maintainers (`.github/CODEOWNERS`) review and merge. In a running instance the operator,
  the person who owns it, decides every image verdict, every release and every licence question; no
  contribution may hand such a decision to a tool or an agent.
- **Conduct and security.** [CODE_OF_CONDUCT.md](CODE_OF_CONDUCT.md) (Contributor Covenant 2.1) applies
  everywhere the project lives. Report a vulnerability privately as [SECURITY.md](SECURITY.md) describes, never
  in a public issue. Neither has an e-mail contact: GitHub's private reporting and the issue tracker are the
  only channels.

## How work is tracked

The issue tracker is the agenda; there is no other list. Each issue states its acceptance criteria before the
work starts, written so that a command or a test can check them, and the pull request is judged against
exactly those criteria.

| label | used for |
|---|---|
| `bug` | a command that does something other than its help or runbook says |
| `pitfall` | a STUCK note from a session: a step failed or a runbook did not match (template generated from the one STUCK template) |
| `runbook-gap` | a runbook that is missing, unclear or wrong about a literal line |
| `lora-type` | a new or changed LoRA type under `types/<id>/` |
| `model-family` | a new or corrected `families/<id>.yaml` |
| `good-first-issue` | small, well-specified, with the acceptance criteria already written |

- **Taking an issue.** Comment on it to take it; a maintainer then assigns you. An issue with an assignee is
  taken, even when it looks quiet: ask on it instead of starting in parallel. When you stop, say so on the issue
  so it can be assigned again.
- **Announce shared files first.** Before you change `families/*.yaml` (shared keys with easel), `schemas/`,
  `types/`, `tests/conftest.py`, `pyproject.toml` or `.github/workflows/ci.yml`, say on the issue which of them
  you will touch and why. `src/kiln/_common/` is never changed here at all (see below). Others may be changing
  them at the same time.

## How to propose a change

1. Fork the repository and branch from `main`. One idea per branch and per pull request.
2. A complete pull request carries, together:
   - the code and its tests, with a sabotage check for every new guard (see "Tests");
   - the doc section that describes the behaviour (a runbook, `docs/REGISTRY.md`, `docs/reference/`);
   - a row in the runbook's failure table or in `docs/lessons/` when you learned something the hard way;
   - an ADR in `docs/decisions/` (`ADR-NNNN-<topic>.md`, one decision per file) when you chose between options
     that a later contributor will ask about;
   - one line in [CHANGELOG.md](CHANGELOG.md) under "Unreleased".
3. **A format change is a version bump.** A record format changes only as a new schema version
   (`kiln/<name>/<n+1>`) plus a reader for the old version and a test of both. A published schema is never
   edited in place.
4. **Shared code starts in easel.** `src/kiln/_common/` is a byte-identical copy of easel's `_common`. Never
   edit it here: change it in easel, copy it with `python tools/sync_common.py --to ../kiln` from the easel
   checkout, and open the pull requests in both repositories; `--check` must print `identical` in both
   ([ADR-0001](docs/decisions/ADR-0001-vendored-common.md)).
5. Fill in the pull request template. A maintainer reviews it; it merges once CI is green and the review
   comments are answered.

## What a change has to meet

### The gates

Run them from the checkout before you open the pull request; CI runs the same set on Linux and Windows, plus a
gitleaks scan of the history ([ADR-0002](docs/decisions/ADR-0002-secret-scanning.md)).

```
python -m ruff check .
python -m pytest -q -m "not gpu and not machine and not comfy_live and not net"
python tools/privacy_check.py --public .
python tools/gen_formats_doc.py --check
python -m kiln qa docs
python tools/sync_common.py --check
python -m kiln families check
python -m kiln types check
python -m piplicenses --from=mixed --ignore-packages easel kiln pip setuptools --allow-only "<the list in ci.yml>"
```

- **The licence gate** needs `pip-licenses>=5.0` (part of `[dev]`; older versions cannot read the
  `license = "MIT"` expression and report `UNKNOWN`). Copy the `--allow-only` value from the "Licence gate" step
  of `.github/workflows/ci.yml`. A new dependency whose licence string is not on that list fails until you add
  the exact string there, in the same pull request, with one sentence saying you read the licence. GPL and AGPL
  packages never go on the list: such programs stay outside the package and are driven over HTTP or as a
  subprocess (see [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md)); `tests/test_no_agpl_imports.py` keeps
  them out of `src/`. `tests/test_licence_gate.py` proves the gate
  refuses a GPL-classified package; it was run on 2026-11-06 and refused it with exit 1.
- **Notices** are regenerated with `python tools/gen_notices.py` whenever a dependency changes.
- A verb that prints `planned: <task>` is not built yet; its gate passes once that task lands.

### Tests

- Every new behaviour has a test. Every new guard (a refusal, a gate, a lock, a validator, a privacy pattern)
  also gets a sabotage check: break the guard on purpose, run the test and watch it fail, then restore the
  guard. List each `mutation -> failing test` in the pull request.
- Tests run offline. Anything that needs a GPU, a configured machine, a live ComfyUI or the network carries the
  marker `gpu`, `machine`, `comfy_live` or `net`; CI deselects those.
- Fixture images are generated at test time into the ignored `tests/fixtures/_gen/`; nothing binary is ever
  committed. Docstrings and fixtures use the demo subject (`demo_robot`, trigger `dmrbt`) and 2030 dates.
- `tests/` has no `__init__.py`, so every test file name must be unique across all its subfolders.

### Standing rules

- Never loosen a gate, a pattern, an allowlist or a test to make something pass. Fix the cause, or open an issue
  that says why the rule is wrong.
- Only a human judges images. No code path turns an assistant's reading of an image into a verdict.
- Subjects are fictional adults only: no spec, intake answer, caption, example or test prompt that makes a
  subject read as a minor or depicts a real person.
- GPU work is submitted through the scheduler interface; no command starts a training run or a render that the
  scheduler did not grant. On an adopted instance, the two host lines that `kiln train plan --emit-legacy`
  prints are handed to the operator, who runs them; no tool and no agent runs the queue line.
- No absolute path, host-specific port or e-mail address in a shipped file. The one address allowed repo-wide is
  ComfyUI's public default, `127.0.0.1:8188`.
- Training runtimes stay pinned by hand (`runtime/train.lock.txt`), never written from an existing environment:
  a freeze of an editable install records a local folder.
- Records are written through the store (atomic writes, OS locks), never by hand. Generated files
  (`docs/STUCK.md`, `docs/reference/formats.md`, `.github/ISSUE_TEMPLATE/pitfall.yml`, the tables in
  THIRD_PARTY_NOTICES.md, every `INDEX.md`) are regenerated, never edited.

### Where to contribute, and the validator for each

| you add | where | checked by |
|---|---|---|
| a LoRA type | `types/<id>/` with its nine files: `intake.yaml`, `checklist.yaml`, `caption_rules.yaml`, `eval_suite.yaml`, `dataset.toml`, `thresholds.yaml`, `spec.template.json`, `card.md.tmpl` (with the `{{repo_links}}` slot), `README.md` | `kiln types check <id> --strict` (warnings fail too) |
| a model family | `families/<id>.yaml`, with its own `licence_summary` (and source URL), `host_ram_gb` and the training block | `kiln families check --strict` and `easel families check --strict`; the keys shared with easel must agree (`sync_common.py --check`) |
| an image source | a module in `src/kiln/sources/` that subclasses `kiln.sources.base.Source` (`kind`, `run(job, ctx)`) | its tests against a fake source, no network |
| a scheduler adapter | an implementation of the scheduler interface in `_common` (through easel, see above) | its tests, including the fail-closed path |
| a runbook fix | the runbook's failure table, dated | `kiln qa docs` |
| a `qa` rule | `qa.toml` at the repository root (the logic in `src/kiln/qa.py` is the same file in both repos) | `kiln qa docs`, `kiln qa agents`, `kiln qa tests` |

`qa.toml` has three tables. `[docs]`: which files are docs and runbooks, the seven runbook headings, the
entry-condition table header, the pending marker, the first year allowed in output blocks, the paths that must
exist (`path_roots`, `path_ignore`), the forbidden GPU command lines (`[[docs.gpu]]`) and the concrete values an
adopt runbook's command lines may not hold (`[[docs.adopt_concrete]]`). `[agents]`: the AGENTS.md line limit,
its parts, the words its "Never" part must carry and the host files. `[tests]`: where tests live and their file
pattern. An unknown key is a usage error (exit 2), so a typo cannot switch a rule off.

### Doc rules

- A runbook has seven sections, in this order and with these exact headings, which `kiln qa docs` checks:
  `## 1. Purpose and who decides`, `## 2. Entry conditions`, `## 3. Commands`, `## 4. Expected output`,
  `## 5. Gate and operator question`, `## 6. Symptom, cause, action`, `## 7. Record`.
- Every command in a doc must run as written; `qa docs` checks commands against `--help`. Write variable parts as
  `<id>`, `<n>`, `<version>`, and paths as `<home>`, `<models_root>` or `~/kiln-home`, never as a real path.
- Expected-output blocks come from the demo under the frozen clock (`KILN_NOW=2030-01-01T00:00:00+00:00`) with
  `--redact-paths`, stdout only. Their dates are 2030 or later; the clock warning never appears in them.
- A dated fact says its date. Troubleshooting pages, lessons and the changelog carry real dates; runbook output
  blocks never do.
- **Planned steps.** A command line that names a verb or subverb not built yet ends with `# planned: <task>`,
  and every doc an agent reads states the one rule: "A step marked planned is not built: report it once and
  stop at that stage." When you build a verb, grep `docs/` and `schemas/` for `kiln <verb> ` and register
  each subverb they name, as a `set_defaults(planned="<task>")` placeholder if it is not built; otherwise
  `qa docs` reports "no such subcommand" for it. Whoever builds a stub removes exactly that stub's
  "planned" test in the same change.
- An entry condition is `kiln next --stage` or a literal line the code prints today; never invent the output
  of a verb that is not built yet.
- Public lessons from a release (troubleshooting rows, the changelog) carry no counts, exact dates, version
  lists or outfit details that would identify that release.
- A runbook command line carries every flag the verb requires (`qa docs` checks that the flags exist, not
  that the required ones are there), and a guide names only paths that exist; a planned module is described
  by its task. A file of the sister repository is written "easel's `<path>`", which the paths check skips.
- **Who acts.** A failure-table action that belongs to someone else starts with `operator:` or
  `maintainer:`. No agent-facing row tells an agent to start, stop, restart or suspend a GPU process: the
  action is to report it to the operator, or the submit line.
- **Each tool in its own terms.** Docs describe what kiln does, never the machine it was built on: no
  description of a host's scheduler, guards, hooks, backup timing or folder layout, and no host numbers
  (exact RAM sizes, timings, a card's reported memory). The external scheduler is configured in
  `[scheduler]`; host facts live only in an instance's `[legacy]` and `[adopt]` tables. Shipped defaults are
  neutral (for example `limits.disk_floor_gb = 20`); a private instance sets its own.
- The maintainers' private sweep: before a release, a maintainer scans the whole tree and its history with their
  own private word list. Your gate does the same for every push you make.

### Your private word list and the pre-push gate

The shipped patterns catch shapes (paths, e-mail addresses, id formats); only you know your own names, folders
and ports. Keep them in a private list and let the gate check every push:

1. Copy `data/privacy/private-words.example.txt` outside every repository and outside any folder that is synced
   or shared, and fill in one value per line under each heading.
2. Run `python tools/install-hooks.py --words <your list> --identity "<your name> <your public e-mail>"`.
   Use the address your public commits should carry, for example your GitHub noreply address; the gate refuses
   commits authored under any other identity, so a commit made with a private address cannot slip out.
3. The hook runs `tools/pre-push-gate` on every push: added lines, file names, commit messages and identities
   against both lists, plus gitleaks when it is installed. It prints counts, never the matched text, and it
   refuses the push when your list cannot be read. Rewrite the offending commit, then push again.

### Hand-offs

A session that stops before its pull request is finished ends with one message in the shape of
[docs/HANDOFF-TEMPLATE.md](docs/HANDOFF-TEMPLATE.md), posted on the issue: what was done, what is open, what the
next person runs first.

### Contributor notes: pitfalls already paid for

Each note is a symptom someone hit and the rule that avoids it. Add yours in the same pull request as the fix.

**Packaging, imports and the CLI**

- `importlib.util.find_spec("pkg.sub.mod")` raises `ModuleNotFoundError` instead of returning `None` when the
  parent package is missing. Wrap a dotted `find_spec` in `try/except ModuleNotFoundError`.
- CLI help strings carry no parentheses: a verb that is not built yet prints its help inside parentheses after
  `planned: <task>`, and nested parentheses become unreadable. Use a colon or a semicolon instead.
- Never name a source, test or docs folder `instance/`, `journal/`, `build/` or `dist/`: `.gitignore` matches
  those names at any depth, and the folder would silently never be committed.
- The MIT text itself contains the words COPYRIGHT HOLDERS, so the placeholder pattern matches only the bracketed
  form (an opening angle bracket, COPYRIGHT HOLDER, a closing angle bracket). Keep the brackets when you touch
  that pattern.
- `pip install -e .` downloads `setuptools>=77` into an isolated build environment, so it needs network access.
  Offline, install `setuptools>=77` into the venv first and add `--no-build-isolation`.
- pip-licenses compares whole licence strings: a package that declares `Apache-2.0 OR BSD-2-Clause` needs exactly
  that string on the allow list. Never add `--partial-match`, which would let an unreviewed licence through.
- A tool that records an interpreter path for later (a hook, a launcher) writes `os.path.abspath(sys.executable)`,
  never `Path(sys.executable).resolve()`: on Linux a venv's `python` is a link to the base interpreter, and
  resolving it drops the venv and its packages.
- The privacy scan reads `tests/` too, with no allowlist there. A test that needs a value which looks private
  (a path, an e-mail address, a placeholder) builds it at run time from fragments, so the literal never sits in
  the file.
- A planned placeholder subparser that must take any words (`--help` too) uses `prefix_chars="\x00"`,
  `add_help=False` and `nargs="*"`; `argparse.REMAINDER` does not swallow a leading `--flag`.
- A verb that returns at once hands a scheduler or any helper a stderr `say`; stdout carries the answer only,
  so a caller that reads stdout is not fed progress notes.
- Running Python with the working folder inside `src/kiln/commands/` makes `commands/types.py` shadow the
  standard `types` module. Run scripts from the repository root or a scratch folder, with `python -I`.
- Printed output carries no real dates (a family's measurement date, for example): doc blocks are pasted from
  it under the frozen clock, and `qa docs` refuses dates before 2030 there. Dated facts stay in the data file.
- When an error from a lower layer reaches the user, say what is wrong in your own words; never insert the
  text of an exception that may carry an absolute path (`store.read_record` errors do).
- Never write records line by line to save a call: register an image through `kiln.project.images.register`
  and ask the store's owner for a missing argument instead.

**Files, encodings and processes**

- `Path.write_text` on Windows writes CRLF. Fixtures whose bytes matter, and any scripted rewrite of a repo
  file, use `write_bytes` (or `newline="\n"`); check line endings afterwards.
- `config set` must keep a file's layout: read TOML as bytes (or with `newline=""`) so CRLF survives.
- A shell heredoc mangles backslashes (`\n` becomes a line break, doubled backslashes collapse), and an unquoted
  one runs backtick spans as commands. Write patch scripts and journal text to a file, or use an editor
  tool; never pass backslash-heavy code through a heredoc.
- A command that reads stdin (for example `cat > file`) in a non-interactive shell call blocks until the
  timeout.
- On Windows a venv's `python.exe` is a launcher, so `Popen.pid` is not the interpreter's pid: a child that
  must be identified prints its own `os.getpid()`. Killing the launcher still ends the interpreter.
- `msvcrt.locking` locks a byte range nobody else can read: lock one byte far past the diagnostics
  (offset `2**30`) and keep the diagnostics at offset 0.
- A relative scan root such as `.` from a subfolder never reaches the repository root: absolutize with
  `os.path.abspath` before walking parents, and never follow symlinks for repo-relative paths.
- Pillow's PNG writer and `zlib` depend on the zlib build, so a sha256 of generated media can differ in CI.
  Fixture media that a record hashes are written byte by byte (stored deflate, sorted JSON).
- Piping a test run into `tail` hides pytest's exit code: write the output to a file and print `exit $?`.

**YAML, JSON and schemas**

- YAML 1.1 reads a bare `on:` key as `true`; loaders reject unknown keys, and keys avoid `on`, `yes`, `no`.
- A YAML flow mapping such as `{label: X, hint: a, b}` splits a plain value at the comma: quote every flow
  value that holds a comma, or use block style. `additionalProperties: false` is what catches it.
- `store.read_record` raises `yaml.YAMLError` (not `ValueError`) on broken YAML: a reader that skips bad
  records catches `(OSError, ValueError, yaml.YAMLError)` and reports the file as a warning.
- An optional block where `{}` means "all defaults" is tested with `is not None`, never with `bool()`.
- Leave a key out when it does not apply, rather than setting it to `null`, unless the schema allows null.
- Before freezing a schema, validate every file in the tree that names it, not only your own examples, and for
  data adopted from an older store map every row in memory and count errors by path and keyword.
- A record schema governs its own `type` field: an enum of the shipped ids there would block every new LoRA
  type; folder-name rules cover only the `type-*` schemas.
- Adding a schema file changes `docs/reference/formats.md`: regenerate it (`python tools/gen_formats_doc.py`)
  in the same change, or `--check` fails. Prefer an existing format.
- Check a dropped JSON key as `"key":`, never as a bare word: the same word can be a legal value.

**LoRA types**

- A required intake question has `default: null`; a rule such as "at most one of these" goes in its `note`.
  `kiln types check` warns when a required question has a default.
- `kiln types check` strips HTML comments before it looks for the card's required slots, so a comment that
  names `{{repo_links}}` cannot hide a missing slot.
- Caption lint reads the project's spec conventions (`caption.min_words`, `max_words`) first and the type's
  `caption_rules.yaml` second.
- Shipped defaults (a style tag, a mouth rule, thresholds) are generic priors marked "validate on your subject
  first", never a value measured on one subject.

**HTTP and downloads**

- Compare HTTP header names case-insensitively in tests: urllib sends `User-agent`.
- `http.client` `read(amt)` returns `b""` at an early EOF instead of raising: compare the bytes received with
  the announced total before verifying a download.
- urllib forwards ordinary headers on a redirect: send a token only with `add_unredirected_header`, never in
  the URL.
- A download whose own suffix is `.json` or `.html` is exempt from the login-page sniff (a repository's
  `config.json` is not a login wall).
- On Windows a refused loopback connection takes about two seconds; a fast "unreachable" test uses a
  listening socket that never answers, with a short client timeout.
- A ComfyUI history entry is done when `completed` is true; `status_str` decides only when the flag is absent.

**Tests**

- Tests copy `examples/demo/` at test time and never depend on files another change may regenerate; read a
  fixture's flags (for example `legacy_untriaged`) before asserting on them.
- A fake lock raises only for the one store root under test and hands every other root to the real lock, or
  the journal's own lock breaks with it.
- Plant copies of a test record first, then edit the originals.
- Assert a command against an independent reference first, and take literal numbers from that reference,
  never from mental arithmetic.
- A test that names the other tool literally breaks when copied between the repos: use a made-up tool name.
- A `by` field in a fixture names only a real verb (`kiln images import`), never an invented command a reader
  might try to run; otherwise leave the optional field out.
- Tests pin their own schema (monkeypatch `config.default_schema_path`), and `config.load` validates only
  when an instance file exists, so "no kiln.toml found" is not buried under a schema error.
- `ruff check` given a YAML file parses it as Python: run `ruff check .` or pass `.py` paths only.
- Never write a percent-encoded separator, the literal frozen-clock warning, a drive-letter path or a low
  `dec-NNNN` id in a test: build such strings at run time (`urllib.parse.quote`, `ids.now_var`, fragments),
  assert decoded values, and use `dec-9001` and up and 2030 dates.

**Privacy of what you write**

- Pattern text and test needles break their own literal with a one-character class, so the scanner does not
  match its own pattern file.
- Invisible or bidirectional characters in a source file come from code points (`chr(0x200b)`), never typed
  or pasted: an editor tool can turn an escape into the real character.
- Never write the CODEOWNERS placeholder (the word handle in angle brackets) in a repo file: the placeholder
  pattern flags it everywhere. Name the value instead (`<author>`).
- Exploration output (a column, a file list) prints only enumerations you chose, counts and masked shapes, never
  a free-text value or a path component.
- Port behaviour, never product names: describe what the old code did ("a cloud placeholder file") in your own
  words.

**Writing an exporter for an adopted workspace**

- A schema file on disk can be older than the live database: compare against the live constraints (or widen
  them to the export's values and say so) before rebuilding from it.
- A loader shared by two tools accepts a target that holds only the other tool's tables, refuses only a clash
  with its own, and skips what it does not load before it checks for a collision.
- Round-trip stored JSON text byte for byte: re-serialise only when `json.dumps` reproduces the text exactly.
- Count a class of output lines by its exact phrase, never by a substring another class shares; equality is
  export against database, and expected counts are a separate, informational line.
- Find where a file lives by reading the code that writes it, never from a path quoted in a plan or a note.
- Read a live SQLite file only with `file:<path>?mode=ro` and plain `SELECT`s (column names from
  `SELECT * FROM t LIMIT 0` and `cursor.description`), never a `PRAGMA`; and read the caller before you fix a
  return shape the task leaves open.

## Questions

Ask on the issue you are working on, or open a new one with the closest template. Security reports go through
the private route in [SECURITY.md](SECURITY.md).
