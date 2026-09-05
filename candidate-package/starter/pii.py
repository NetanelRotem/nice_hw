"""PII detection + masking, per knowledge/pii_handling.md.

Regex-based, best-effort. Catches the direct identifiers the policy names
(SSN, email, phone, simple street addresses, and customer names when they are
attached to a PII-bearing request). A real production system should use a PII
detector such as Presidio/NER + phonenumbers; these rules are deliberately
small, auditable guardrails for the exercise dataset, not a general PII engine.
"""
from __future__ import annotations

import re
from typing import Tuple

_SSN = re.compile(r"\b\d{3}-\d{2}-(\d{4})\b")
_EMAIL = re.compile(r"\b[\w.+-]+@[\w-]+\.[\w.-]+\b")
_PHONE = re.compile(r"\b(?:\+?1[-.\s]?)?\(?\d{3}\)?[-.\s]?\d{3}[-.\s]?\d{4}\b")
_LOCAL_PHONE = re.compile(r"\b\d{3}[-.\s]\d{4}\b")
_STREET_ADDRESS = re.compile(
    r"\b\d{1,6}\s+[A-Z][A-Za-z0-9.'-]*(?:\s+[A-Z][A-Za-z0-9.'-]*){0,4}\s+"
    r"(?:St|Street|Ave|Avenue|Rd|Road|Blvd|Boulevard|Dr|Drive|Ln|Lane|Way|Ct|Court)\b",
    re.I,
)
_CUSTOMER_NAME = re.compile(
    r"\bCustomer\s+([A-Z][A-Za-z.'-]+(?:\s+[A-Z]\.)?(?:\s+[A-Z][A-Za-z.'-]+)+)\b"
)


def _redact_customer_name_if_needed(text: str, pii_found: bool) -> Tuple[str, int]:
    """Redact a customer full name when the same text already contains contact
    PII. Employee names used with internal user IDs are intentionally not
    redacted; the policy calls out customer/individual identifying details,
    and over-redacting every proper name would make access audits harder to
    understand.
    """
    if not pii_found:
        return text, 0
    return _CUSTOMER_NAME.subn("Customer [NAME_REDACTED]", text)


def redact(text: str) -> Tuple[str, bool]:
    """Mask SSNs (keep last 4) and redact emails/phones/addresses in ``text``.

    Returns (masked_text, found_any).
    """
    text, n_ssn = _SSN.subn(lambda m: f"***-**-{m.group(1)}", text)
    text, n_email = _EMAIL.subn("[EMAIL_REDACTED]", text)
    text, n_phone = _PHONE.subn("[PHONE_REDACTED]", text)
    text, n_local_phone = _LOCAL_PHONE.subn("[PHONE_REDACTED]", text)
    text, n_address = _STREET_ADDRESS.subn("[ADDRESS_REDACTED]", text)
    text, n_customer_name = _redact_customer_name_if_needed(
        text, (n_ssn + n_email + n_phone + n_local_phone + n_address) > 0
    )
    return text, (n_ssn + n_email + n_phone + n_local_phone + n_address + n_customer_name) > 0
