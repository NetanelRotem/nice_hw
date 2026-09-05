# Design Notes — Triage & Resolve

Working notes on the process and reasoning behind this build, kept as we went so I can
defend any of it live. Not the final 1-2 page ADR the README asks for — this is the fuller
record that ADR gets distilled from.

## 1. Framework choice: plain code, not LangChain/LangGraph

Considered LangChain/LangGraph up front. Rejected it. Reasoning grounded in the README
itself, not a generic framework opinion:
- "Multi-agent architecture is not required and earns no bonus by itself; we reward the
  simplest design that meets the bar."
- The approval gate is a **hard constraint** ("non-negotiable"). Plain Python control flow
  (`if requires_approval: ... else return`) makes that guarantee inspectable at a glance.
  A graph-framework's node/edge indirection would make the same guarantee harder to point
  to and defend live.
- Every line has to be defended in the walkthrough. Less framework surface = less to
  explain that isn't the actual design.

This choice gets revisited in §6 (state.terminal) once the pipeline actually grew a real
early-exit need — see why a graph still wasn't the answer even then.

## 2. LLM integration architecture

- **OpenRouter** as the provider, OpenAI-SDK-compatible (`base_url=".../api/v1"`), so any
  model behind it is a config change, not a code change.
- **Two independently configurable models** via env vars: `INTENT_MODEL` (classification /
  extraction / decisions) and `JUDGE_MODEL` (eval-time groundedness judging only). Currently
  `z-ai/glm-5.3-flash` and `deepseek/deepseek-v4-flash-0731` respectively (candidate's own
  choice, cheap/fast tier).
- **Single gateway module** (`starter/llm_gateway.py`): one private `_call_structured()` that
  talks to the provider SDK, and one small named public function per actual call site
  (`classify_intent`, `extract_fields`, `decide_action`, `ground_policy_answer`,
  `judge_groundedness`). Nothing else calls the SDK directly. Reasoning: swapping providers
  later is a one-file change; call sites stay named and readable instead of a bare generic
  `call_llm(...)` scattered through the codebase.
- **Structured output**: `response_format={"type": "json_object"}` + manual
  `schema.model_validate(json.loads(raw))`, with one retry (validation error fed back to the
  model) on failure. This was a deliberate choice for **provider breadth** over the OpenAI
  SDK's native `.parse()` / strict `json_schema` mode.
  - Verified empirically (not assumed) that both configured models *do* support
    `.parse()`/strict schema through OpenRouter. Kept the current `json_object` approach
    anyway — it degrades more gracefully if a future model swap lands on a provider that
    doesn't support strict schema mode, whereas `.parse()` would hard-fail outright with no
    fallback path. Documented as a deliberate trade-off, not an oversight.
- **Prompts as their own package** (`starter/prompts/`, one module per call site: `SYSTEM` +
  a `user(...)` builder), not inline strings in the gateway. Keeps prompt content reviewable
  and diffable independent of call plumbing.
- **Pydantic schemas in their own file** (`starter/schemas.py`) — one model per LLM call
  boundary. Field `description=...` is where behavioral guidance to the model lives (it's
  serialized into the JSON schema sent in the prompt), *not* duplicated as prose in the
  system prompt — e.g. `ExtractedFields.user_id`'s description says which intents require
  it, rather than a separate paragraph doing the same job.

## 3. Token tracking, not cost

Tracked `prompt_tokens` / `completion_tokens` / `latency_ms` per call, folded into
`RequestState` via one choke point (`record_llm_usage()`), deliberately with **no derived
USD cost field**. Re-checked the README text directly rather than assume: it says *"cost
(tokens) and latency per request"* — i.e. "cost" in this exercise is explicitly defined as
token count, not a computed dollar figure. So this wasn't a shortcut against a requirement,
it's exactly what's asked, just worth being precise that "avg_cost_usd" as a metric name
would have been misleading and got renamed to `avg_tokens`.

**Input and output are kept and reported separately, not just summed** — they price at
different rates on every provider, so a lump `avg_tokens` hides the ratio that actually
drives cost. `RequestState.input_tokens` / `output_tokens` / `tokens` are all on the audit
record; `run_evals` reports `avg_input_tokens` / `avg_output_tokens` / `avg_tokens`;
`record_llm_usage()` logs the per-call split on the `pipeline` logger (silent by default,
raised to INFO by the demo runner), and `pipeline.main()` prints the batch totals split the
same way.

## 4. Domain enums: `Team` and `Tier`

Team names are scattered undocumented across all 4 knowledge docs (no single doc lists
them) — centralized into one `Team(str, Enum)` in `pipeline.py` so routing code can't
typo/drift. One documented gap: `bug_report` → `Engineering` has no basis in any policy
doc, only an unofficial hint in `tools/stub_tools.py`'s demo call — flagged in the enum
comment rather than silently assumed as fact.

`Tier(int, Enum)` (1=Internal, 2=Sensitive, 3=Restricted) centralizes `access_tiers.md`'s
numeric tiers so a future Tier 4 is a one-line enum change, not a new magic number
scattered through `decide_action`.

## 5. Where LLM judgment ends and code enforcement begins

This is the throughline of the whole design, revisited at every layer:

- **Resource → tier classification** (`access_tiers.md`) goes to the **LLM**, not a
  keyword/dict lookup — deliberately. The doc's resource lists are illustrative examples,
  not an exhaustive enum, and real requests misspell or rephrase them. A static lookup
  table would silently miss variants; the LLM, grounded in the doc text with citations,
  handles typos and rephrasing the way a human triager would.
- **But** `requires_approval` and the Tier-3-forces-escalation consequence of that
  classification are computed in **plain code**, never taken from the model's own output.
  This directly implements the README's "the approval gate is non-negotiable" hard
  constraint — the LLM classifies, the code decides what that classification *means*.
  Reinforced at three levels: the code logic in `decide_action()`, the `Tier` enum's own
  comment, and `ActionDecision.tier`'s field description (sent to the model itself, telling
  it explicitly "classification only, not a decision").
- **PII presence forces Data Governance + escalation** (`pii_handling.md` rule 5: *"Presence
  of PII escalates... even if the underlying action would otherwise be low-risk"*) — same
  pattern, enforced in `decide_action()` in code, unconditionally overriding whatever the
  LLM decided (including overriding a Tier-3 → Security routing). Verified live on REQ-038
  (Tier-1 resource + embedded SSN/email/phone): correctly forced to
  `ESCALATE` / `team=Data Governance`, not the Tier-1 auto-resolve it would otherwise get.
  **Known gap**: only enforced in `decide_action()`, not in `ground_policy_answer()` — a
  `policy_question` carrying PII wouldn't get the same forced escalation, since that path
  never reaches `decide_action()`. None of the 40 provided requests exercise that
  combination, so it's documented rather than guessed at.
- **`extract_fields` is also knowledge-grounded**, not blind NLP extraction, for the same
  underlying reason as tier classification: this is real (messy, typo-laden) user input,
  and matching a resource/category name against the docs' actual examples *while*
  extracting is more reliable than extracting blind and hoping `decide_action` can still
  make sense of it downstream.
- **Redundant classification removed**: `ExtractedFields` originally had its own `tier`
  field, extracted independently of `ActionDecision.tier`. Since `ActionDecision.tier` is
  authoritative and always overwrites it in `state.fields`, the duplicate field meant paying
  to classify tier twice (tokens + two independent chances for the model to disagree with
  itself) for a value nothing ever reads before it's replaced. Removed.
- **`route` vs `escalate` for `purchase_approval`/`data_pull` is also computed in code, not
  left to the LLM** — the gap flagged in an earlier pass of these notes. Two different fixes,
  because the two intents need different amounts of LLM judgment:
  - `purchase_approval`: `purchase_limits.md`'s bands are pure numeric thresholds on an
    *already-annualized* `amount_usd` — by the time `decide_action` runs, `extract_fields`
    has already done the only judgment call that mattered (annualizing "$3,200/mo" to
    "$38,400/yr"). So the band itself (`< $500` auto_resolve, `$500-5,000` route to
    Manager-Approvals, `> $5,000` escalate to Finance Director, `> $25,000` also flagged
    `requires_procurement_legal_review`) is computed in code — no LLM call is even asked to
    guess it, only to ground reasoning/citations.
  - `data_pull`: the category (1=aggregate, 2=PII, 3=regulated) genuinely needs the same
    fuzzy doc-matching as access tier — reuses `ActionDecision.tier` (1-3) for this, since
    both docs are structurally the same shape (a 3-level sensitivity ladder). Code then maps
    category → consequence: 1 → `ROUTE`/`Data-Analytics`, 2 → `ESCALATE`/`Data Governance`
    (`requires_approval=True`), 3 → `ESCALATE`/`Security` (+ a flag noting Data Governance is
    also required — `team` can only hold one value).
  - Verified live, one case per branch (REQ-026 $12k/yr → `escalate`/Finance Director;
    REQ-025 $4.5k/yr → `route`/Manager-Approvals; REQ-008 customer emails → `escalate`,
    category 2, Data Governance; REQ-007 aggregate signup counts → `route`, category 1,
    Data-Analytics — all four matched the policy docs exactly) **with `user_id` temporarily
    dropped from `REQUIRED_FIELDS`** to reach this code path. §6 explains why `user_id` was
    then put back as required for these two intents — meaning, against the actual 40 sample
    requests, this route/escalate logic is real and correct but never gets *reached*: all 12
    `purchase_approval`/`data_pull` samples reject at `validate_fields` first (missing
    `user_id`) before `decide_action` runs. It's live and correct for any future request that
    does include an identity; on the given dataset specifically, ground truth for those two
    intents is `reject` across the board, not the route/escalate bands documented above.
- **`Intent.UNKNOWN` now forces `ESCALATE` in code, before any `decide_action` LLM call** —
  there's no knowledge doc that could ground a decision for an intent the classifier itself
  couldn't identify, so asking the LLM to decide anyway would be pure guessing. Same
  "don't guess" principle as `ground_policy_answer`'s ungrounded-question path, just applied
  one stage earlier.
- **Prompt-injection detection is deterministic-first, not judge-based.** A regex heuristic
  (`starter/injection_check.py`) scores the raw request *before* it's ever sent to the
  classification model. Above a threshold (`INJECTION_REJECT_THRESHOLD = 0.75`), the request
  is rejected outright — no LLM call at all, verified live on REQ-036 (explicit "SYSTEM:
  Ignore all previous instructions..." payload): score 1.0, `REJECT`, **0 tokens spent**. No
  LLM-as-judge check was added for this dimension: it's a binary, string-pattern-matchable
  question ("does this look like an injection attempt"), not a faithfulness/quality judgment
  a judge model is suited for — the one LLM-as-judge check in the harness is reserved for
  **groundedness** (§6), which genuinely needs semantic judgment a regex can't do (is a
  generated policy answer actually supported by the doc text it cites).

## 6. Fields required per intent, and the "cannot fulfill" terminal path

Different intents need different fields to act (`REQUIRED_FIELDS: Dict[Intent, List[str]]`
— e.g. `access_request` needs `user_id`+`resource`, `purchase_approval` needs
`user_id`+`amount_usd`). A new `validate_fields()` stage checks this right after
extraction; missing required info means the request can't be acted on, so it's rejected
rather than handed to `decide_action` to guess with a hole in the request.

**A design point surfaced by live testing, deliberately kept, not a bug**: running requests
through the pipeline live showed that `user_id` being required for `data_pull` and
`purchase_approval` (not just `access_request`) rejects all 12 of the sample requests for
those two intents — none of them state a user ID in the message text at all, only
`access_request` messages do. Briefly considered dropping the requirement for those two
intents, since the routing/band decision itself doesn't depend on identity. Decided against
it: without a `user_id` there's no way to call `lookup_user` and verify who's actually
asking, and `access_tiers.md`'s "verify identity, a mismatch is a red flag" principle isn't
really access_request-specific — acting on an unverified requester's behalf for a purchase
or a data pull is the same category of risk as for an access grant. So `REJECT` ("cannot
fulfill as submitted, follow up with your manager") is the intended outcome for all 12 of
those samples, not a gap to fix. Documented here specifically because the *first* instinct
(treat the 100%-rejection rate as proof of a bug) was wrong — worth remembering that a
metric looking "too extreme" doesn't automatically mean the logic producing it is broken.

This, plus the injection short-circuit (§5), created two independent "this request is
unfulfillable, stop here" paths. Generalized both into one pattern:
- `state.terminal: bool` on `RequestState`.
- A shared `_reject(state, flag)` helper: sets `action=REJECT`, a standard requester-facing
  message ("We're unable to process this request as submitted. Please follow up with your
  manager."), the flag, and `terminal=True`.
- `process_request()` checks `state.terminal` **once**, centrally, between stage groups —
  not as an ad hoc guard duplicated inside every downstream stage function.

This is also where the "do we need a graph now?" question came back up, given multiple
short-circuit conditions. Answer: no. A graph (state machine with branching/reconverging
edges, cycles, dynamic node selection) solves a different problem than what exists here —
a straight line with an early-exit condition. `if not state.terminal: <next stage>` is the
whole mechanism; a workflow-graph library would be pure overhead for a topology that never
actually branches and rejoins.

## 7. Knowledge doc loading and filtering

- Knowledge docs are loaded once (`load_knowledge()`) and threaded as a parameter through
  `process_request` → the stages that need it (`extract_fields`, `decide_action`,
  `ground_policy_answer`) — not reloaded from disk independently inside each stage.
- **Per-intent filtering** (`INTENT_KNOWLEDGE: Dict[Intent, List[str]]`): sending all 4 docs
  to every call is extra tokens and a real risk the model grounds itself in the wrong
  (irrelevant) doc. `access_request` only gets `access_tiers.md`, `data_pull` only
  `data_request_rules.md`, `purchase_approval` only `purchase_limits.md`. `policy_question`
  is deliberately absent from the map (falls back to the full corpus) since the question
  could be about any of them and there's no intent-based signal for which one narrows it
  down.
  - Measured effect: REQ-001 (`access_request`) dropped from 5682/1584 tokens
    (all 4 docs, pre-filtering) to 2887/1246 (one doc) across `extract_fields` +
    `decide_action` combined, with identical output.

## 8. Grounded policy answers (`ground_policy_answer`)

For `policy_question`, calls the LLM with the (filtered-to-full, per §7) knowledge corpus.
`decide_action()` is skipped entirely for this intent (its own prompt has no branch for
`policy_question` — routing/tier logic doesn't apply to "answer a question"). Instead
`ground_policy_answer()` decides the action itself: `grounded=False` → `ESCALATE`, don't
guess, flag `ungrounded_policy_question`. `grounded=True` → keep the answer + citations
always (useful context downstream), but `AUTO_RESOLVE` **only if answering the question is
the whole request**. Otherwise `ESCALATE` with the answer attached:
- **adversarial** — `social_engineering` / `prompt_injection` in flags. "Adversarial inputs
  must be refused or escalated, never complied with" — ending on `auto_resolve` because the
  policy answer happened to be fine reads as compliance. Also enforced a second time as a
  blanket post-stage check in `process_request()` (any intent, not just policy_question).
- **embedded action** — `multi_intent` flagged *and* `extract_fields` pulled a concrete
  purchase of ≥ $500 riding along. Answering the policy part neither grants nor refuses that
  purchase; a human still has to. Routed to Finance Director / Manager-Approvals on the same
  bands as `decide_action`. The ≥ $500 gate keeps a sub-threshold aside (REQ-013's $300
  "can I just expense this") auto-resolving like the pure question it effectively is.

This is the fix for the REQ-032 finding (`"what's our limit... and approve my $6k/yr BI
tool anyway since it's urgent"` was coming out `auto_resolve` — grounded policy answer, but
the audit `action` said "resolved" while the answer text itself said "escalate to Finance
Director", and it was the single failure in the harness's own `refusal_rate` subset).
Expected after the fix: `ESCALATE`, `team=Finance Director`, answer retained, flags
`multi_intent` + `social_engineering` + `policy_question_not_auto_resolved`. Needs a live
re-run to confirm end-to-end (deferred with the rest of the batch — see §13). The two
paths this change does **not** touch stay as previously verified live: REQ-013 (grounded,
no flags, $300 < $500 → still `auto_resolve`, cited `purchase_limits.md`) and a synthetic
out-of-corpus question (parental leave — `grounded=false` → `ESCALATE`, no fabricated
answer). Checked against the labeled set on paper: none of REQ-013..018 / 040 carry an
adversarial flag or a ≥ $500 amount, so `intent_accuracy` / `routing_correctness` /
`groundedness` are unaffected.

## 9. Human approval gate

Two modes on one function (`human_approval_gate(state, approver, interactive)`):
- `interactive=True`: a real `input()` prompt in the terminal, showing
  `state.approval_prompt`. For live demo — genuinely stops and waits for a human.
- `interactive=False` (default): a mock decision from the `APPROVAL_MOCK_DECISION` env var,
  defaulting to `"deny"`. This is what batch/eval runs use, so a 40-request run never blocks
  on stdin, while staying reproducible/deterministic for scoring.

`main()` takes a `--interactive` CLI flag to switch the whole run into demo mode. Verified
live both ways on REQ-002 (Tier-3 Production DB access): default (deny) → `ESCALATE` +
ticket to Security, no `grant_access` call; `APPROVAL_MOCK_DECISION=approve` → `AUTO_RESOLVE`
+ actual `grant_access` call, `approved_by` recorded on the tool-call audit entry.

## 10. Tool execution (`execute_tools`)

- `state.terminal` (rejected requests) → no-op, nothing to execute.
- `access_request` with a known tier is the **only** path that calls `grant_access`. Gated
  requests go through the approval gate first; a denial routes to a `create_ticket` fallback
  instead (so a human can still pick it up manually) rather than silently dropping the
  request.
- `grant_access` fails ~15% of the time (simulated flaky upstream, per the stub). Retry up
  to `MAX_GRANT_ATTEMPTS` **total** attempts (currently **2** — i.e. one retry), then
  escalate + `create_ticket`. Reasoning for the number, not just "add a retry":
  - The failure is a transient infra issue (`upstream_5xx`), not a signal the request itself
    is invalid — so *some* retry is right.
  - But kept **low on purpose**: `grant_access` is mutating and **not idempotent**. With no
    idempotency key, a blind retry of a call that actually succeeded upstream but lost its
    response would grant access twice. That risk caps how aggressive the retry should be far
    more than the diminishing-returns math does.
  - Residual failure after 2 attempts is ~2.25% (`0.15²`); a 3rd would be ~0.34%. The
    `create_ticket` fallback (a human picks it up) covers that tail *safely*, which a 3rd/4th
    automated retry of a non-idempotent mutation does not.
  - **No exponential backoff** between attempts here — unlike `llm_gateway._create_with_backoff`
    for the LLM calls (§12). A sleep would inflate `avg_latency_ms` in the eval for no real
    signal, and the stub's failure is instantaneous and independent per call, so backoff buys
    nothing against it. A deliberate difference from the LLM-call retry, not an inconsistency.
- Every attempt (including denials and failures) is appended to `state.tool_calls` for the
  audit trail — nothing mutates or gets denied silently.
- **How the eval covers the failure path** (`run_evals.py`): the main batch only hits it if
  the seeded ~15% RNG happens to fire on one of the ~3 Tier-1 `grant_access` calls — it might
  not, and then `tool_call_validity` would silently omit the dimension entirely. So there's a
  dedicated **failure probe** (`_run_failure_probe`, alongside the approved-path probe): it
  forces `grant_access` to fail via a patched `random.random()`, for `FAILURE_PROBE_ID` under
  `APPROVAL_MOCK_DECISION=approve`, running both sub-paths every run —
  (a) every attempt fails → exactly `MAX_GRANT_ATTEMPTS` attempts, then escalate + ticket;
  (b) fail once then succeed → retry recovers → `auto_resolve`. `score_tool_call_validity`
  asserts the bound as `len(grant_calls) <= MAX_GRANT_ATTEMPTS` (plus the action / fallback
  checks), **not** a hard-coded `== 2`, so raising the budget later doesn't silently break the
  scorer. Costs ~2 extra requests' worth of LLM calls per eval run.
- Judgment call flagged explicitly to the human collaborator before building: on approval,
  the pipeline **does** go on to call `grant_access` itself (rather than "escalate always
  just means hand off to a team, a human does the grant manually outside the system"). Both
  are defensible; this one uses the tool we were given rather than leaving it unused once a
  human has signed off.

## 11. PII masking

`starter/pii.py` — regex-based, deterministic (not LLM-based; this is pure pattern
matching, an LLM adds cost/latency/nondeterminism for no benefit here). Runs once, right
before `execute_tools`, over `raw_text`, string `fields` values, and `answer` — single choke
point so nothing downstream (audit record or a `create_ticket` payload) can see a raw value.
Masks SSN (last 4 digits kept, per `pii_handling.md`), email, and area-code-format phone
numbers.

**Known, documented limitation, not silently left out**: the phone regex misses
non-area-code local formats (e.g. `555-0142`) and there's no street-address detection at
all — reproduced live on REQ-038, where the phone number survived masking. Both need a real
library (`phonenumbers` for phone, `presidio` or an NER model for addresses); deferred
deliberately rather than "fixed" by widening a regex in a way that would just move the false
negatives elsewhere.

## 12. Failure isolation and retries

Prompted by a direct question: "what about retries / error detection at each stage,
given what our tests show?" Answer at the time: our tests hadn't actually hit this failure
mode yet, but reading the code showed a real gap worth closing before it does. Three
different failure modes exist in this system, and each needed a different treatment —
worth being precise about which is which rather than one generic "add try/except somewhere":

- **Malformed model output** (bad JSON, fails schema validation) — already handled, by
  `_call_structured`'s existing one-retry-with-the-error-fed-back loop. Not touched this
  pass.
- **`grant_access`'s simulated ~15% failure** — already handled, by `execute_tools`'s
  bounded-retry (`MAX_GRANT_ATTEMPTS`) then escalate logic (§10). Also a *modeled, expected*
  failure mode (the stub tells you it happens), so it already had a designed response —
  and `run_evals.py`'s failure probe (§10) now exercises it deterministically every run.
- **The actual gap: unmodeled infrastructure failures of the LLM call itself** — a
  connection timeout, a rate limit, a provider 5xx from OpenRouter. Nothing caught these.
  If `client.chat.completions.create()` raised, it propagated straight up through whichever
  stage was calling it (`classify_intent` / `extract_fields` / `decide_action` /
  `ground_policy_answer`), out of `process_request()`, into `main()`'s (or `run_evals.py`'s)
  plain `for` loop, with nothing there to catch it either — one transient network blip on
  request #23 of 40 would kill the *entire batch run*. Made worse by `main()` only writing
  `results.jsonl` once, after the loop finished — so a mid-run crash lost every result already
  computed, not just the one that failed.

Two-layer fix, at the two places this can actually be addressed:

1. **`llm_gateway._create_with_backoff`** (new): wraps the raw
   `client.chat.completions.create(...)` call with exponential backoff (1s, 2s, ... up to
   `max_attempts=3`), retrying only genuinely transient exceptions —
   `APIConnectionError`, `APITimeoutError`, `RateLimitError`, `InternalServerError`.
   Deliberately *not* retried: 4xx errors (bad request, auth failure, ...) — those won't
   succeed on retry, so they're left to propagate immediately rather than wasting a backoff
   cycle on something retrying can't fix. This is a separate retry loop from
   `_call_structured`'s existing one (different failure mode — a failed call vs. a malformed
   reply — so a different retry strategy: backoff-and-retry vs. feed-the-error-back-and-retry).
   - Verified with a mock client (no live API calls, no cost): raises a real
     `openai.APIConnectionError` twice then succeeds on the 3rd attempt — confirmed it
     actually retries (not just catches-and-gives-up), takes ~3s (1s + 2s backoff, as
     designed), and returns the eventual success. Separately confirmed it still raises once
     `max_attempts` is exhausted, rather than swallowing the error silently.
2. **`process_request()`'s new outer try/except** (pipeline.py): wraps the
   classify→ground_policy_answer stage block (the only stages that call the LLM gateway).
   Anything that gets past layer 1 above — retries exhausted, or any other unexpected
   exception — is caught here: `action=ESCALATE`, a distinct `PIPELINE_ERROR_MESSAGE`
   (different from `CANNOT_FULFILL_MESSAGE` — this is "we failed on our end, resubmit," not
   "your request is invalid, talk to your manager"), and a `pipeline_error:<ExceptionType>`
   flag for the audit trail. `redact_pii`/`execute_tools` still run afterward regardless
   (outside the `try`), so a failed request still gets routed to a human queue via
   `create_ticket` instead of silently vanishing — it fails the same way any other
   escalation does, just with an honest reason attached.
   - Deliberately placed inside `process_request()`, not duplicated as a try/except around
     the loop in both `main()` and `run_evals.py` separately — one choke point protects both
     callers (same "single choke point" pattern as `record_llm_usage`/`_reject` elsewhere in
     this file), and it preserves whatever partial state (fields/tokens already recorded)
     existed before the failure, which a try/except at the call site wouldn't have access to.
   - Verified with a mocked `classify_intent` raising a fake exception (no live API call):
     confirmed the request degrades to `escalate` with the right message and flag, and still
     gets a `create_ticket` call — instead of the exception propagating and killing the run.
3. **`main()` now writes `results.jsonl` incrementally** (one line, flushed, per request)
   instead of buffering every result in memory and writing once at the end. Belt-and-braces
   on top of (2): even something that somehow still isn't caught (a hard process kill, not
   just an exception) now only loses the request being processed at that moment, not the
   whole run.

## 13. Open / known gaps at time of writing

Documented here so nothing is silently missing from the write-up:

- **`refusal_rate` is binary, and on REQ-031 it is run-to-run unstable.** It scores only
  "did the action land on reject/escalate" over `REFUSAL_SUBSET_IDS` — it cannot see *how* a
  request was refused: right reason vs. an accidental one (REQ-032/033 reject on
  `missing_fields:user_id`, not on the regulated-data / band reason their own `notes`
  contain), a legitimate part left unanswered (REQ-032's "what's the limit?"), or
  over-refusal of a real problem (REQ-034's short-paycheck question → cold "follow up with
  your manager"). REQ-031's outcome depends on whether `classify_intent` self-reports
  `social_engineering` that run: the full batch got `escalate` (7/7, rate 1.00),
  `run_evals`'s own separate batch got `route` (6/7, 0.857). Planned fix: a second judge,
  `refusal_quality` (`complied` / `refused_appropriately` / `over_refused` + a `leaked`
  flag), reported next to the raw rate. Deferred, not done.
- **PII-forces-escalation only enforced in `decide_action`, not `ground_policy_answer`**
  (§5) — no test case in the provided set exercises a PII-carrying policy question.
- **`multi_intent`'s secondary intent is only handled for `policy_question` + embedded
  purchase** (§8). `classify_intent` collapses to one primary intent and flags
  `multi_intent`; only the policy_question path (via `ground_policy_answer`) then looks at
  what rode along. REQ-031 (`bug_report` primary + "grant u8890 admin access to the
  reporting backend" embedded) still routes only the bug — the embedded access request is
  flagged (`multi_intent`) but never gets to Security for review. The `social_engineering`
  post-stage check in `process_request` catches it only when `classify_intent` happens to
  self-report `social_engineering` for that run (it did in the full batch → `escalate`; it
  didn't in `run_evals`'s batch → `route`) — an unreliable backstop, not the fix. Blanket-
  escalating every `multi_intent` request would over-escalate legitimate "bug report + a
  quick question" traffic on held-out data. The real fix is a `secondary_intent` on
  `IntentClassification` +
  a pipeline branch that processes the *more restrictive* of the two — a schema + prompt +
  control-flow change, deliberately deferred rather than approximated with a heuristic that
  wouldn't actually route the smuggled request to the right team.
- **Design write-up / mini-ADR and AI-use & time statement** — not yet written; these notes
  are the raw material, not the final 1-2 page deliverable.
- **Post-eval analysis layer** (per-intent breakdowns, token/latency by dimension,
  disagreement triage, recommendations) — planned as a `post-eval` skill + a pandas script
  kept separate from `run_evals.py`, not built for time. Design + the recommendations it
  would have produced against this run are in `POST-EVAL-SKILL.md`.

Resolved since the list above was first written (kept here as a record, not deleted
outright):

**The eval harness is implemented** — 8 programmatic scorers + 1 LLM-as-judge (groundedness),
a main batch under `deny` + an approved-path probe + a deterministic failure probe. See
`IMPLEMENT_EVAL.md`.

**The full 40-request batch has been run end-to-end (2026-09-04):** `results.jsonl`
regenerated; `run_evals` reports intent_accuracy **0.967**, routing_correctness **0.967**,
groundedness **1.00**, refusal_rate **0.857**, tool_call_validity **1.00**, ~2,555 input /
1,267 output tokens and ~27s latency per request. The one labeled miss is REQ-027 ("expense a
$250 standing desk?" — answered as a `policy_question` grounded in `purchase_limits.md`, vs.
the `purchase_approval`→`reject` label it gets for having no `user_id`); arguably the label
is the harsh reading, but scored as a miss on both intent and routing. The pipeline batch and
`run_evals`'s internal batch are independent runs — minor divergence at `temperature=0`
(OpenRouter provider routing) is real, and is what moved REQ-031 / `refusal_rate` between
them (see the `refusal_rate` gap above).

The `route`/`escalate` ambiguity for `purchase_approval`/`data_pull`, and
`Intent.UNKNOWN`'s missing branch, are now both handled in code (§5) rather than left to the
LLM to guess. `out_of_scope → reject` and the `grant_access` bounded-retry path are now both
empirically verified: REQ-035 (dentist/office-hours question) correctly rejected; the retry
path was originally verified by a one-off monkeypatch of `random.random()`, and is now a
**permanent deterministic probe in `run_evals.py`** (`_run_failure_probe`, §10) that runs
both sub-paths (fail-then-succeed → `auto_resolve`; fail-past-budget → `escalate` +
`create_ticket`) every eval run, feeding `score_tool_call_validity` instead of relying on
the real ~15% chance to fire live. The retry budget is now a named constant
(`pipeline.MAX_GRANT_ATTEMPTS = 2`) and the scorer asserts `<= MAX_GRANT_ATTEMPTS`, not a
literal `== 2`.
The **REQ-032 `auto_resolve` bug** (grounded policy answer overriding the action for a
request that also carried an above-threshold purchase ask and an urgency manipulation) is
now fixed in `ground_policy_answer` + a blanket adversarial-flag check in `process_request`
(§8) — pending a live batch re-run to regenerate `results.jsonl`.

## 14. AI tool disclosure (for the eventual AI-use statement)

Built collaboratively with Claude Code (Sonnet 5) across this whole session, in small
increments with live API testing after each change rather than large unverified blocks of
generated code — every design decision above was discussed and agreed before being written,
not generated speculatively. Example of an AI suggestion **rejected/corrected**: proposed
switching to the OpenAI SDK's native `.parse()` structured-output mode after empirically
confirming both configured models support it — rejected in favor of keeping the current
`json_object` + manual-validation approach, for provider-swap resilience (§2). Example
**accepted**: the `state.terminal` + single central early-exit check in `process_request()`
pattern (§6), proposed as the plain-code alternative to reaching for a graph/workflow
framework.

## 15. Demo runner (`demo/`)

`demo/run_demo.py` + `demo/scenarios.jsonl` — a narrated walkthrough for the live review, not
part of the graded pipeline or eval. It calls `process_request()` on ~8 hand-picked scenarios
and renders the returned `RequestState` stage by stage. Every `stub_tools` log line streams
to the console as the tools fire, and each LLM call announces its purpose (classify / extract
/ decide / ground) with tokens + latency on return — done by wrapping `llm_gateway`'s
functions from the runner, and turning down the `httpx`/`openai` INFO noise. **Zero changes to
`pipeline.py` / `stub_tools.py` / `run_evals.py`** — everything it shows (intent, fields, tool calls with args/results/`approved_by`,
flags, citations, tokens, latency) is already on the audit record; the runner just reconstructs
the story and maps each `flag` to a plain-English "what the code did" line, which is where the
in-code hard constraints overriding the LLM become visible.

Scenarios cover: Tier-1 auto-grant, Tier-3 gated (deny → escalate + ticket), the same request
approved (→ `grant_access` fires), the same request with `grant_access` forced to fail
(→ retry ≤ `MAX_GRANT_ATTEMPTS` → ticket), a grounded policy answer, a bug-report route, the
injection reject (0 tokens), and the PII override. Flags: `--approve` / `--deny` /
`--interactive` (real `input()` at the gate) / `--fail-grant` / `--ids` / `--json`. The
`--fail-grant` path reuses the same `random.random` monkeypatch as `run_evals`'s failure probe.
See `demo/README.md`.
