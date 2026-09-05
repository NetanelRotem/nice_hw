"""Pydantic schemas for every LLM call boundary.

Each schema is what we force the model's JSON output to validate against.
Keep fields minimal — every field here is something the pipeline or the
audit record actually consumes.
"""
from __future__ import annotations

from typing import List, Optional

from pydantic import BaseModel, Field

VALID_INTENTS = (
    "access_request",
    "data_pull",
    "policy_question",
    "bug_report",
    "purchase_approval",
    "out_of_scope",
)


class IntentClassification(BaseModel):
    intent: str = Field(description=f"one of: {', '.join(VALID_INTENTS)}")
    confidence: float = Field(ge=0.0, le=1.0)
    flags: List[str] = Field(
        default_factory=list,
        description="e.g. prompt_injection, social_engineering, ambiguous, multi_intent, pii",
    )
    reasoning: str = Field(description="one sentence; audit trail only, not shown to the requester")


class ExtractedFields(BaseModel):
    # Which fields are "required" varies by intent (see pipeline.REQUIRED_FIELDS,
    # which enforces this in code) -- said here too so the model knows what to
    # look hard for instead of treating every field as equally optional.
    user_id: Optional[str] = Field(default=None, description="required for access_request, data_pull, purchase_approval")
    resource: Optional[str] = Field(default=None, description="required for access_request, data_pull: what they're asking for")
    # No `tier` here on purpose -- ActionDecision.tier (decide_action) is the
    # authoritative classification and always overwrites it in state.fields
    # anyway. Keeping it here too meant paying for the same classification
    # twice (tokens + a second chance to disagree with itself) for a value
    # nothing ever reads before it's replaced.
    amount_usd: Optional[float] = Field(
        default=None, description="required for purchase_approval; annualize if recurring (monthly * 12)"
    )
    team: Optional[str] = Field(default=None, description="only what the requester stated about themselves, not a routing decision")
    notes: Optional[str] = Field(default=None, description="anything else needed to act, free text")


class ActionDecision(BaseModel):
    action: str = Field(description="one of: auto_resolve, route, escalate, reject")
    team: Optional[str] = None
    tier: Optional[int] = Field(
        default=None,
        description="classification only: sensitivity tier (1-3). access_tiers.md tiers for "
        "access_request; data_request_rules.md categories (1=aggregate/non-PII, 2=PII, "
        "3=regulated/restricted) for data_pull. Not a decision -- approval/escalation/team "
        "consequences are computed in code from this, per intent.",
    )
    approval_prompt: Optional[str] = Field(default=None, description="what a human approver would be asked to confirm")
    citations: List[str] = Field(default_factory=list, description="policy doc filename(s) grounding this decision")
    reasoning: str
    # requires_approval is deliberately NOT here -- decide_action() in pipeline.py
    # computes it from `tier` in code. The approval gate is a hard constraint,
    # not something we take on the model's word.


class PolicyAnswer(BaseModel):
    grounded: bool = Field(description="false if the provided docs do not cover the question")
    answer: Optional[str] = None
    citations: List[str] = Field(default_factory=list, description="doc filenames used")


class JudgeVerdict(BaseModel):
    score: float = Field(ge=0.0, le=1.0)
    verdict: str = Field(description="short label, e.g. grounded / unsupported / partially_grounded")
    rationale: str
