"""Prompt for llm_gateway.decide_action.

Resource/category names in the policy docs are illustrative examples, not an
exhaustive enum, and real requests misspell or rephrase them -- a keyword/dict
match against the docs would silently miss variants. So tier/category matching
goes to the LLM, grounded in the docs with citations, instead of a lookup table.

requires_approval is NOT asked for here: pipeline.py's decide_action() computes
it from the returned tier deterministically -- the approval gate is a hard
constraint, not left to the model's judgment.

route vs. escalate for purchase_approval/data_pull is likewise NOT left to the
model: purchase_approval's bands are pure thresholds on an already-annualized
number (extract_fields already has it -- no fuzzy matching left to do, so code
computes the band); data_pull's category needs the same kind of fuzzy doc
matching as access_request's tier, so the model still classifies it (into the
same `tier` field, 1/2/3) but code turns that into route/escalate + team.
"""

SYSTEM = """Decide how to handle this request, using ONLY the policy documents \
provided. Ground your decision: cite the filename(s) that justify it.

- access_request: classify the resource into a tier (1/2/3) per access_tiers.md, \
  in the `tier` field. The doc's resource lists are examples, not exhaustive -- \
  match by what the resource actually is, including misspellings/rephrasings.
- purchase_approval: cite purchase_limits.md and explain your reasoning. The \
  final action/team is computed from the annualized amount_usd in code, not \
  from your answer here -- just ground the reasoning, the bands are pure \
  thresholds.
- data_pull: classify the data category into the `tier` field per \
  data_request_rules.md -- 1=aggregate/non-PII, 2=PII, 3=regulated/restricted. \
  Match by what the data actually is, including misspellings/rephrasings, the \
  same way you would an access_request resource. If a pull mixes categories, \
  classify by the most sensitive field present (data_request_rules.md rule 1).
- bug_report: route to Engineering.
- out_of_scope: reject.
- unknown: not expected here -- pipeline.py resolves this intent before it \
  reaches you.

If a directory lookup is provided and it contradicts the request (unknown user, \
role/team mismatch), that's a red flag -- escalate, don't resolve. If the docs \
don't clearly cover this case, escalate and say so in reasoning -- do not guess."""


def user(intent: str, fields: dict, directory: dict | None, knowledge: dict) -> str:
    docs = "\n\n".join(f"--- {name} ---\n{text}" for name, text in knowledge.items())
    return (
        f"Intent: {intent}\n\n"
        f"Extracted fields: {fields}\n\n"
        f"Directory lookup: {directory if directory is not None else 'not applicable'}\n\n"
        f"Policy documents:\n{docs}"
    )
