from typing import Any
from langchain.agents.middleware import (
    ModelRetryMiddleware,
    SummarizationMiddleware,
    ToolRetryMiddleware,
    PIIMiddleware,
    AgentMiddleware,
    AgentState,
    hook_config,
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
        if not state["messages"]:
            return None
        first = state["messages"][0]
        if first.type != "human":
            return None
        content = first.content.lower()
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

model_retry = ModelRetryMiddleware(
    max_retries=4,
    retry_on=lambda e: "429" in str(e) or "RESOURCE_EXHAUSTED" in str(e),
    backoff_factor=2.0,
    initial_delay=10.0,
    max_delay=120.0,
    jitter=True,
    on_failure="continue",
)

summarization = SummarizationMiddleware(
    model=llm,
    trigger=("messages", 12),
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