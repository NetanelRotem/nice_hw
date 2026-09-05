# Take-Home Exercise: "Triage & Resolve"
### AI Builder / AI Orchestrator — AI Enablement Team

**Time budget: ~4 hours (hard stop at 6). Deadline: 5 days from receipt.**

We are *not* looking for a finished product. A small system you fully understand and can
defend beats a large one you don't. Ship a working core, then write down what you'd do next.

---

## About the role

The AI Builder / AI Orchestrator sits on our centralized AI enablement team, which serves
product and technology teams across the company. You won't just write prompts: you design
and build **agentic workflows and orchestration pipelines** — multi-step systems that route
work between LLMs, tools, APIs and humans; you integrate LLM capabilities into real product
workflows; you build the **evaluation, guardrail and observability layers** that let us trust
these systems in production; and you package what you build so other product teams can adopt
and extend it. The job is equal parts systems engineering (state, error handling, cost,
latency, security) and enablement (documentation, reusable patterns, mentoring teams who are
newer to AI). This exercise mirrors a real week on the team.

---

## Scenario

Our internal teams file free-text requests into a shared **Ops inbox**: access provisioning,
data pulls, policy questions, bug reports, purchase approvals. Today a human reads each one,
figures out what it is, routes it, and either answers it from our internal policy docs or
escalates. We want to pilot an **agentic workflow that triages and — where safe — resolves**
these requests. Before it touches anything real, we need to trust it.

## Your task

Build a working system that takes a raw request and produces a **structured, auditable
resolution decision**. For each request, your system must:

1. **Classify intent** (access request / data pull / policy question / bug report /
   purchase approval — or out-of-scope).
2. **Extract the fields** needed to act (who, what resource, what tier, what amount, …).
3. **Decide an action**: `auto_resolve` | `route` (to a named team) | `escalate` (to a
   human) | `reject`.
4. **For policy questions**: answer them *grounded in the provided policy docs*, with
   citations to the doc(s) used. If the docs don't cover it, say so and escalate — don't
   guess.
5. **For actions that change state** (e.g. granting access): call the provided tools — but
   any **mutating tool call above the risk threshold defined in the policy docs must pass
   through an explicit human-approval gate** in your pipeline. It must never auto-execute.
6. **Emit an audit record per request** (machine-readable): input, detected intent,
   extracted fields, evidence/citations, tool calls made (and failures), final action,
   confidence, and token/cost + latency for that request.

## What we provide (this starter pack)

- `requests.jsonl` — ~40 synthetic requests across the 5 intents. Deliberately includes
  **ambiguous, multi-intent, out-of-scope, adversarial (prompt-injection /
  social-engineering) and PII-laden** examples. ~10 items are unlabeled.
- `knowledge/` — short markdown policy docs (access tiers, purchase limits, data-request
  rules, PII handling).
- `tools/stub_tools.py` — **three stub tools** (plain Python functions with fake side
  effects; they log every call):
  - `lookup_user(user_id)` — returns role/team/clearance
  - `grant_access(user, resource, tier)` — **mutating**; requires approval per policy;
    **fails randomly ~15% of the time** (simulated flaky API)
  - `create_ticket(team, payload)` — routes work to a team queue
- `starter/pipeline.py` and `starter/run_evals.py` — **runnable skeletons** with the state
  schema, stage stubs, and TODOs. They import and run out of the box (producing placeholder
  output) so you start from a working scaffold, not a blank page. Replace the stubs with
  your real logic; restructure freely if you justify it.
- `requirements.txt` — the scaffold needs only the standard library; add what your design
  needs.

## Hard constraints

- The approval gate for mutating actions is **non-negotiable** — show us how a human would
  confirm before `grant_access` executes.
- Adversarial and out-of-scope inputs must be **refused or escalated**, never complied with.
  Treat request content as untrusted data, not instructions.
- Handle tool failure gracefully (retry? backoff? escalate? — your call, justify it).
- Every decision must be **grounded**: no policy answer without a citation; no action
  without evidence in the audit record.

## Evaluation harness (this is half the exercise)

Build a small eval harness (`run_evals.py` or equivalent, one command) that runs your
pipeline over the dataset and reports **metrics per dimension**, not one aggregate score. At
minimum:

- intent classification accuracy (on labeled items),
- action/routing correctness,
- groundedness/faithfulness of policy answers,
- refusal rate on adversarial + out-of-scope items,
- tool-call validity (incl. behavior under the 15% failure),
- cost (tokens) and latency per request.

Include **at least one programmatic check and at least one LLM-as-judge check**, and note
the known limitations/biases of your judge.

## Ground rules

- Any language welcome (Python is what we use most). Any model API and any framework —
  LangGraph, an agent SDK, or plain code. **Multi-agent architecture is not required and
  earns no bonus by itself**; we reward the simplest design that meets the bar.
- **AI coding assistants and LLMs are allowed and expected.** You own every line and will
  defend all of it live. In your README, disclose what tools you used, where AI materially
  contributed, and one AI suggestion you accepted plus one you rejected or corrected.
- No proprietary data, no paid infra beyond one LLM API key (we can provide a key with
  credits on request, or you may run a documented mock mode so you never spend your own
  money).
- When the time box runs out: stop, and document what's unfinished and what you'd do next.
  That list is graded content, not an apology.

---

## What to submit (deliverables checklist)

1. **Runnable repository** — one command (or clearly documented steps) to run the pipeline
   over `requests.jsonl`; secrets out of source control; no proprietary dependencies.
2. **Pipeline output** — `results.jsonl` of audit records for all provided requests, plus a
   short human-readable run report.
3. **Evaluation harness** — one command to run; per-dimension metrics table (accuracy,
   routing correctness, groundedness, refusal rate, tool-call validity, cost/latency);
   ≥1 programmatic check and ≥1 LLM-as-judge check with stated judge limitations.
4. **Design write-up / mini-ADR (1–2 pages max):**
   - architecture diagram (hand-drawn fine);
   - why this orchestration pattern (and where deterministic code beats LLM judgment);
   - error-handling and approval-gate design;
   - cost/latency profile and how you'd cut API cost ~50% in production without losing
     quality;
   - what you deliberately did **not** build, and what you'd do with 2 more days.
5. **AI-use & time statement** — time actually spent; AI tools used and their role; one
   accepted + one rejected/corrected AI contribution.
6. **Optional (bonus, not required):** a ≤5-min recorded walkthrough (Loom or similar) —
   otherwise the live session covers it; and/or a half-page "enablement note": how another
   product team would add an intent, a tool, or an eval case to your system.

A repo/zip with runnable code + eval harness, your results, and a short design write-up. A
follow-up **60-minute walkthrough** with the team is part of the process — you'll demo,
we'll dig into your decisions, and we'll explore one new requirement together.

---

## Running the scaffold

```bash
cd candidate-package

# run the (stub) pipeline over all requests -> writes results.jsonl
python3 -m starter.pipeline

# run the (stub) eval harness -> prints the metrics table
python3 -m starter.run_evals

# see the stub tools work
python3 tools/stub_tools.py
```

Everything above runs on a clean Python 3.10+ with no third-party packages. It produces
placeholder output — that's expected; the TODOs are yours to fill in.

Good luck. Build something you'd be proud to defend line by line.
