# import os
# from langchain_google_genai import ChatGoogleGenerativeAI
# from langchain_core.rate_limiters import InMemoryRateLimiter

# rate_limiter = InMemoryRateLimiter(
#     requests_per_second=2.0,
#     check_every_n_seconds=0.1,
#     max_bucket_size=10,
# )

# llm = ChatGoogleGenerativeAI(
#     model="gemini-2.5-flash",
#     google_api_key=os.getenv("GOOGLE_API_KEY"),
#     temperature=0.4,
#     max_retries=6,
#     request_timeout=25,
#     rate_limiter=rate_limiter,
#     thinking_budget=0,
# )










import os
from langchain_openai import ChatOpenAI
from langchain_core.rate_limiters import InMemoryRateLimiter

rate_limiter = InMemoryRateLimiter(
    requests_per_second=2.0,
    check_every_n_seconds=0.1,
    max_bucket_size=10,
)

# OpenRouter is OpenAI-compatible — same client, different base_url.
llm = ChatOpenAI(
    model=os.getenv("OPENROUTER_MODEL", "google/gemini-2.5-flash"),
    api_key=os.getenv("OPENROUTER_API_KEY"),
    base_url="https://openrouter.ai/api/v1",
    temperature=0.4,
    # OpenRouter reserves credits against this up front; unset = 65535 = 402 on a
    # low balance. Free (":free") models aren't billed against credits, so 2048 is
    # safe again -- drop it back to ~1024 if OPENROUTER_MODEL is ever pointed at a
    # paid model on a near-empty balance.
    max_tokens=int(os.getenv("OPENROUTER_MAX_TOKENS", "2048")),
    max_retries=6,
    timeout=25,
    rate_limiter=rate_limiter,
)