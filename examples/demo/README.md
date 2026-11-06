# The kiln demo instance

A complete, synthetic kiln home: one project, `demo_robot` (a service robot, trigger `dmrbt`), with every
record kiln keeps, dated in January 2030. The tests copy this folder, generate its media into the copy and run
the commands on it; the runbooks show what those commands print here. It is the contract the commands are
tested on: `tests/test_schemas.py` validates every record against `schemas/`.

What is in it (`projects/demo_robot/`):

- `project.yaml`, `state.json` (a goal is set: version 0.3.0 from the easel demo run), `intake.yaml`, `spec.json`
  (6 checklist items), `coverage.yaml`, `showcase.yaml`, two card templates;
- `images.jsonl`: 16 dataset images under `dataset-images/` (root `images`; one child crop, one untriaged image,
  the front anchor labelled `anchor:front` for the jobs file's `@front`, `lighting:<bucket>` labels for the
  coverage board) and 3 kept renders imported from the easel demo run (root `imports`, verdicts and checklist
  checks from that run);
- `captions.jsonl`: one approved caption per training image, drafts before some approvals;
- `snapshots/0.1.0.json` (7 members) and `snapshots/0.2.0.json` (12 members, `from_version` 0.1.0), so
  `kiln dataset freeze 0.3.0 --from 0.2.0` has an old version (12 + 3 new = 15 members);
- `runs/run-20300102-001/run.json` (a finished training run, step 800 picked), `evals/suites/core.yaml`,
  `review/{scores,pairs,picks,feedback}.jsonl`, `decisions/dec-0001.md` (decided), `dec-0002.md` (open),
  `jobs/batch-01.json` (an image-source jobs file).

Media: nothing binary is committed. `python tests/fixtures/make_fixtures.py --place <copy> --demo kiln` writes
the 16 dataset images, the 3 imports and 4 eval renders into a copy, byte for byte matching the sha256 values
in the records.
