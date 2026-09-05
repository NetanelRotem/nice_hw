#!/usr/bin/env python3
"""Triage & Resolve -- narrated demo runner.

Runs a curated set of scenarios through the REAL pipeline (starter/pipeline.py)
and prints a stage-by-stage account of each one, with every tool call logged to
the console as it happens. Nothing here re-implements the pipeline -- it calls
process_request() and renders the RequestState that comes back.

    python demo/run_demo.py                 # every scenario, gate decision per scenario
    python demo/run_demo.py --approve       # force the approval gate to APPROVE everywhere
    python demo/run_demo.py --deny          # force the approval gate to DENY everywhere
    python demo/run_demo.py --interactive   # real y/N prompt at the gate
    python demo/run_demo.py --fail-grant    # force grant_access to fail (show retry -> ticket)
    python demo/run_demo.py --ids acc-tier1,injection
    python demo/run_demo.py --json          # also dump the full audit record per scenario

Each scenario makes real LLM calls (~30-60s on the configured models); a full
run is a few minutes. Use --ids to run a subset. The `injection` scenario spends
zero tokens by design.
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import random
import sys
import textwrap
from unittest.mock import patch

_HERE = os.path.dirname(os.path.abspath(__file__))
_PKG_ROOT = os.path.dirname(_HERE)  # candidate-package/
if _PKG_ROOT not in sys.path:
    sys.path.insert(0, _PKG_ROOT)

from starter import injection_check  # noqa: E402
from starter.pipeline import (  # noqa: E402
    MAX_GRANT_ATTEMPTS,
    load_knowledge,
    process_request,
)

SCENARIOS_PATH = os.path.join(_HERE, "scenarios.jsonl")
W = 74  # console rule width


# --------------------------------------------------------------------------- #
# Logging -- make every stub-tool call visible, cleanly formatted
# --------------------------------------------------------------------------- #
class _DemoFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        msg = record.getMessage()
        if record.name == "stub_tools":
            return f"      | tool  {msg}"
        return f"      | {record.name}  {msg}"


def setup_logging() -> None:
    # stub_tools attaches its own handler at import time; strip it (and any
    # others) so this runner owns the console formatting.
    for name in ("stub_tools", "llm_gateway"):
        lg = logging.getLogger(name)
        lg.handlers.clear()
        lg.propagate = True
        lg.setLevel(logging.INFO)
    # `pipeline` logs an input/output token line per LLM call; in the demo the
    # instrument_gateway() wrapper already shows that (with the call's purpose),
    # so keep this logger quiet here to avoid printing it twice.
    logging.getLogger("pipeline").setLevel(logging.WARNING)
    # The HTTP stack under the OpenAI SDK logs every request at INFO
    # ("HTTP Request: POST .../chat/completions 200 OK") -- noise for a demo.
    # instrument_gateway() below narrates each LLM call with its actual purpose
    # instead.
    for noisy in ("httpx", "httpcore", "openai", "urllib3"):
        logging.getLogger(noisy).setLevel(logging.WARNING)
    root = logging.getLogger()
    root.handlers.clear()
    root.setLevel(logging.INFO)
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(_DemoFormatter())
    root.addHandler(handler)


# LLM call site -> why the pipeline is making it. Wrapped onto llm_gateway so
# each call announces itself; no change to the gateway or the pipeline.
_LLM_PURPOSE = {
    "classify_intent": "classify the request into an intent",
    "extract_fields": "extract the fields needed to act",
    "decide_action": "decide the action + tier, grounded in the policy docs",
    "ground_policy_answer": "answer the policy question from the docs, with citations",
}


def instrument_gateway() -> None:
    from starter import llm_gateway

    for name, why in _LLM_PURPOSE.items():
        original = getattr(llm_gateway, name)

        def wrap(original, name, why):
            def wrapped(*args, **kwargs):
                print(f"      | llm   {name:20} {why}")
                call = original(*args, **kwargs)
                tin = getattr(call, "prompt_tokens", 0)
                tout = getattr(call, "completion_tokens", 0)
                secs = getattr(call, "latency_ms", 0.0) / 1000
                print(f"      | llm   {name:20} -> input {tin:,} / output {tout:,} tokens, {secs:.1f}s")
                return call

            return wrapped

        setattr(llm_gateway, name, wrap(original, name, why))


# --------------------------------------------------------------------------- #
# Flag -> plain-English explanation (this is how the audit trail records what
# the code did on top of / instead of the LLM's suggestion)
# --------------------------------------------------------------------------- #
FLAG_MEANING = {
    "approval_denied": "approval gate returned DENY -> grant_access blocked, routed to a team instead",
    "downgraded_auto_resolve_above_tier1": "tier >= 2 -> code overrode the LLM's auto_resolve to escalate",
    "pii": "PII found in the request -> masked before the audit record or any ticket payload",
    "pii_forces_escalation": "PII override -> forced ESCALATE + Data Governance, regardless of tier",
    "grant_access_failed": "grant_access still failing after every retry -> escalated with a ticket",
    "unknown_intent_escalated": "intent could not be classified -> forced ESCALATE (nothing to ground a decision on)",
    "ungrounded_policy_question": "policy docs don't cover it -> escalated, no guessed answer",
    "adversarial_flag_forces_escalation": "adversarial flag on an auto_resolve/route result -> forced ESCALATE",
    "policy_question_not_auto_resolved": "policy answer kept, but an embedded action / manipulation blocked auto_resolve",
    "requires_data_governance_and_security": "data category 3 -> both Data Governance and Security required",
    "requires_procurement_legal_review": "purchase > $25k -> procurement + legal review flagged",
    "multi_intent": "more than one ask in the request -- secondary intent flagged for a human",
}


def flag_meaning(flag: str) -> str | None:
    if flag.startswith("prompt_injection_high_risk"):
        return "deterministic injection pre-check tripped -> REJECT before any LLM call (0 tokens)"
    if flag.startswith("missing_fields:"):
        return f"required field(s) absent ({flag.split(':', 1)[1]}) -> cannot act, REJECT"
    if flag.startswith("invalid_"):
        return f"model returned something outside the schema ({flag}) -> did not guess, escalated"
    return FLAG_MEANING.get(flag)


# --------------------------------------------------------------------------- #
# Running one scenario
# --------------------------------------------------------------------------- #
def resolve_approval(args: argparse.Namespace, scn: dict) -> str | None:
    """The gate decision for this scenario. None => interactive (real prompt)."""
    if args.interactive:
        return None
    if args.approve:
        return "approve"
    if args.deny:
        return "deny"
    return scn.get("approval", "deny")


def run_one(scn: dict, knowledge: dict, args: argparse.Namespace):
    raw = {
        "id": scn["id"],
        "raw_text": scn["raw_text"],
        "label_status": scn.get("label_status", "unlabeled"),
    }
    random.seed(args.seed)  # reproducible ~15% grant_access failure when not forced
    decision = resolve_approval(args, scn)
    force_fail = args.fail_grant or bool(scn.get("fail_grant", False))

    prev = os.environ.get("APPROVAL_MOCK_DECISION")
    if decision is not None:
        os.environ["APPROVAL_MOCK_DECISION"] = decision
    try:
        if force_fail:
            # random.random() -> 0.0 is always below FAILURE_RATE, so every
            # grant_access attempt fails -- same trick as run_evals' failure probe.
            with patch("tools.stub_tools.random.random", return_value=0.0):
                state = process_request(raw, knowledge, interactive=args.interactive)
        else:
            state = process_request(raw, knowledge, interactive=args.interactive)
    finally:
        if prev is None:
            os.environ.pop("APPROVAL_MOCK_DECISION", None)
        else:
            os.environ["APPROVAL_MOCK_DECISION"] = prev

    return state, decision, force_fail


# --------------------------------------------------------------------------- #
# Rendering
# --------------------------------------------------------------------------- #
def _wrapped(label: str, text: str, width: int = 62) -> None:
    lines = textwrap.wrap(text, width) or [""]
    for i, ln in enumerate(lines):
        print(f"  {(label if i == 0 else ''):<9} {ln}")


def _bullet(text: str, width: int = 62) -> None:
    lines = textwrap.wrap(text, width) or [""]
    print(f"    - {lines[0]}")
    for ln in lines[1:]:
        print(f"      {ln}")


def print_header(scn: dict, idx: int, total: int) -> None:
    print("\n" + "=" * W)
    print(f"  [{idx}/{total}]  {scn['id']}")
    print(f"  {scn['note']}")
    print("  " + "-" * (W - 2))
    print(f"  input      {textwrap.shorten(scn['raw_text'], 200)}")
    inj = injection_check.score(scn["raw_text"])
    verdict = "REJECT -- above 0.75, no LLM call" if inj >= 0.75 else "clear (threshold 0.75)"
    print(f"  injection  {inj:.2f}   {verdict}")
    print("  running the pipeline ...")


def render(scn: dict, state, decision, forced_fail: bool, show_json: bool) -> None:
    print("  " + "." * (W - 2))

    fields = {k: v for k, v in state.fields.items() if k != "notes"}
    print(f"  intent     {state.intent.value}   confidence {state.confidence:.2f}")
    for k, v in fields.items():
        print(f"  field      {k} = {v!r}")
    if state.fields.get("notes"):
        _wrapped("rationale", str(state.fields["notes"]), 62)
    if "pii" in state.flags:
        print(f"  redacted   {textwrap.shorten(state.raw_text, 200)}")

    if state.requires_approval:
        src = "interactive prompt" if decision is None else f"APPROVAL_MOCK_DECISION={decision}"
        outcome = "DENIED" if "approval_denied" in state.flags else "APPROVED"
        print(f"  gate       required -> {outcome}   ({src})")
        if state.approval_prompt:
            _wrapped("prompt", state.approval_prompt, 58)

    if state.tool_calls:
        print("  tools")
        grant_n = 0
        for tc in state.tool_calls:
            r = tc.result if isinstance(tc.result, dict) else {}
            if tc.tool == "grant_access":
                grant_n += 1
                status = "OK" if r.get("ok") else f"FAILED ({r.get('error')})"
                extra = f"   approved_by={tc.approved_by}" if tc.approved_by else ""
                print(f"    grant_access   attempt {grant_n}/{MAX_GRANT_ATTEMPTS}   -> {status}{extra}")
            elif tc.tool == "lookup_user":
                found = "found" if r.get("found") else "not found"
                print(f"    lookup_user    {tc.args.get('user_id', '?')!r:16} -> {found}")
            elif tc.tool == "create_ticket":
                data = r.get("data") if isinstance(r.get("data"), dict) else {}
                tid = data.get("ticket_id", "?")
                print(f"    create_ticket  team={tc.args.get('team', '?')!r:14} -> {tid}")
    else:
        print("  tools      (none called)")

    explanations = [e for e in (flag_meaning(f) for f in state.flags) if e]
    if explanations:
        print("  what the code did")
        for e in explanations:
            _bullet(e)

    print("  " + "-" * (W - 2))
    print(f"  ACTION     {state.action.value.upper()}")
    if fields.get("team"):
        print(f"  team       {fields['team']}")
    if state.answer:
        _wrapped("answer", state.answer, 62)
    if state.citations:
        print(f"  citations  {', '.join(sorted(set(state.citations)))}")
    if state.flags:
        print(f"  flags      {', '.join(state.flags)}")
    print(
        f"  tokens     input {state.input_tokens:,}  /  output {state.output_tokens:,}  "
        f"/  total {state.tokens:,}"
    )
    print(f"  latency    {state.latency_ms / 1000:.1f} s")

    if show_json:
        print("  " + "." * (W - 2))
        print(textwrap.indent(json.dumps(state.to_audit_record(), indent=2, ensure_ascii=False), "  "))


def print_summary(rows: list[tuple]) -> None:
    print("\n" + "=" * W)
    print("  SUMMARY")
    print("  " + "-" * (W - 2))
    print(f"  {'scenario':20} {'action':13} {'in':>7} {'out':>7} {'total':>8}   {'time':>6}")
    tot_in = tot_out = 0.0
    for scn_id, action, tin, tout, secs in rows:
        tot_in += tin
        tot_out += tout
        print(f"  {scn_id:20} {action:13} {tin:>7,} {tout:>7,} {tin + tout:>8,}   {secs:>5.1f}s")
    print("  " + "-" * (W - 2))
    print(f"  {'TOTAL':20} {'':13} {tot_in:>7,.0f} {tot_out:>7,.0f} {tot_in + tot_out:>8,.0f}")
    print("=" * W)


# --------------------------------------------------------------------------- #
# Entry point
# --------------------------------------------------------------------------- #
def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description="Narrated demo of the Triage & Resolve pipeline.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    ap.add_argument("--approve", action="store_true", help="force the approval gate to APPROVE for every scenario")
    ap.add_argument("--deny", action="store_true", help="force the approval gate to DENY for every scenario")
    ap.add_argument("--interactive", action="store_true", help="real y/N prompt at the approval gate")
    ap.add_argument("--fail-grant", dest="fail_grant", action="store_true",
                    help="force grant_access to fail, to show the retry -> ticket fallback")
    ap.add_argument("--ids", default=None, help="comma-separated scenario ids to run (default: all)")
    ap.add_argument("--json", dest="show_json", action="store_true", help="also print the full audit record per scenario")
    ap.add_argument("--seed", type=int, default=42, help="RNG seed for the simulated grant_access failure (default 42)")
    args = ap.parse_args(argv)

    if args.approve and args.deny:
        print("error: pass at most one of --approve / --deny", file=sys.stderr)
        return 2

    setup_logging()
    instrument_gateway()

    with open(SCENARIOS_PATH, encoding="utf-8") as fh:
        scenarios = [json.loads(line) for line in fh if line.strip()]
    if args.ids:
        want = {s.strip() for s in args.ids.split(",")}
        scenarios = [s for s in scenarios if s["id"] in want]
        if not scenarios:
            print(f"error: no scenarios matched --ids {args.ids!r}", file=sys.stderr)
            return 1

    knowledge = load_knowledge()

    print(f"\n  Triage & Resolve -- demo  ({len(scenarios)} scenario(s), seed {args.seed})")
    if not args.interactive:
        gate = "APPROVE" if args.approve else "DENY" if args.deny else "per scenario"
        print(f"  approval gate: {gate}   |   real LLM calls, ~30-60s each")

    rows = []
    for i, scn in enumerate(scenarios, 1):
        print_header(scn, i, len(scenarios))
        state, decision, forced_fail = run_one(scn, knowledge, args)
        render(scn, state, decision, forced_fail, args.show_json)
        rows.append((
            scn["id"], state.action.value,
            state.input_tokens, state.output_tokens, state.latency_ms / 1000,
        ))

    print_summary(rows)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
