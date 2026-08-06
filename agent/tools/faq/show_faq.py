from langchain_core.tools import tool


@tool
def show_faq(question: str) -> str:
    """Answer ONE specific question the customer asked, using a saved FAQ card.
    Call this for general questions about the cafe — delivery, payment,
    cancelling, allergies, location, how ordering works.
    Pass the customer's question in their own words as question.
    Returns FAQ_NO_MATCH if nothing on file fits; answer normally in that case.
    """
    from db import get_faq

    faq = get_faq(question)
    if not faq:
        return "FAQ_NO_MATCH"
    # Emit the *matched* question, not the customer's phrasing, so the dispatch
    # side gets an exact hit instead of re-running the fuzzy ladder.
    return f"FAQ_TRIGGERED|{faq['question']}"
