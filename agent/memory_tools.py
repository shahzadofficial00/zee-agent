from langchain_core.tools import tool
from langgraph.prebuilt import InjectedStore
from langgraph.store.base import BaseStore
from typing import Annotated

@tool
def get_customer_info(user_id: str, store: Annotated[BaseStore, InjectedStore()]) -> str:
    """Get previously saved customer name and phone number. Pass the current user_id."""
    try:
        info = store.get(("customers",), user_id)
        return str(info.value) if info else "No saved info"
    except Exception:
        return "No saved info"

@tool
def save_customer_info(user_id: str, name: str, phone: str, store: Annotated[BaseStore, InjectedStore()]) -> str:
    """Save customer name and phone number. Pass the current user_id, name, and phone."""
    try:
        store.put(("customers",), user_id, {"name": name, "phone": phone})
        return "Customer info saved."
    except Exception:
        return "Could not save customer info."