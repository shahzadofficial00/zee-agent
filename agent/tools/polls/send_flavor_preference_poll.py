from langchain_core.tools import tool

@tool
def send_flavor_preference_poll() -> str:
    """
    Ask the customer which flavors they enjoy so you can recommend
    matching drinks from the menu. Call this when:
    - Customer asks for a recommendation
    - Customer seems unsure what to order
    - Customer says "surprise me" or "what's good?"
    Do NOT call this if customer already knows what they want.
    """
    return "MULTI_POLL_TRIGGERED|What flavors do you enjoy?|Bold & Strong, Creamy, Chocolatey, Sweet, Nutty, Fruity, Earthy & Matcha"