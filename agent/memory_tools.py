from langchain.tools import tool, ToolRuntime
from agent.context import Context
from db import get_customer, save_customer

@tool
def get_customer_info(runtime: ToolRuntime[Context]) -> str:
    """Get previously saved customer name and phone number for the current customer."""
    info = get_customer(runtime.context.user_id)
    result = str(info) if info else "No saved info"
    print(f"🧠 get_customer_info(user_id={runtime.context.user_id!r}) → {result}")
    return result

@tool
def save_customer_info(name: str, phone: str, runtime: ToolRuntime[Context]) -> str:
    """Save the current customer's name and phone number. Pass name and phone."""
    try:
        save_customer(runtime.context.user_id, name, phone)
        print(f"🧠 save_customer_info(user_id={runtime.context.user_id!r}, name={name!r}, phone={phone!r}) → saved")
        return "Customer info saved."
    except Exception as e:
        print(f"🧠 save_customer_info(user_id={runtime.context.user_id!r}) → FAILED: {e}")
        return "Could not save customer info."
