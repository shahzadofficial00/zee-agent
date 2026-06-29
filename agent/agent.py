from langchain.agents import create_agent
from agent.llm import llm
from agent.tools import show_menu, confirm_order, confirm_reservation
from agent.memory_tools import save_customer_info, get_customer_info
from langgraph.store.memory import InMemoryStore
from agent.middleware import RestaurantGuardrail, model_retry, summarization, tool_retry, pii_phone
from agent.prompt import SYSTEM_PROMPT
from agent.tools import show_menu, confirm_order, confirm_reservation, show_order_history, show_item, show_category

tools = [
    show_menu, confirm_order, confirm_reservation, show_order_history, show_item, show_category,
    save_customer_info, get_customer_info,
]
agent = create_agent(
    model=llm,
    tools=tools,
    system_prompt=SYSTEM_PROMPT,
    store = InMemoryStore(),
    middleware=[
        RestaurantGuardrail(),
        model_retry,
        summarization,
        tool_retry,
        pii_phone,
    ],
)
