from langchain.agents import create_agent
from agent.llm import llm
from agent.context import Context
from agent.memory_tools import save_customer_info, get_customer_info
from agent.middleware import RestaurantGuardrail, summarization, tool_retry, pii_phone, model_call_limit, tool_call_limit
from agent.prompt import SYSTEM_PROMPT
from agent.tools import show_menu, confirm_order, confirm_reservation, show_order_history, show_item, show_category, send_single_choice_poll, send_flavor_preference_poll, show_banner, show_poll_history, send_special_instructions_poll, send_rating_poll, send_ranking_poll, show_faq, show_faqs, send_calculator, get_menu_prices, show_events, show_event

tools = [
    show_menu, confirm_order, confirm_reservation, show_order_history, show_item, show_category,
    save_customer_info, get_customer_info,
    send_single_choice_poll, send_flavor_preference_poll, show_banner, show_poll_history, send_special_instructions_poll, send_rating_poll, send_ranking_poll,
    show_faq, show_faqs, send_calculator, get_menu_prices, show_events, show_event
]
agent = create_agent(
    model=llm,
    tools=tools,
    system_prompt=SYSTEM_PROMPT,
    context_schema=Context,
    middleware=[
        RestaurantGuardrail(),
        summarization,
        tool_retry,
        pii_phone,
        model_call_limit,
        tool_call_limit,
    ],
)
