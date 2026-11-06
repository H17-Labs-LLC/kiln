"""Training: plan a run (`plan`), check its gates (`preflight`), list runs (`runs`), and the adopt-mode emitter.

`plan` writes `runs/<run-id>/run.json` with status `planned` and starts nothing. On an adopted instance
`legacy_emit` renders the host workspace's own launcher and dataset config and writes its queue file, so the
host runner trains on the operator's go. kiln's own launcher, queue, smoke and tune verbs come later.
"""
