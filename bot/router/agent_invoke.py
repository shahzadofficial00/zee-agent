import asyncio
from agent.agent import agent
from agent.context import Context
from bot.router.state import logger

# ─────────────────────────────────────────────────────────────────────────────
# AGENT INVOCATION HELPERS
# ─────────────────────────────────────────────────────────────────────────────
def _is_empty_agent_response(result) -> bool:
    """True if the model returned neither text nor a tool call — a blank generation.
    A blank final message is NOT considered empty if a tool already ran this turn
    (e.g. a card-only poll turn that intentionally ends with no text reply)."""
    if not isinstance(result, dict) or not result.get("messages"):
        return True
    messages = result["messages"]
    last = messages[-1]
    content = last.content if hasattr(last, "content") else str(last)
    if isinstance(content, list):
        text = " ".join(
            block.get("text", "") if isinstance(block, dict) else str(block)
            for block in content
        ).strip()
    else:
        text = (content or "").strip()
    tool_calls = getattr(last, "tool_calls", None)
    if text or tool_calls:
        return False
    prev = messages[-2] if len(messages) >= 2 else None
    if prev is not None and getattr(prev, "type", None) == "tool":
        return False
    return True


async def _invoke_agent_with_retry(messages, sender: str, retries: int = 4):
    """Gemini occasionally returns a blank generation (no text, no tool call) with
    no error raised — retry before falling back to the apology text."""
    result = None
    for attempt in range(retries + 1):
        result = await asyncio.wait_for(
            agent.ainvoke({"messages": messages}, context=Context(user_id=sender)),
            timeout=30.0,
        )
        if not _is_empty_agent_response(result):
            return result
        last = result["messages"][-1] if isinstance(result, dict) and result.get("messages") else None
        finish_reason = getattr(last, "response_metadata", {}).get("finish_reason") if last is not None else None
        logger.warning(
            f"⚠️ Empty agent response for {sender} (attempt {attempt + 1}/{retries + 1}), "
            f"finish_reason={finish_reason}, retrying"
        )
        if attempt < retries:
            await asyncio.sleep(0.8)
    return result
