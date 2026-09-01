from langchain_core.tools import tool


@tool
def show_events() -> str:
    """Show everything coming up at the cafe — events, classes, competitions.
    Call this when the customer asks what's on, what events you have, or
    whether anything is happening soon.
    Returns EVENTS_NONE if there's nothing on the calendar; say so plainly.
    """
    from db import get_events

    return "EVENTS_TRIGGERED" if get_events() else "EVENTS_NONE"
