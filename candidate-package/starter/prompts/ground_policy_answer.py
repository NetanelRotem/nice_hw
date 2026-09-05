"""Prompt for llm_gateway.ground_policy_answer."""

SYSTEM = """Answer the question using ONLY the policy documents provided below. Cite \
the filename(s) you used. If the documents do not cover the question, set \
grounded=false and leave answer null -- do not guess or use outside knowledge."""


def user(question: str, knowledge: dict) -> str:
    docs = "\n\n".join(f"--- {name} ---\n{text}" for name, text in knowledge.items())
    return f"Policy documents:\n{docs}\n\nQuestion:\n{question}"
