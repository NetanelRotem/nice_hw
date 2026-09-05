"""Triage & Resolve — evaluation harness SKELETON.

This is a SCAFFOLD. It parses args, loads requests.jsonl, runs the (stub)
pipeline, and prints a metrics table with every required metric as a
placeholder. YOUR JOB is to implement the scoring logic behind each metric.

The eval harness is HALF the exercise. We expect:
  - per-dimension metrics (NOT one aggregate score),
  - at least one PROGRAMMATIC check and at least one LLM-AS-JUDGE check,
  - a note on your judge's known limitations / biases,
  - tokens + latency per request.

Required metrics (implement each):
  - intent_accuracy        : intent classification accuracy on labeled items
  - routing_correctness    : fraction with the correct action decision
  - groundedness           : are policy answers supported by cited docs (judge)
  - refusal_rate           : refused/escalated fraction on adversarial+OOS items
  - tool_call_validity     : valid tool calls incl. behavior under 15% failure
  - avg_cost_usd           : mean cost per request
  - avg_latency_ms         : mean latency per request

Run:  python3 -m starter.run_evals               (from candidate-package/)
  or: python3 starter/run_evals.py --requests requests.jsonl
"""
from __future__ import annotations

import argparse
import os
import sys
from typing import Any, Dict, List

_HERE = os.path.dirname(os.path.abspath(__file__))
_PKG_ROOT = os.path.dirname(_HERE)
if _PKG_ROOT not in sys.path:
    sys.path.insert(0, _PKG_ROOT)

from starter.pipeline import (  # noqa: E402
    RequestState,
    load_knowledge,
    load_requests,
    process_request,
)

# Metric names must line up with what your write-up reports.
METRIC_NAMES = [
    "intent_accuracy",
    "routing_correctness",
    "groundedness",
    "refusal_rate",
    "tool_call_validity",
    "avg_cost_usd",
    "avg_latency_ms",
]


# --------------------------------------------------------------------------- #
# Scoring — IMPLEMENT THESE
# --------------------------------------------------------------------------- #
def score_intent_accuracy(states: List[RequestState]) -> float:
    """TODO (programmatic): compare predicted intent to ground truth on labeled items.

    You must supply your own ground-truth labels for the labeled items (the
    candidate file intentionally does not include the answer key). Document how
    you derived them.
    """
    return 0.0


def score_routing_correctness(states: List[RequestState]) -> float:
    """TODO (programmatic): fraction of requests with the correct action decision."""
    return 0.0


def score_groundedness(states: List[RequestState]) -> float:
    """TODO (LLM-as-judge): is each policy answer supported by its cited doc(s)?

    Note your judge's biases (e.g. length/fluency preference) and how you'd
    calibrate against human labels.
    """
    return 0.0


def score_refusal_rate(states: List[RequestState]) -> float:
    """TODO (programmatic): on adversarial + out-of-scope items, fraction refused/escalated."""
    return 0.0


def score_tool_call_validity(states: List[RequestState]) -> float:
    """TODO (programmatic): were tool calls well-formed, gated when required, and were
    grant_access failures (≈15%) handled gracefully?"""
    return 0.0


def score_cost(states: List[RequestState]) -> float:
    """TODO: mean cost_usd per request."""
    return 0.0


def score_latency(states: List[RequestState]) -> float:
    """TODO: mean latency_ms per request."""
    if not states:
        return 0.0
    return sum(s.latency_ms for s in states) / len(states)


SCORERS = {
    "intent_accuracy": score_intent_accuracy,
    "routing_correctness": score_routing_correctness,
    "groundedness": score_groundedness,
    "refusal_rate": score_refusal_rate,
    "tool_call_validity": score_tool_call_validity,
    "avg_cost_usd": score_cost,
    "avg_latency_ms": score_latency,
}


def print_metrics_table(metrics: Dict[str, float]) -> None:
    print("\n" + "=" * 44)
    print(f"{'METRIC':<28}{'VALUE':>16}")
    print("-" * 44)
    for name in METRIC_NAMES:
        val = metrics.get(name, 0.0)
        print(f"{name:<28}{val:>16.3f}")
    print("=" * 44)
    print("[scaffold] all metrics are placeholders — implement the scorers.\n")


def run(requests_path: str) -> Dict[str, float]:
    requests = load_requests(requests_path)
    knowledge = load_knowledge()

    states: List[RequestState] = [process_request(raw, knowledge) for raw in requests]

    metrics = {name: scorer(states) for name, scorer in SCORERS.items()}
    return metrics


def parse_args(argv: List[str]) -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Triage & Resolve eval harness (scaffold).")
    p.add_argument(
        "--requests",
        default=os.path.join(_PKG_ROOT, "requests.jsonl"),
        help="Path to requests.jsonl",
    )
    p.add_argument(
        "--judge-model",
        default=None,
        help="Model id for the LLM-as-judge checks (candidate wires this up).",
    )
    return p.parse_args(argv)


def main(argv: List[str] | None = None) -> int:
    args = parse_args(argv if argv is not None else sys.argv[1:])
    if not os.path.exists(args.requests):
        print(f"error: requests file not found: {args.requests}", file=sys.stderr)
        return 1
    metrics = run(args.requests)
    print_metrics_table(metrics)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
