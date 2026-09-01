from langchain_core.tools import tool


@tool
def show_event(event_name: str) -> str:
    """Show ONE event in detail, with its ticket options, so the customer can book.
    Call this when they name an event or ask about tickets, prices or times for one.
    Pass the event's title as event_name.
    Returns EVENT_NO_MATCH if there's no such event; offer show_events instead.

    You never decide whether tickets are available — the card's counts are a
    snapshot and booking is settled when the customer taps. Never promise a
    seat, and never say an event is sold out.
    """
    from db import get_event

    event = get_event(event_name)
    if not event:
        return "EVENT_NO_MATCH"
    # Emit the matched title, not the customer's phrasing, so dispatch gets an
    # exact hit instead of re-running the match ladder.
    return f"EVENT_TRIGGERED|{event['title']}"
