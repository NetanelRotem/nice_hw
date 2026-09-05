"""LLM call gateway -- every model call in this codebase goes through here.

``_call_structured`` is the only place that talks to the provider SDK; the
named functions below (one per call site) are what pipeline.py and
run_evals.py actually import. To switch providers later, only this file
changes -- no caller does.
"""
from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Type, TypeVar

from openai import (
    APIConnectionError,
    APITimeoutError,
    InternalServerError,
    OpenAI,
    RateLimitError,
)
from pydantic import BaseModel, ValidationError

try:
    from dotenv import load_dotenv

    load_dotenv()
except ImportError:
    pass

from . import prompts
from .schemas import ActionDecision, ExtractedFields, IntentClassification, JudgeVerdict, PolicyAnswer

OPENROUTER_API_KEY = os.environ.get("OPENROUTER_API_KEY")
INTENT_MODEL = os.environ.get("INTENT_MODEL", "openai/gpt-4o-mini")
JUDGE_MODEL = os.environ.get("JUDGE_MODEL", "anthropic/claude-3.5-sonnet")

_client: OpenAI | None = None


def _get_client() -> OpenAI:
    global _client
    if _client is None:
        if not OPENROUTER_API_KEY:
            raise RuntimeError(
                "OPENROUTER_API_KEY not set. Copy .env.example to .env and fill it in."
            )
        _client = OpenAI(base_url="https://openrouter.ai/api/v1", api_key=OPENROUTER_API_KEY)
    return _client


T = TypeVar("T", bound=BaseModel)

# Transient failures of the call itself (network/timeout/rate-limit/provider
# 5xx) -- worth a backoff-retry since the same request would likely succeed a
# moment later. Deliberately NOT retried here: 4xx errors (bad request, auth,
# etc.) -- those won't succeed on retry, so they're left to propagate straight
# up to process_request()'s outer catch (pipeline.py), same as any other
# unexpected failure.
_TRANSIENT_ERRORS = (APIConnectionError, APITimeoutError, RateLimitError, InternalServerError)


def _create_with_backoff(client: OpenAI, *, max_attempts: int = 3, **kwargs: Any):
    """``client.chat.completions.create(**kwargs)`` with exponential backoff on
    transient provider/network failures. A different failure mode from the
    JSON/schema retry below (a failed call vs. a malformed reply), so it gets
    its own loop and its own retry budget instead of sharing one.
    """
    delay = 1.0
    for attempt in range(max_attempts):
        try:
            return client.chat.completions.create(**kwargs)
        except _TRANSIENT_ERRORS:
            if attempt == max_attempts - 1:
                raise
            time.sleep(delay)
            delay *= 2


@dataclass
class StructuredCall:
    parsed: BaseModel
    prompt_tokens: int
    completion_tokens: int
    latency_ms: float


def _call_structured(
    *, model: str, system: str, user: str, schema: Type[T], max_retries: int = 1
) -> StructuredCall:
    """Call ``model``, parse the JSON reply into ``schema``.

    Retries once with the validation error appended if the reply is not
    valid JSON or fails schema validation. Network/rate-limit/5xx failures of
    the call itself are handled separately, with backoff, by
    ``_create_with_backoff`` -- see its docstring for why that's a distinct
    retry loop from this one.
    """
    client = _get_client()
    t0 = time.perf_counter()
    messages = [
        {"role": "system", "content": system},
        {
            "role": "user",
            "content": user
            + "\n\nRespond with ONLY a JSON object matching this schema:\n"
            + json.dumps(schema.model_json_schema()),
        },
    ]
    last_error: Exception | None = None

    for _ in range(max_retries + 1):
        resp = _create_with_backoff(
            client,
            model=model,
            messages=messages,
            response_format={"type": "json_object"},
            temperature=0,
        )
        raw = resp.choices[0].message.content or ""
        usage = resp.usage
        prompt_tokens = usage.prompt_tokens if usage else 0
        completion_tokens = usage.completion_tokens if usage else 0

        try:
            parsed = schema.model_validate(json.loads(raw))
            return StructuredCall(
                parsed=parsed,
                prompt_tokens=prompt_tokens,
                completion_tokens=completion_tokens,
                latency_ms=(time.perf_counter() - t0) * 1000.0,
            )
        except (json.JSONDecodeError, ValidationError) as e:
            last_error = e
            messages.append({"role": "assistant", "content": raw})
            messages.append(
                {"role": "user", "content": f"That was invalid: {e}. Return ONLY the corrected JSON object."}
            )

    raise RuntimeError(f"LLM call to {model} failed after retries: {last_error}")


# --------------------------------------------------------------------------- #
# Named call sites. Add new ones here -- nothing else should call
# _call_structured directly.
# --------------------------------------------------------------------------- #
def classify_intent(raw_text: str) -> StructuredCall:
    return _call_structured(
        model=INTENT_MODEL,
        system=prompts.classify_intent.SYSTEM,
        user=prompts.classify_intent.user(raw_text),
        schema=IntentClassification,
    )


def extract_fields(raw_text: str, intent: str, knowledge: Dict[str, str]) -> StructuredCall:
    return _call_structured(
        model=INTENT_MODEL,
        system=prompts.extract_fields.SYSTEM,
        user=prompts.extract_fields.user(raw_text, intent, knowledge),
        schema=ExtractedFields,
    )


def decide_action(
    intent: str, fields: Dict[str, Any], directory: Optional[Dict[str, Any]], knowledge: Dict[str, str]
) -> StructuredCall:
    return _call_structured(
        model=INTENT_MODEL,
        system=prompts.decide_action.SYSTEM,
        user=prompts.decide_action.user(intent, fields, directory, knowledge),
        schema=ActionDecision,
    )


def ground_policy_answer(question: str, knowledge: Dict[str, str]) -> StructuredCall:
    return _call_structured(
        model=INTENT_MODEL,
        system=prompts.ground_policy_answer.SYSTEM,
        user=prompts.ground_policy_answer.user(question, knowledge),
        schema=PolicyAnswer,
    )


def judge_groundedness(
    question: str, answer: str, citations: List[str], knowledge: Dict[str, str]
) -> StructuredCall:
    return _call_structured(
        model=JUDGE_MODEL,
        system=prompts.judge_groundedness.SYSTEM,
        user=prompts.judge_groundedness.user(question, answer, citations, knowledge),
        schema=JudgeVerdict,
    )
