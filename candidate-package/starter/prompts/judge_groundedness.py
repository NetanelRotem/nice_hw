"""Prompt for llm_gateway.judge_groundedness (LLM-as-judge, eval harness)."""

SYSTEM = """You are auditing whether an answer to a policy question is actually \
supported by its cited document(s). Score 1.0 if every claim in the answer is backed \
by the cited text, 0.0 if unsupported or fabricated, and something in between for \
partial support. Be strict: an unsupported claim is not excused by other correct ones."""


def user(question: str, answer: str, citations: list, knowledge: dict) -> str:
    cited_docs = "\n\n".join(
        f"--- {name} ---\n{knowledge.get(name, '[MISSING DOC]')}" for name in citations
    )
    return (
        f"Question:\n{question}\n\nAnswer given:\n{answer}\n\n"
        f"Cited document(s):\n{cited_docs or '[none cited]'}"
    )
