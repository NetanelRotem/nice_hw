# Post-eval analysis — planned, not built (out of time)

**Status: proposed next step, not implemented.** The 4-hour budget went to the pipeline,
the eval harness, and the demo. This file records what I wanted to build and — since the
analysis was mostly worked out by hand against this run's `results.jsonl` — the
recommendations it would have produced, so the thinking isn't lost.

---

## What it would be - need to think - but will have pandas/.

A small **`post-eval` skill** + a backing script (`eval/analyze_results.py`), kept
**separate from `run_evals.py`**. The harness stays lean and deterministic (it's graded);
the analysis layer is exploratory, human-facing, and is where a heavier dependency
(pandas) would be justified — framed as "the layer that still works at 10k rows", with the
40-row batch as just today's input.

The skill would package *how we read eval output* so other teams do it consistently:
`run eval → analyze → decide what to change → re-run`. For this repo the value is marginal
(the eval runs a handful more times); the value is the reusable pattern, which is what the
enablement role is about. A more clearly repeatable variant would be an **"eval regression
diff"** skill — re-run, diff each metric against a saved baseline, flag what moved.
