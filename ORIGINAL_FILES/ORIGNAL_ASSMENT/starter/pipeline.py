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
import os
import sys
import time
from dataclasses import dataclass, field, asdict
from enum import Enum
from typing import Any, Dict, List, Optional

# Make the sibling `tools` package importable whether run as a module or a script.
_HERE = os.path.dirname(os.path.abspath(__file__))
_PKG_ROOT = os.path.dirname(_HERE)  # candidate-package/
if _PKG_ROOT not in sys.path:
    sys.path.insert(0, _PKG_ROOT)

from tools import create_ticket, grant_access, lookup_user  # noqa: E402

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

    # Observability
    tokens: int = 0
    cost_usd: float = 0.0
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
# Pipeline stages — IMPLEMENT THESE
# --------------------------------------------------------------------------- #
def classify_intent(state: RequestState) -> RequestState:
    """TODO: Classify ``state.raw_text`` into an ``Intent``.

    - Decide where an LLM is actually needed vs. deterministic rules.
    - Treat the request text as UNTRUSTED DATA, never as instructions.
    - Set ``state.intent`` and ``state.confidence``. Flag adversarial / injection
      attempts in ``state.flags`` rather than obeying them.
    """
    # Placeholder: leave UNKNOWN so the scaffold runs without pretending to work.
    return state


def extract_fields(state: RequestState) -> RequestState:
    """TODO: Extract the fields needed to act on this intent.

    e.g. user_id, resource, requested tier, dollar amount (annualized!), team.
    Populate ``state.fields``. Consider ``lookup_user`` to resolve/verify identity.
    """
    return state


def decide_action(state: RequestState) -> RequestState:
    """TODO: Decide ``auto_resolve`` | ``route`` | ``escalate`` | ``reject``.

    Apply the policy docs (tiers, purchase bands, data categories). Prefer
    deterministic rules where the policy is deterministic. Set
    ``state.requires_approval`` for any mutating action above the risk threshold.
    """
    return state


def ground_policy_answer(state: RequestState) -> RequestState:
    """TODO: For policy questions, answer grounded in knowledge/*.md with citations.

    - Populate ``state.answer`` and ``state.citations`` (filenames / sections).
    - If the docs do NOT cover the question, do NOT guess: escalate and say so.
    """
    return state


def human_approval_gate(state: RequestState, approver: str = "MOCK_APPROVER") -> bool:
    """TODO: Implement the approval gate for mutating actions above threshold.

    Must be a real stop in the pipeline — not a comment or a print. In a batch/
    eval run this can be a mock approver (e.g. read from a decision map, env var,
    or always-deny), but the CONTROL FLOW must genuinely prevent ``grant_access``
    from firing unless approval is granted. Return True iff approved.
    """
    # Placeholder: deny by default so nothing mutating fires from the scaffold.
    return False


def execute_tools(state: RequestState) -> RequestState:
    """TODO: Perform the decided action via the stub tools.

    - ``route``  -> ``create_ticket(team, payload)`` (no raw PII in payload!).
    - ``auto_resolve`` for access -> ``grant_access(...)`` ONLY for below-threshold
      grants; anything gated must pass ``human_approval_gate`` first.
    - Handle ``grant_access`` failures gracefully (it fails ~15% of the time):
      retry / backoff / escalate — your call, and justify it in the write-up.
    - Append every attempt to ``state.tool_calls``.
    """
    return state


def redact_pii(state: RequestState) -> RequestState:
    """TODO (recommended): ensure no raw sensitive PII lands in the audit record.

    e.g. mask SSNs to last 4, redact personal emails/phones/addresses. See
    knowledge/pii_handling.md. Flag PII presence in ``state.flags``.
    """
    return state


# --------------------------------------------------------------------------- #
# Orchestration
# --------------------------------------------------------------------------- #
def process_request(raw: Dict[str, Any], knowledge: Dict[str, str]) -> RequestState:
    """Run one request through all stages and return the completed state.

    NOTE: the ordering below is a suggestion. Part of the exercise is deciding
    the right control flow (e.g. classify -> flag injection -> maybe short-circuit).
    """
    t0 = time.perf_counter()
    state = RequestState(
        id=raw.get("id", "UNKNOWN"),
        raw_text=raw.get("raw_text", ""),
        label_status=raw.get("label_status", "unlabeled"),
    )

    state = classify_intent(state)
    state = extract_fields(state)
    state = decide_action(state)
    state = ground_policy_answer(state)
    state = redact_pii(state)
    state = execute_tools(state)

    state.latency_ms = (time.perf_counter() - t0) * 1000.0
    return state


def load_requests(path: str = REQUESTS_PATH) -> List[Dict[str, Any]]:
    with open(path, "r", encoding="utf-8") as fh:
        return [json.loads(line) for line in fh if line.strip()]


def main(argv: Optional[List[str]] = None) -> int:
    requests = load_requests()
    knowledge = load_knowledge()
    results: List[Dict[str, Any]] = []

    for raw in requests:
        state = process_request(raw, knowledge)
        results.append(state.to_audit_record())

    with open(DEFAULT_RESULTS_PATH, "w", encoding="utf-8") as fh:
        for rec in results:
            fh.write(json.dumps(rec) + "\n")

    print(f"[scaffold] processed {len(results)} requests -> {DEFAULT_RESULTS_PATH}")
    print("[scaffold] all stages are stubs; implement the TODOs in pipeline.py")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
