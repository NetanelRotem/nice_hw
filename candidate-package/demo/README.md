# Demo — narrated pipeline walkthrough

A small runner that pushes a curated set of requests through the **real**
pipeline (`starter/pipeline.py`) and narrates what happened at each stage, with
**every tool call logged to the console as it fires**. Use it to show the system
working end to end — the approval gate, tool failures, PII handling, refusals.

It does not re-implement anything: it calls `process_request()` and renders the
`RequestState` that comes back, plus the `stub_tools` log lines.

---

## Setup

From `candidate-package/`:

```bash
pip install -r requirements.txt
cp .env.example .env          # then put a real OPENROUTER_API_KEY in .env
```

`.env` is loaded automatically (via `python-dotenv`). The models used are the two
`INTENT_MODEL` / `JUDGE_MODEL` slugs in `.env` — the demo only uses `INTENT_MODEL`.

---

## Run it

One command, just Python — nothing else to install. Run from `candidate-package/`:

```bash
python demo/run_demo.py
```

(or `python3`, or `py -3` on Windows — whatever runs the rest of the project).

| Command | What it does |
|---|---|
| `python demo/run_demo.py` | all scenarios; the approval gate follows each scenario's own setting |
| `python demo/run_demo.py --approve` | force the gate to **APPROVE** everywhere |
| `python demo/run_demo.py --deny` | force the gate to **DENY** everywhere |
| `python demo/run_demo.py --fail-grant` | force `grant_access` to fail — shows the retry then ticket fallback |
| `python demo/run_demo.py --interactive` | real `y/N` prompt at the gate (stops and waits for you) |
| `python demo/run_demo.py --json` | also print the full audit record (`results.jsonl` shape) per scenario |
| `python demo/run_demo.py --ids acc-tier1,injection` | run only those scenario ids |
| `python demo/run_demo.py --seed N` | RNG seed for the simulated `grant_access` failure (default 42) |

Flags combine, e.g. `--fail-grant --ids acc-tier3-grantfail` to demo just the retry path.

**Timing / cost:** each scenario makes real LLM calls and takes roughly 30–60s on
the configured flash models, so a full run is a few minutes. Use `--ids` for a
subset. The `injection` scenario spends **0 tokens** by design (rejected before
any model call) — `python demo/run_demo.py --ids injection,acc-tier1` is the quick one.

---

## The scenarios

| id | Shows |
|---|---|
| `acc-tier1` | Tier-1 resource → `auto_resolve`; `grant_access` called and succeeds, no gate |
| `acc-tier3-deny` | Tier-3 resource, no human → gate **blocks** `grant_access` → `escalate` + ticket to Security |
| `acc-tier3-approve` | Same request, human **approves** → `grant_access` fires, `approved_by` recorded |
| `acc-tier3-grantfail` | Approved, but `grant_access` hits the simulated failure → retry ≤ `MAX_GRANT_ATTEMPTS` → ticket fallback |
| `policy-grounded` | Policy question answered from `purchase_limits.md` with a citation; nothing mutates |
| `bug-report` | Bug report → `route` to Engineering; one `create_ticket` |
| `injection` | Prompt-injection payload → deterministic `REJECT`, **0 tokens** |
| `pii-override` | Tier-1 resource but SSN/email/phone present → PII override forces `escalate` + Data Governance |

`acc-tier3-deny`, `-approve` and `-grantfail` use the **same request text** on
purpose — the only thing that changes is the gate decision and the tool outcome.

Edit `scenarios.jsonl` to add your own. Each line: `id`, `note` (printed as the
header), `raw_text`, optional `approval` (`"approve"` / `"deny"`, default deny)
and `fail_grant` (`true` to force the `grant_access` failure for that scenario).

---

## Reading the output

For each scenario you get:

```
==========================================================================
  [2/8]  acc-tier3-deny
  Tier-3 resource (Production Database), no human present -> the gate blocks ...
  --------------------------------------------------------------------------
  input      Requesting access to the Production Database. user id u2087. ...
  injection  0.00   clear (threshold 0.75)
  running the pipeline ...
      | llm   classify_intent      classify the request into an intent
      | llm   classify_intent      -> input 812 / output 392 tokens, 3.1s
      | llm   extract_fields       extract the fields needed to act
      | llm   extract_fields       -> input 1,138 / output 392 tokens, 4.0s
      | tool  lookup_user user_id=u2087 found=true role='SRE' ...
      | llm   decide_action        decide the action + tier, grounded in the policy docs
      | llm   decide_action        -> input 1,566 / output 408 tokens, 6.2s
      | tool  create_ticket ticket_id=TCK-1A2B3C4D team='Security' ...
  ..........................................................................
  intent     access_request   confidence 0.91
  field      user_id = 'u2087'
  field      resource = 'Production Database'
  rationale  Production Database is a Tier 3 (Restricted) resource ...
  gate       required -> DENIED   (APPROVAL_MOCK_DECISION=deny)
    prompt   Approve granting u2087 access to Production Database (tier 3)?
  tools
    lookup_user    'u2087'          -> found
    create_ticket  team='Security'  -> TCK-1A2B3C4D
  what the code did
    - approval gate returned DENY -> grant_access blocked, routed to a team instead
  --------------------------------------------------------------------------
  ACTION     ESCALATE
  team       Security
  citations  access_tiers.md
  flags      approval_denied
  tokens     input 3,516  /  output 1,192  /  total 4,708
  latency    49.1 s
```

Input and output tokens are always shown separately (they price differently) —
per LLM call, in the per-scenario verdict, and summed in the final table.

The `| llm` lines announce each model call and its purpose (with tokens + time
on return); the `| tool` lines are the raw `stub_tools` logs. Both stream while
the pipeline runs. The HTTP-client noise (`httpx: HTTP Request: POST ...`) is
turned down so these stay readable. Everything under the dotted line is
reconstructed from the returned `RequestState` — the same object that becomes
one line in `results.jsonl`.

`what the code did` translates the audit `flags` into plain English: this is
where you can see the pipeline's **hard constraints in code** overriding or
gating what the LLM proposed.
