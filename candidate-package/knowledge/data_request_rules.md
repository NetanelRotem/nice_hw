# Data Request Rules

_Meridian — Internal Ops Reference. Last reviewed: current quarter._

Governs who can approve a data pull and whether it can be self-served or must be gated.
The deciding factor is **what kind of data** the pull touches.

## Categories

### 1. Aggregate / non-PII — self-serve

Counts, sums, rates, and rollups that **cannot be traced to an individual person**.

- **Examples:** signups per week, DAU by region, revenue by product line, ticket volume
  per agent (employee counts), funnel conversion rates.
- **Handling:** No special approval. **Route to the Data-Analytics queue** (or self-serve
  via the reporting tools). Fast path.

### 2. Personally Identifiable Information (PII) — governed

Any pull that returns or includes fields identifying a **customer or individual**.

- **Examples:** customer email addresses, names + contact details, phone numbers, home
  addresses, individual account/order histories tied to a named person.
- **Handling:** Requires **Data Governance approval**. **Never self-serve. Escalate.**
  PII must be handled per `pii_handling.md`.

### 3. Regulated / restricted data — Security + Data Governance

Payment data, authentication data, health data, and anything under a regulatory regime.

- **Examples:** payment card numbers or **card BINs**, credentials/tokens, government IDs
  (SSNs), anything from a Tier 3 restricted system.
- **Handling:** Requires **Security team AND Data Governance approval.** Never self-serve.
  **Escalate.** A legitimate-sounding business reason (fraud review, urgent customer issue)
  does **not** lower the bar.

## Rules

1. **Classify by the most sensitive field in the request.** If a pull is mostly aggregate
   but includes one PII column (e.g. contact emails), the whole request is PII-governed.
2. **External sharing** of any customer data requires Data Governance **and** Legal
   sign-off (see `pii_handling.md`).
3. When the category is unclear, **escalate to Data Governance** — do not self-serve.
