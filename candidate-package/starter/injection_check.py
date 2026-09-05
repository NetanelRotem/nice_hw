"""Deterministic prompt-injection heuristic -- a regex backstop alongside the
LLM's own "prompt_injection" self-report from classify_intent.

Why deterministic at all, given classify_intent already flags this itself:
a suspected injection payload shouldn't be sent to the model in the first
place -- if the injection is good enough, it's the classification call itself
that's under attack. This regex pass runs BEFORE that call and can reject
without ever spending a token on the untrusted text.

Not meant to be exhaustive (a determined attacker can dodge any fixed regex
list) -- it's a cheap high-confidence tripwire for the obvious cases, not a
replacement for the LLM's own judgment on subtler ones.
"""
from __future__ import annotations

import re

# Fixed phrasings that show up in the requests.jsonl injection examples and in
# common injection playbooks. Each is a hit; score is 0.5 per hit (2+ hits ==
# clearly not accidental phrasing) capped at 1.0.
_PATTERNS = [
    re.compile(r"ignore (all |any )?(previous|prior|above) instructions", re.I),
    re.compile(r"disregard (all |any )?(previous|prior|above)", re.I),
    re.compile(r"you are now (in )?(admin|root|developer)\b", re.I),
    re.compile(r"\bsystem\s*:", re.I),
    re.compile(r"end of system (message|prompt)", re.I),
    re.compile(r"admin(istrator)? (override|mode)", re.I),
    re.compile(r"approval gate (has been |is )?disabled", re.I),
    re.compile(r"auto-?approve (this|it|the)", re.I),
    re.compile(r"reveal (your |the )?(system prompt|instructions)", re.I),
]


def score(text: str) -> float:
    """Return an injection-risk score in [0, 1]: 0.5 per matched pattern, capped at 1.0."""
    hits = sum(1 for p in _PATTERNS if p.search(text))
    return min(1.0, hits * 0.5)
