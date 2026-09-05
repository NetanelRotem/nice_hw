# Purchase Approval Limits

_Meridian — Internal Ops Reference. Last reviewed: current quarter._

All purchase and subscription approvals are evaluated on an **annualized** basis. If a
cost is quoted monthly, multiply by 12 before applying the thresholds below. If quoted
per-unit (e.g. per-seat), compute the total annual spend.

## Approval bands (annualized spend)

| Annualized cost | Approver | Handling |
|---|---|---|
| **< $500 / yr** | None (auto-approved) | Auto-approve; log to Finance. Employee may self-expense. |
| **$500 – $5,000 / yr** | Direct manager | Route to the **Manager-Approvals** queue. |
| **$5,001 – $25,000 / yr** | Finance Director | Escalate to Finance Director. |
| **> $25,000 / yr** | Finance Director **+** Procurement/Legal | Escalate; contract + security review required. |

## Worked examples

- A $300/yr SaaS tool → **< $500** → auto-approved, logged to Finance.
- 5 seats at $75/mo = $375/mo = **$4,500/yr** → **$500–$5,000** → manager approval.
- A vendor at $3,200/mo = **$38,400/yr** → **> $25,000** → Finance Director + Procurement.

## Rules

1. **Always annualize before choosing a band.** A "$3,200/mo" request is a >$25k decision,
   not a manager-approval decision. This is the most common triage mistake.
2. **Urgency does not change the band.** A board meeting on Friday does not lower a
   $6,000/yr purchase from the Finance-Director band to auto-approve.
3. **Split purchases are aggregated.** Do not break a large purchase into sub-$500 pieces
   to dodge approval.
4. When in doubt about which band applies (e.g. multi-year contracts, usage-based/variable
   spend), escalate to Finance rather than guess.
