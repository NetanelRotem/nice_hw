"""Stub tools for the "Triage & Resolve" take-home exercise.

These are FAKE, in-memory stand-ins for real internal services. They have
*simulated* side effects only — nothing leaves this process, no real access is
granted, no real ticket is filed. Every call is logged with structured fields so
you can build an audit trail and observe tool behavior in your eval harness.

Provided tools
--------------
- ``lookup_user(user_id)``   -> read-only directory lookup (role/team/clearance).
- ``grant_access(user, resource, tier)`` -> **mutating**. Simulates a flaky API:
  fails randomly ~15% of the time. Per policy, grants above the risk threshold must
  pass a human-approval gate in YOUR pipeline before this is ever called; this stub
  does NOT enforce that gate for you.
- ``create_ticket(team, payload)`` -> routes work to a team queue, returns a ticket id.

Notes
-----
- Logging goes through the stdlib ``logging`` module under the logger name
  ``stub_tools`` and also returns structured results you can persist.
- ``grant_access`` failure is controlled by ``random.random() < FAILURE_RATE``.
  Seed ``random`` in your harness if you want reproducible runs.
- Do NOT put raw PII into ``create_ticket`` payloads (see knowledge/pii_handling.md).
"""
from __future__ import annotations

import logging
import random
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Dict, Optional

logger = logging.getLogger("stub_tools")
if not logger.handlers:
    # Basic config so the stubs are useful out of the box; candidates may replace this.
    _h = logging.StreamHandler()
    _h.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s %(message)s"))
    logger.addHandler(_h)
    logger.setLevel(logging.INFO)

# Probability that a grant_access call fails (simulated flaky downstream API).
FAILURE_RATE = 0.15


# --------------------------------------------------------------------------- #
# Fake user directory. Includes an entry for every user_id referenced in
# requests.jsonl. clearance is an int tier (1=Internal, 2=Sensitive, 3=Restricted)
# the user is *already* cleared up to.
# --------------------------------------------------------------------------- #
_DIRECTORY: Dict[str, Dict[str, Any]] = {
    "u1042": {"name": "Jamie Chen",     "role": "Marketing Analyst", "team": "Marketing",   "clearance": 1},
    "u2087": {"name": "Priya Nair",     "role": "SRE",               "team": "SRE",         "clearance": 2},
    "u4210": {"name": "Diego Alvarez",  "role": "Sales Rep",         "team": "Sales",       "clearance": 1},
    "u5567": {"name": "Wei Zhang",      "role": "Financial Analyst", "team": "Finance",     "clearance": 2},
    "u8890": {"name": "Sam Okafor",     "role": "Data Engineer",     "team": "Data",        "clearance": 2},
    "u9145": {"name": "Ravi Patel",     "role": "Ops Coordinator",   "team": "Operations",  "clearance": 1},
}


@dataclass
class ToolResult:
    """Structured result returned by mutating/routing tools."""
    ok: bool
    tool: str
    data: Dict[str, Any] = field(default_factory=dict)
    error: Optional[str] = None
    latency_ms: float = 0.0

    def as_dict(self) -> Dict[str, Any]:
        return {
            "ok": self.ok,
            "tool": self.tool,
            "data": self.data,
            "error": self.error,
            "latency_ms": round(self.latency_ms, 2),
        }


def lookup_user(user_id: str) -> Dict[str, Any]:
    """Read-only directory lookup. Returns role/team/clearance for ``user_id``.

    Returns a dict. If the user is unknown, returns ``{"found": False, ...}`` rather
    than raising, so your pipeline can decide how to handle unknown requesters.
    """
    t0 = time.perf_counter()
    entry = _DIRECTORY.get(user_id)
    latency_ms = (time.perf_counter() - t0) * 1000.0
    if entry is None:
        logger.info("lookup_user user_id=%s found=false latency_ms=%.2f", user_id, latency_ms)
        return {"user_id": user_id, "found": False}
    result = {"user_id": user_id, "found": True, **entry}
    logger.info(
        "lookup_user user_id=%s found=true role=%r team=%r clearance=%s latency_ms=%.2f",
        user_id, entry["role"], entry["team"], entry["clearance"], latency_ms,
    )
    return result


def grant_access(user: str, resource: str, tier: int) -> Dict[str, Any]:
    """MUTATING (simulated). Grant ``user`` access to ``resource`` at ``tier``.

    Fails randomly ~15% of the time to simulate a flaky downstream API. Every call
    is logged, success or failure. This stub does NOT enforce the human-approval
    gate — your pipeline is responsible for that before calling this function.
    """
    t0 = time.perf_counter()
    call_id = uuid.uuid4().hex[:12]
    failed = random.random() < FAILURE_RATE
    latency_ms = (time.perf_counter() - t0) * 1000.0

    if failed:
        logger.warning(
            "grant_access call_id=%s user=%s resource=%r tier=%s status=FAILED "
            "error=%r latency_ms=%.2f",
            call_id, user, resource, tier, "upstream_5xx", latency_ms,
        )
        return ToolResult(
            ok=False, tool="grant_access",
            data={"call_id": call_id, "user": user, "resource": resource, "tier": tier},
            error="upstream_5xx", latency_ms=latency_ms,
        ).as_dict()

    logger.info(
        "grant_access call_id=%s user=%s resource=%r tier=%s status=OK latency_ms=%.2f",
        call_id, user, resource, tier, latency_ms,
    )
    return ToolResult(
        ok=True, tool="grant_access",
        data={"call_id": call_id, "user": user, "resource": resource, "tier": tier,
              "granted": True},
        latency_ms=latency_ms,
    ).as_dict()


def create_ticket(team: str, payload: Dict[str, Any]) -> Dict[str, Any]:
    """Route work to ``team``'s queue. Returns a fake ticket id.

    Reminder: do not include raw PII in ``payload`` (see knowledge/pii_handling.md).
    """
    t0 = time.perf_counter()
    ticket_id = f"TCK-{uuid.uuid4().hex[:8].upper()}"
    latency_ms = (time.perf_counter() - t0) * 1000.0
    logger.info(
        "create_ticket ticket_id=%s team=%r payload_keys=%s latency_ms=%.2f",
        ticket_id, team, sorted(payload.keys()), latency_ms,
    )
    return ToolResult(
        ok=True, tool="create_ticket",
        data={"ticket_id": ticket_id, "team": team, "payload": payload},
        latency_ms=latency_ms,
    ).as_dict()


if __name__ == "__main__":
    # Tiny self-demo so `python3 stub_tools.py` shows the tools working.
    random.seed(0)
    print(lookup_user("u1042"))
    print(lookup_user("u9999"))
    print(grant_access("u1042", "Analytics Dashboard", 1))
    print(create_ticket("Engineering", {"summary": "500 on export", "ref": "REQ-019"}))
