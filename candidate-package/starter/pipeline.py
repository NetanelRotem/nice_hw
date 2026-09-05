"""Triage & Resolve — pipeline SKELETON.

This is a SCAFFOLD, not a solution. It imports cleanly and runs end-to-end,
producing placeholder audit records, so you have a working starting point
instead of a blank page. YOUR JOB is to implement the stages marked ``TODO``.

Design intent (you may change the structure — justify it in your write-up):

    load requests
        -> classify intent            (LLM where language understanding is needed)
        -> extract fields             (who / resource / tier / amount / ...)
        -> decide action              (auto_resolve | route | escalate | reject)
        -> ground policy answers      (cite knowledge/*.md; don't guess -> escalate)
        -> call tools w/ approval gate (mutating calls above threshold MUST gate)
        -> emit an audit record        (machine-readable, one per request)

Nothing here calls an LLM. Deterministic routing vs. LLM judgment is a design
decision we want to see you make.

Run:  python3 -m starter.pipeline            (from the candidate-package/ dir)
  or: python3 starter/pipeline.py
"""
from __future__ import annotations

import json
import logging
import os
import sys
import time
from dataclasses import dataclass, field, asdict
from enum import Enum
from typing import Any, Dict, List, Optional

# Silent by default (no handler, inherits root's WARNING) so run_evals / a
# plain batch run stay quiet; a caller that wants the play-by-play -- the demo
# runner -- raises this logger to INFO. Every LLM call's input/output token
# split is emitted here via record_llm_usage().
logger = logging.getLogger("pipeline")

# Make the sibling `tools` package importable whether run as a module or a script.
_HERE = os.path.dirname(os.path.abspath(__file__))
_PKG_ROOT = os.path.dirname(_HERE)  # candidate-package/
if _PKG_ROOT not in sys.path:
    sys.path.insert(0, _PKG_ROOT)

from tools import create_ticket, grant_access, lookup_user  # noqa: E402
from starter import injection_check, llm_gateway, pii  # noqa: E402

# --------------------------------------------------------------------------- #
# Paths
# --------------------------------------------------------------------------- #
REQUESTS_PATH = os.path.join(_PKG_ROOT, "requests.jsonl")
KNOWLEDGE_DIR = os.path.join(_PKG_ROOT, "knowledge")
DEFAULT_RESULTS_PATH = os.path.join(_PKG_ROOT, "results.jsonl")


# --------------------------------------------------------------------------- #
# Enums / schema. Typed state passed between stages. Extend as needed.
# --------------------------------------------------------------------------- #
class Intent(str, Enum):
    ACCESS_REQUEST = "access_request"
    DATA_PULL = "data_pull"
    POLICY_QUESTION = "policy_question"
    BUG_REPORT = "bug_report"
    PURCHASE_APPROVAL = "purchase_approval"
    OUT_OF_SCOPE = "out_of_scope"
    UNKNOWN = "unknown"


class Action(str, Enum):
    AUTO_RESOLVE = "auto_resolve"
    ROUTE = "route"
    ESCALATE = "escalate"
    REJECT = "reject"
    UNDECIDED = "undecided"


# Centralized so team names can't typo/drift across decide_action branches --
# they're scattered across all 4 knowledge/*.md docs, no single doc lists them.
class Team(str, Enum):
    SECURITY = "Security"
    DATA_ANALYTICS = "Data-Analytics"
    DATA_GOVERNANCE = "Data Governance"
    LEGAL = "Legal"
    MANAGER_APPROVALS = "Manager-Approvals"
    FINANCE_DIRECTOR = "Finance Director"
    FINANCE = "Finance"
    PROCUREMENT = "Procurement"
    ENGINEERING = "Engineering"  # bug_report: no policy doc defines this, only stub_tools.py's demo call


# The LLM's job is only to CLASSIFY a resource into a tier (access_tiers.md).
# What that tier means -- gate required? force-escalate? -- is decide_action()'s
# job below, enforced in code. If access_tiers.md ever grows a Tier 4, extend
# this enum; don't add another magic number to decide_action().
class Tier(int, Enum):
    INTERNAL = 1
    SENSITIVE = 2
    RESTRICTED = 3


# What extract_fields() must have found before decide_action() is even worth
# calling -- each intent acts on different fields, so this is per-intent, not
# one global list. Missing here means "can't act", not "field unimportant";
# short-circuits to REJECT in validate_fields() instead of guessing.
#
# user_id is required for every intent that acts on a specific person's
# request, including data_pull/purchase_approval -- not just access_request.
# Without it we cannot call lookup_user to verify who's actually asking (the
# access_tiers.md "verify identity, a mismatch is a red flag" rule isn't
# access_request-specific in spirit), so we cannot act on the request at all,
# regardless of how clear-cut the amount/resource itself is. Confirmed live:
# none of the 12 purchase_approval/data_pull sample requests state a user_id
# in the text, so this rejects all of them as "cannot fulfill" -- that's the
# intended behavior here, not a bug: we're not willing to route/escalate a
# request on someone's behalf without knowing who they are, even if the
# routing decision itself doesn't otherwise depend on identity.
REQUIRED_FIELDS: Dict[Intent, List[str]] = {
    Intent.ACCESS_REQUEST: ["user_id", "resource"],
    Intent.DATA_PULL: ["user_id", "resource"],
    Intent.PURCHASE_APPROVAL: ["user_id", "amount_usd"],
}


# Which knowledge/*.md doc(s) are actually relevant to each intent -- sending
# all 4 to every call is extra tokens and a chance the model grounds itself in
# the wrong doc. policy_question is deliberately absent: the question could be
# about any of them, so it keeps the full corpus (see _relevant_knowledge).
INTENT_KNOWLEDGE: Dict[Intent, List[str]] = {
    Intent.ACCESS_REQUEST: ["access_tiers.md"],
    Intent.DATA_PULL: ["data_request_rules.md"],
    Intent.PURCHASE_APPROVAL: ["purchase_limits.md"],
    Intent.BUG_REPORT: [],
    Intent.OUT_OF_SCOPE: [],
    Intent.UNKNOWN: [],
}


def _relevant_knowledge(intent: Intent, knowledge: Dict[str, str]) -> Dict[str, str]:
    """Filter the full knowledge corpus down to what ``intent`` needs, per
    INTENT_KNOWLEDGE. Intents not listed there (currently just policy_question)
    get everything -- no fixed doc applies, don't guess by narrowing it.
    """
    names = INTENT_KNOWLEDGE.get(intent)
    if names is None:
        return knowledge
    return {k: v for k, v in knowledge.items() if k in names}


@dataclass
class ToolCall:
    """Record of a single tool invocation for the audit trail."""
    tool: str
    args: Dict[str, Any]
    result: Dict[str, Any] = field(default_factory=dict)
    approved_by: Optional[str] = None  # who cleared the approval gate, if applicable


@dataclass
class RequestState:
    """State object threaded through every pipeline stage.

    This is the audit record. Keep it machine-readable and complete: it is how
    the eval harness scores you and how a human reconstructs what happened.
    """
    id: str
    raw_text: str
    label_status: str = "unlabeled"

    intent: Intent = Intent.UNKNOWN
    fields: Dict[str, Any] = field(default_factory=dict)
    action: Action = Action.UNDECIDED

    # Grounding: the answer to a policy question + the doc(s) it is grounded in.
    answer: Optional[str] = None
    citations: List[str] = field(default_factory=list)

    tool_calls: List[ToolCall] = field(default_factory=list)
    requires_approval: bool = False
    approval_prompt: Optional[str] = None  # what a human would be asked to confirm

    confidence: float = 0.0
    flags: List[str] = field(default_factory=list)  # e.g. "prompt_injection", "pii"

    # Set by a stage that decides the request is unfulfillable (missing
    # fields, high-risk injection, ...) and nothing downstream should run.
    # process_request() checks this centrally between stages -- a single
    # early-exit point instead of every stage re-checking every other stage's
    # outcome.
    terminal: bool = False

    # Observability. input_tokens / output_tokens are tracked separately (they
    # audit differently) -- record_llm_usage() is the one place that increments
    # them. No cost_usd: pricing varies per OpenRouter-routed model and we
    # deliberately only track raw token counts, not derived cost.
    input_tokens: int = 0
    output_tokens: int = 0
    tokens: int = 0
    latency_ms: float = 0.0

    def to_audit_record(self) -> Dict[str, Any]:
        """Serialize to a JSON-friendly dict for results.jsonl."""
        d = asdict(self)
        d["intent"] = self.intent.value
        d["action"] = self.action.value
        d["tool_calls"] = [asdict(tc) for tc in self.tool_calls]
        return d


# --------------------------------------------------------------------------- #
# Knowledge loading (provided helper — feel free to replace with real retrieval)
# --------------------------------------------------------------------------- #
def load_knowledge() -> Dict[str, str]:
    """Load knowledge/*.md into {filename: text}. Trivial for a 4-doc corpus."""
    docs: Dict[str, str] = {}
    if os.path.isdir(KNOWLEDGE_DIR):
        for name in sorted(os.listdir(KNOWLEDGE_DIR)):
            if name.endswith(".md"):
                with open(os.path.join(KNOWLEDGE_DIR, name), "r", encoding="utf-8") as fh:
                    docs[name] = fh.read()
    return docs


# --------------------------------------------------------------------------- #
# Observability helpers
# --------------------------------------------------------------------------- #
def record_llm_usage(state: RequestState, call: llm_gateway.StructuredCall) -> None:
    """Fold one LLM call's token usage into ``state``.

    The single choke point for token accounting: every stage that calls the
    gateway passes its ``StructuredCall`` here instead of touching the counters
    directly. Prompt (input) and completion (output) tokens are kept apart --
    they price differently and the audit record reports each -- and ``tokens``
    stays their running sum.
    """
    state.input_tokens += call.prompt_tokens
    state.output_tokens += call.completion_tokens
    state.tokens = state.input_tokens + state.output_tokens
    logger.info(
        "llm_usage input=%d output=%d  (request so far: input=%d output=%d total=%d)",
        call.prompt_tokens, call.completion_tokens,
        state.input_tokens, state.output_tokens, state.tokens,
    )


# Below this, the request is unfulfillable by us -- reject, don't guess or route.
CANNOT_FULFILL_MESSAGE = "We're unable to process this request as submitted. Please follow up with your manager."
INJECTION_REJECT_THRESHOLD = 0.75

# Distinct from CANNOT_FULFILL_MESSAGE: that one means "your request is
# invalid/incomplete/out of scope"; this one means "we failed on our end" --
# different fault, different requester-facing guidance (retry, don't go to
# your manager).
PIPELINE_ERROR_MESSAGE = "We hit an internal error processing this request. Please resubmit, or contact support if it persists."

# grant_access retry budget -- TOTAL attempts, not retries-after-the-first.
# Kept deliberately low: grant_access is mutating and NOT idempotent. With no
# idempotency key, blindly retrying a call that actually succeeded upstream but
# lost its response would grant access twice -- that risk caps how aggressive
# the retry should be far more than the diminishing failure math does. 2
# attempts leaves ~2.25% residual failure (0.15**2); the create_ticket fallback
# in execute_tools() covers that tail safely, which a 3rd/4th automated retry of
# a non-idempotent mutation would not. See NETANEL_NOTES.md §10.
MAX_GRANT_ATTEMPTS = 2


def _reject(state: RequestState, flag: str) -> None:
    """Mark ``state`` terminal + REJECT with the standard requester-facing
    message. Shared by every short-circuit path (missing fields, high-risk
    injection, ...) so the outcome and the message they see stay consistent.
    """
    state.action = Action.REJECT
    state.answer = CANNOT_FULFILL_MESSAGE
    state.flags.append(flag)
    state.terminal = True


# --------------------------------------------------------------------------- #
# Pipeline stages — IMPLEMENT THESE
# --------------------------------------------------------------------------- #
def classify_intent(state: RequestState) -> RequestState:
    """Classify ``state.raw_text`` into an ``Intent`` via the LLM gateway."""
    risk = injection_check.score(state.raw_text)
    if risk >= INJECTION_REJECT_THRESHOLD:
        # High-confidence injection payload -- don't spend a call sending
        # untrusted text to the model at all, reject deterministically.
        _reject(state, flag=f"prompt_injection_high_risk:{risk:.2f}")
        return state

    call = llm_gateway.classify_intent(state.raw_text)
    result = call.parsed  # IntentClassification
    try:
        state.intent = Intent(result.intent)
    except ValueError:
        # Model returned a string outside our enum -- don't guess, mark unknown.
        state.intent = Intent.UNKNOWN
        state.flags.append(f"invalid_intent_from_llm:{result.intent}")

    state.confidence = result.confidence
    state.flags.extend(result.flags)
    record_llm_usage(state, call)
    return state


def extract_fields(state: RequestState, knowledge: Dict[str, str]) -> RequestState:
    """Extract the fields needed to act, conditioned on the intent classify_intent set.

    Grounded in knowledge too (see prompts/extract_fields.py) -- same reason
    as decide_action: real requests misspell/rephrase resource and category
    names, and matching against the docs while extracting beats extracting
    blind.
    """
    call = llm_gateway.extract_fields(state.raw_text, state.intent.value, _relevant_knowledge(state.intent, knowledge))
    result = call.parsed  # ExtractedFields
    # Drop nulls: don't clutter the audit record with fields the model didn't find.
    state.fields = {k: v for k, v in result.model_dump().items() if v is not None}
    record_llm_usage(state, call)
    return state


def validate_fields(state: RequestState) -> RequestState:
    """Check the fields extract_fields() found against REQUIRED_FIELDS for this
    intent. Missing required info means we can't act -- reject and tell the
    requester what's missing instead of asking the LLM to guess or proceed
    with a hole in the request. process_request() stops here (state.terminal)
    so decide_action() never runs on an incomplete request.
    """
    missing = [f for f in REQUIRED_FIELDS.get(state.intent, []) if not state.fields.get(f)]
    if missing:
        _reject(state, flag=f"missing_fields:{','.join(missing)}")
    return state


def decide_action(state: RequestState, knowledge: Dict[str, str]) -> RequestState:
    """Decide auto_resolve | route | escalate | reject, grounded in policy docs.

    lookup_user is a deterministic tool call (identity verification, not
    judgment). Tier/category matching against the docs goes to the LLM --
    see prompts/decide_action.py for why. requires_approval, the tier/category
    consequences, the purchase_approval bands, and the PII-forces-Data-
    Governance rule are all enforced here in code, never taken from the model:
    those are hard constraints in access_tiers.md / purchase_limits.md /
    data_request_rules.md / pii_handling.md, not judgment calls.

    KNOWN GAP: the PII rule above is only enforced here, not in
    ground_policy_answer() -- a policy_question carrying PII wouldn't get the
    same forced escalation, since that path never reaches this function.
    None of the provided requests exercise that combination, so it's left
    unhandled rather than guessed at. (The adversarial / embedded-purchase
    hard constraints on the policy_question path ARE handled -- in
    ground_policy_answer() itself, and in process_request()'s post-stage
    adversarial-flag check.)

    policy_question is skipped here -- prompts/decide_action.py has no
    instructions for it, ground_policy_answer() decides that action instead.
    unknown is also skipped (forced ESCALATE) -- there's no knowledge doc to
    ground a decision for an intent we couldn't classify, so asking the LLM
    to decide anyway would just be guessing; same "don't guess" principle as
    ground_policy_answer's ungrounded case.
    (Terminal requests -- missing fields, high-risk injection -- never reach
    here at all; process_request() stops calling stages once state.terminal.)
    """
    if state.intent == Intent.POLICY_QUESTION:
        return state

    if state.intent == Intent.UNKNOWN:
        state.action = Action.ESCALATE
        state.flags.append("unknown_intent_escalated")
        return state

    directory = None
    user_id = state.fields.get("user_id")
    if user_id:
        result = lookup_user(user_id)
        state.tool_calls.append(ToolCall(tool="lookup_user", args={"user_id": user_id}, result=result))
        directory = result

    call = llm_gateway.decide_action(
        state.intent.value, state.fields, directory, _relevant_knowledge(state.intent, knowledge)
    )
    decision = call.parsed  # ActionDecision
    record_llm_usage(state, call)

    try:
        state.action = Action(decision.action)
    except ValueError:
        state.action = Action.ESCALATE
        state.flags.append(f"invalid_action_from_llm:{decision.action}")

    if decision.team:
        state.fields["team"] = decision.team
    state.citations.extend(decision.citations)

    if decision.tier is not None:
        try:
            tier = Tier(decision.tier)
        except ValueError:
            # Model returned a tier outside 1-3 -- don't guess what it meant.
            tier = None
            state.action = Action.ESCALATE
            state.flags.append(f"invalid_tier_from_llm:{decision.tier}")

        if tier is not None:
            state.fields["tier"] = tier.value
            if state.intent == Intent.ACCESS_REQUEST:
                # Hard constraints from access_tiers.md -- enforced here, not by the model.
                if tier >= Tier.SENSITIVE:
                    state.requires_approval = True
                    if state.action == Action.AUTO_RESOLVE:
                        state.action = Action.ESCALATE
                        state.flags.append("downgraded_auto_resolve_above_tier1")
                if tier == Tier.RESTRICTED:
                    state.action = Action.ESCALATE
                    state.fields.setdefault("team", Team.SECURITY.value)
            elif state.intent == Intent.DATA_PULL:
                # data_request_rules.md: category 1 is a routed self-serve
                # queue -- there's no tool that actually delivers data, so
                # "auto_resolve" is never correct here even though category 1
                # needs no approval. Categories 2/3 are gated escalations,
                # never self-serve; 3 additionally needs Security (noted via
                # flag -- team can only hold one value, Data Governance is
                # implied by data_request_rules.md for every gated category).
                if tier == Tier.INTERNAL:
                    state.action = Action.ROUTE
                    state.fields.setdefault("team", Team.DATA_ANALYTICS.value)
                else:
                    state.action = Action.ESCALATE
                    state.requires_approval = True
                    if tier == Tier.RESTRICTED:
                        state.fields["team"] = Team.SECURITY.value
                        state.flags.append("requires_data_governance_and_security")
                    else:
                        state.fields["team"] = Team.DATA_GOVERNANCE.value

    if state.intent == Intent.PURCHASE_APPROVAL:
        # purchase_limits.md's bands are pure thresholds on an already-
        # annualized dollar figure (extract_fields' job, per its own field
        # description) -- no fuzzy matching left once that number exists, so
        # compute the band in code instead of trusting the model's arithmetic.
        # Contrast with tier/category above, which genuinely needs LLM
        # judgment to match a loose description against the docs' examples.
        amount = state.fields.get("amount_usd")
        if amount is not None:
            if amount < 500:
                state.action = Action.AUTO_RESOLVE
            elif amount <= 5000:
                state.action = Action.ROUTE
                state.fields["team"] = Team.MANAGER_APPROVALS.value
            else:
                state.action = Action.ESCALATE
                state.fields["team"] = Team.FINANCE_DIRECTOR.value
                state.requires_approval = True
                if amount > 25000:
                    state.flags.append("requires_procurement_legal_review")

    if "pii" in state.flags:
        # pii_handling.md rule 5: PII presence alone forces Data Governance +
        # escalation, even if the tier/category above would otherwise have
        # auto-resolved -- a hard constraint enforced here, same pattern as
        # the Tier-3 rule above, not left to the model's judgment. Overrides
        # whatever team the tier logic picked (e.g. Security for tier 3).
        state.action = Action.ESCALATE
        state.fields["team"] = Team.DATA_GOVERNANCE.value
        state.requires_approval = True
        state.flags.append("pii_forces_escalation")

    state.approval_prompt = decision.approval_prompt if state.requires_approval else None
    return state


def ground_policy_answer(state: RequestState, knowledge: Dict[str, str]) -> RequestState:
    """For policy questions, answer grounded in knowledge/*.md with citations.

    Not applicable to other intents -- decide_action() already set their
    action. This is also where policy_question's own action gets decided since
    decide_action() skips it: auto_resolve if grounded AND the question is the
    whole request; escalate if the docs don't cover it, if the text also tried
    to manipulate us (social_engineering / prompt_injection), or if it carries
    an embedded above-threshold purchase ask (multi_intent + amount).
    """
    if state.intent != Intent.POLICY_QUESTION:
        return state

    call = llm_gateway.ground_policy_answer(state.raw_text, knowledge)
    result = call.parsed  # PolicyAnswer
    record_llm_usage(state, call)

    state.citations.extend(result.citations)

    if not result.grounded:
        # Docs don't cover it -- don't guess, escalate to a human instead.
        state.action = Action.ESCALATE
        state.flags.append("ungrounded_policy_question")
        return state

    # We have an answer grounded in the docs -- keep it no matter what happens
    # to the action below: it is useful context for whoever handles the request.
    state.answer = result.answer

    # A policy_question is only *auto-resolved* when answering the question is
    # the whole request. Two cases where it isn't -- and, as everywhere else in
    # this pipeline, code decides the action for a hard constraint, not the
    # model (this is the policy_question analogue of the PII rule enforced in
    # decide_action, which this path likewise never reaches):
    #   1. adversarial: the text also tried to manipulate us (social_engineering
    #      / prompt_injection flagged by classify_intent). "Adversarial inputs
    #      must be refused or escalated, never complied with" -- ending on
    #      auto_resolve because the policy answer was fine reads as compliance.
    #   2. embedded action: classify flagged multi_intent and extract_fields
    #      pulled a concrete ask riding along -- e.g. REQ-032, "what's the limit?
    #      also approve my $6k/yr BI tool anyway since it's urgent". Answering
    #      the policy part neither grants nor refuses the embedded purchase; a
    #      human still has to. Gated on amount >= $500 so a sub-threshold aside
    #      (REQ-013's $300 "can I just expense this") still auto-resolves like
    #      the pure question it effectively is.
    adversarial = {"social_engineering", "prompt_injection"} & set(state.flags)
    amount = state.fields.get("amount_usd")
    embedded_purchase = "multi_intent" in state.flags and amount is not None and amount >= 500

    if adversarial or embedded_purchase:
        state.action = Action.ESCALATE
        state.flags.append("policy_question_not_auto_resolved")
        if amount is not None and amount >= 500:
            # Same bands as decide_action()'s purchase_approval path.
            state.fields.setdefault(
                "team",
                Team.FINANCE_DIRECTOR.value if amount > 5000 else Team.MANAGER_APPROVALS.value,
            )
    else:
        state.action = Action.AUTO_RESOLVE

    return state


def human_approval_gate(state: RequestState, approver: str = "MOCK_APPROVER", interactive: bool = False) -> bool:
    """Real stop for mutating actions above threshold. Returns True iff approved.

    interactive=True: a live person is asked via input() -- for a demo run.
    interactive=False (default -- what main()'s batch loop uses, so a 40-request
    run doesn't block on stdin 40 times): a mock decision from the
    APPROVAL_MOCK_DECISION env var, so eval runs stay deterministic/reproducible.
    Default is deny -- nothing mutates without an explicit approval either way.
    """
    if interactive:
        print(f"\n[APPROVAL REQUIRED] request={state.id}")
        print(state.approval_prompt or "(no approval_prompt set)")
        answer = input(f"Approve as {approver}? [y/N]: ").strip().lower()
        return answer == "y"

    return os.environ.get("APPROVAL_MOCK_DECISION", "deny").strip().lower() == "approve"


def execute_tools(state: RequestState, interactive: bool = False) -> RequestState:
    """Perform the decided action via the stub tools.

    grant_access is only ever called for access_request with a known tier --
    every other outcome (route, non-access escalate, a denied approval, a
    grant_access that still fails after retry) becomes a create_ticket for a
    human to pick up instead. redact_pii() already ran before this stage, so
    payloads built from state.fields/state.answer here are safe (no raw PII).
    """
    if state.terminal:
        # REJECT: CANNOT_FULFILL_MESSAGE already stands, nothing to execute.
        return state

    team = state.fields.get("team", "Unassigned")

    def route_to_team(reason: str) -> None:
        payload = {"request_id": state.id, "intent": state.intent.value, "reason": reason, **state.fields}
        result = create_ticket(team, payload)
        state.tool_calls.append(ToolCall(tool="create_ticket", args={"team": team}, result=result))

    if state.intent == Intent.ACCESS_REQUEST and state.fields.get("tier") is not None:
        approved_by = None
        if state.requires_approval:
            if not human_approval_gate(state, interactive=interactive):
                state.flags.append("approval_denied")
                state.action = Action.ESCALATE
                route_to_team("approval_denied")
                return state
            approved_by = "MOCK_APPROVER" if not interactive else "interactive_approver"

        user_id, resource, tier = state.fields.get("user_id"), state.fields.get("resource"), state.fields.get("tier")
        args = {"user": user_id, "resource": resource, "tier": tier}

        # grant_access fails ~15% of the time on a flaky *simulated* upstream,
        # not because the request itself is bad -- retry up to MAX_GRANT_ATTEMPTS
        # total, then fall back to escalate + create_ticket below. Budget kept
        # low because grant_access is a non-idempotent mutation (see the
        # MAX_GRANT_ATTEMPTS comment / NETANEL_NOTES.md §10). No backoff between
        # attempts: the stub's failure is instantaneous and independent per
        # call, so a sleep would only inflate eval latency for no signal.
        result: Dict[str, Any] = {}
        for _ in range(MAX_GRANT_ATTEMPTS):
            result = grant_access(user_id, resource, tier)
            state.tool_calls.append(ToolCall(tool="grant_access", args=args, result=result, approved_by=approved_by))
            if result.get("ok"):
                break

        if result.get("ok"):
            state.action = Action.AUTO_RESOLVE
        else:
            state.flags.append("grant_access_failed")
            state.action = Action.ESCALATE
            route_to_team("grant_access_failed")
        return state

    if state.action in (Action.ROUTE, Action.ESCALATE):
        route_to_team(state.action.value)

    return state


def redact_pii(state: RequestState) -> RequestState:
    """Mask PII (see knowledge/pii_handling.md) before anything downstream --
    the audit record or a create_ticket payload -- can see the raw value.

    Runs after extraction/grounding (which need the raw text) and before
    execute_tools, so it's the single choke point everything serialized
    passes through.
    """
    state.raw_text, found_text = pii.redact(state.raw_text)

    found_fields = False
    for key, value in state.fields.items():
        if isinstance(value, str):
            masked, found = pii.redact(value)
            state.fields[key] = masked
            found_fields = found_fields or found

    found_answer = False
    if state.answer:
        state.answer, found_answer = pii.redact(state.answer)

    if (found_text or found_fields or found_answer) and "pii" not in state.flags:
        state.flags.append("pii")

    return state


# --------------------------------------------------------------------------- #
# Orchestration
# --------------------------------------------------------------------------- #
def process_request(raw: Dict[str, Any], knowledge: Dict[str, str], interactive: bool = False) -> RequestState:
    """Run one request through all stages and return the completed state.

    NOTE: the ordering below is a suggestion. Part of the exercise is deciding
    the right control flow (e.g. classify -> flag injection -> maybe short-circuit).

    ``state.terminal`` is checked once here, between judgment stages, instead
    of each stage re-checking every earlier stage's outcome: a request marked
    terminal (missing fields, high-risk injection) skips straight to
    redact_pii/execute_tools with the REJECT answer already set by whichever
    stage rejected it.

    The classify->ground_policy_answer block is wrapped in a try/except: those
    are the only stages that call the LLM gateway, and llm_gateway already
    retries what it can (malformed JSON, transient network/rate-limit/5xx --
    see _call_structured / _create_with_backoff). What's left after those
    retries are exhausted, or any other genuinely unexpected failure, is a
    single boundary here rather than none at all -- one bad request must not
    take down a 40-request batch (main() / run_evals.py both call this
    function in a loop with no try/except of their own; this is the one place
    that protects both). redact_pii/execute_tools always still run afterward
    (outside the try) so a failed request still gets PII-masked and routed to
    a human queue like any other escalation, instead of silently vanishing
    from the audit trail.
    """
    t0 = time.perf_counter()
    state = RequestState(
        id=raw.get("id", "UNKNOWN"),
        raw_text=raw.get("raw_text", ""),
        label_status=raw.get("label_status", "unlabeled"),
    )

    try:
        state = classify_intent(state)
        if not state.terminal:
            state = extract_fields(state, knowledge)
            state = validate_fields(state)
        if not state.terminal:
            state = decide_action(state, knowledge)
            state = ground_policy_answer(state, knowledge)
    except Exception as e:
        state.action = Action.ESCALATE
        state.answer = PIPELINE_ERROR_MESSAGE
        state.flags.append(f"pipeline_error:{type(e).__name__}")
        print(f"[pipeline] ERROR processing {state.id}: {type(e).__name__}: {e}", file=sys.stderr)

    # Hard constraint, enforced once here regardless of which intent path ran
    # (decide_action / ground_policy_answer each only see their own slice):
    # "adversarial and out-of-scope inputs must be refused or escalated, never
    # complied with." classify_intent flags the lower-confidence adversarial
    # cases (a high-risk injection score is already a terminal reject in
    # classify_intent); if such a request still came out auto_resolve or route,
    # force escalate so a human sees it. reject/escalate are left as-is.
    if not state.terminal and state.action in (Action.AUTO_RESOLVE, Action.ROUTE):
        if {"social_engineering", "prompt_injection"} & set(state.flags):
            state.action = Action.ESCALATE
            state.flags.append("adversarial_flag_forces_escalation")

    state = redact_pii(state)
    state = execute_tools(state, interactive=interactive)

    state.latency_ms = (time.perf_counter() - t0) * 1000.0
    return state


def load_requests(path: str = REQUESTS_PATH) -> List[Dict[str, Any]]:
    with open(path, "r", encoding="utf-8") as fh:
        return [json.loads(line) for line in fh if line.strip()]


def _parse_delay(argv: List[str]) -> float:
    """Optional ``--delay <seconds>``: a proactive pause between requests in
    the batch loop, on top of llm_gateway's reactive backoff-retry on rate
    limits (_create_with_backoff) -- throttling client-side so a burst of ~40
    sequential requests is less likely to trip the provider's rate limit in
    the first place, not just recover after it already has.
    """
    if "--delay" in argv:
        idx = argv.index("--delay")
        if idx + 1 < len(argv):
            try:
                return float(argv[idx + 1])
            except ValueError:
                pass
    return 0.0


def main(argv: Optional[List[str]] = None) -> int:
    argv = argv if argv is not None else sys.argv[1:]
    # -h/--help (or any unrecognized flag) must exit before touching the API --
    # this used to silently fall through to a real full-batch run on a typo'd
    # or unknown flag, which is exactly how an unintended live run happened
    # once during development. Better to refuse an unknown flag than guess.
    if "-h" in argv or "--help" in argv:
        print(__doc__)
        print("Flags: --interactive, --delay <seconds>")
        return 0
    known = {"--interactive", "--delay"}
    for arg in argv:
        if arg.startswith("--") and arg not in known:
            print(f"[pipeline] unknown flag {arg!r} -- refusing to run rather than ignore it. "
                  f"Known flags: {sorted(known)}", file=sys.stderr)
            return 1

    # --interactive: real input() prompts at the approval gate, for a live demo.
    # Default (batch/eval) uses APPROVAL_MOCK_DECISION so a full run never blocks on stdin.
    interactive = "--interactive" in argv
    delay_seconds = _parse_delay(argv)

    requests = load_requests()
    knowledge = load_knowledge()
    count = 0
    total_input_tokens = 0
    total_output_tokens = 0

    # Written one line at a time (and flushed) as each request finishes,
    # not buffered in memory and written once at the end -- so a run killed
    # partway through (Ctrl-C, an exhausted retry outside process_request's
    # own safety net, ...) still leaves every request processed so far on
    # disk instead of losing the whole batch.
    with open(DEFAULT_RESULTS_PATH, "w", encoding="utf-8") as fh:
        for raw in requests:
            state = process_request(raw, knowledge, interactive=interactive)
            fh.write(json.dumps(state.to_audit_record()) + "\n")
            fh.flush()
            count += 1
            total_input_tokens += state.input_tokens
            total_output_tokens += state.output_tokens
            if delay_seconds:
                time.sleep(delay_seconds)

    print(f"[pipeline] processed {count} requests -> {DEFAULT_RESULTS_PATH}")
    print(
        f"[pipeline] tokens: {total_input_tokens:,} input + {total_output_tokens:,} output "
        f"= {total_input_tokens + total_output_tokens:,} total"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
