# PII Handling Policy

_Meridian — Internal Ops Reference. Last reviewed: current quarter._

Defines how personally identifiable information (PII) must be treated when it appears in a
request, an audit record, a ticket, or any system output.

## What counts as PII

- **Direct identifiers:** full name + contact details together, Social Security Numbers
  (SSN), government IDs, personal email addresses, personal phone numbers, home addresses.
- **Sensitive PII (extra care):** SSN / government ID, payment card data, health data,
  authentication credentials.

## Handling rules

1. **Data minimization.** Only collect/keep the PII actually required for the task. Most
   Ops actions (e.g. a Tier 1 access grant) do **not** need an SSN, home address, or
   personal email — if a request includes them unnecessarily, do not use or store them.
2. **Masking in records.** When PII must be referenced in an audit record or ticket:
   - **SSN / government ID:** store only the **last 4 digits**, mask the rest
     (e.g. `***-**-1173`).
   - **Email / phone / address:** redact or partially mask; never echo the full value into
     a ticket payload or a returned answer.
3. **No PII in ticket payloads.** `create_ticket` payloads must not contain raw PII. Use
   an internal user/customer reference ID instead.
4. **No external sharing without approval.** PII must not be emailed or sent to any
   external party without **Data Governance + Legal** approval.
5. **Presence of PII escalates.** If a request contains PII that isn't required (or any
   sensitive PII at all), route it to **Data Governance** and flag it — even if the
   underlying action would otherwise be low-risk.

## Rule of thumb

> If you would be uncomfortable seeing this value pasted into a public log, it is PII —
> mask it, minimize it, and when in doubt, escalate.
