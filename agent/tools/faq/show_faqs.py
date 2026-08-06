from langchain_core.tools import tool


@tool
def show_faqs() -> str:
    """Show every saved FAQ as one browsable accordion card.
    Call this when the customer asks to see all the common questions
    ("faq", "help", "what can you tell me about the cafe"), or when they have a
    general question you could not match to a single FAQ.
    """
    return "FAQ_LIST_TRIGGERED"
