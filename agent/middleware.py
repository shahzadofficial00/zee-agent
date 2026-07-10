from typing import Any
from langchain.agents.middleware import (
    SummarizationMiddleware,
    ToolRetryMiddleware,
    PIIMiddleware,
    AgentMiddleware,
    AgentState,
    hook_config,
    ModelCallLimitMiddleware,
    ToolCallLimitMiddleware,
)
from langgraph.runtime import Runtime
from agent.llm import llm

class RestaurantGuardrail(AgentMiddleware):
    """Block banned keywords before hitting the LLM."""

    BANNED = [
        "ignore previous", "ignore instructions", "jailbreak",
        "you are now", "forget you are", "hack", "exploit",
        "system prompt", "sudo", "admin"
    ]

    @hook_config(can_jump_to=["end"])
    def before_agent(self, state: AgentState, runtime: Runtime) -> dict[str, Any] | None:
        humans = [m for m in state["messages"] if m.type == "human"]
        if not humans:
            return None
        content = humans[-1].content.lower()
        for keyword in self.BANNED:
            if keyword in content:
                return {
                    "messages": [{
                        "role": "assistant",
                        "content": "I'm here to help with orders and reservations at Dot Cafe! 🍽️"
                    }],
                    "jump_to": "end"
                }
        return None

summarization = SummarizationMiddleware(
    model=llm,
    trigger=("tokens", 8000),
    keep=("messages", 6),
)

tool_retry = ToolRetryMiddleware(
    max_retries=3,
    tools=["confirm_order", "confirm_reservation"],
    backoff_factor=2.0,
    initial_delay=1.0,
    on_failure="continue",
)

pii_phone = PIIMiddleware(
    "phone_number",
    detector=r"\+?\d{1,3}[\s.-]?\d{3,4}[\s.-]?\d{4}",
    strategy="none",
    apply_to_input=False,
    apply_to_output=False,
)

# No checkpointer is configured (each turn is a fresh agent.ainvoke call), so only
# run_limit (scoped to a single invocation) applies — thread_limit would need a
# checkpointer to track calls across turns and is left unset.
model_call_limit = ModelCallLimitMiddleware(
    run_limit=10,
    exit_behavior="end",
)

tool_call_limit = ToolCallLimitMiddleware(
    run_limit=10,
    exit_behavior="continue",
)