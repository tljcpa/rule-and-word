import asyncio
import re
import time
import openai
import config


async def _call_llm(
    client: openai.AsyncOpenAI,
    model: str,
    messages: list[dict],
) -> str | None:
    try:
        resp = await asyncio.wait_for(
            client.chat.completions.create(
                model=model,
                messages=messages,
                temperature=config.TEMPERATURE,
                top_p=config.TOP_P,
                max_tokens=config.MAX_TOKENS,
            ),
            timeout=config.LLM_TIMEOUT,
        )
        content = resp.choices[0].message.content
        return content
    except (asyncio.TimeoutError, openai.APIError):
        return None


def _strip_thinking(content: str) -> str:
    """Remove <think>...</think> chain-of-thought if present."""
    if "<think>" in content:
        match = re.search(r"</think>(.*)", content, re.DOTALL)
        if match:
            return match.group(1).strip()
    return content.strip()


async def rewrite(
    original_text: str,
    violations: list[dict],
    rules: list[dict],
    cached_strategy: dict | None = None,
) -> tuple[str, str, bool, int]:
    """
    Returns: (result_text, model_used, used_fallback, elapsed_ms)
    """
    if not violations:
        return original_text, "", False, 0

    from prompt import SYSTEM_PROMPT, build_prompt

    user_prompt = build_prompt(original_text, violations, rules, cached_strategy)
    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": user_prompt},
    ]

    t0 = time.time()

    # Try primary model
    primary_client = openai.AsyncOpenAI(
        api_key=config.PRIMARY_API_KEY,
        base_url=config.PRIMARY_BASE_URL,
    )
    content = await _call_llm(primary_client, config.PRIMARY_MODEL, messages)

    used_fallback = False
    model_used = config.PRIMARY_MODEL

    if content is None:
        # Fallback model
        used_fallback = True
        model_used = config.FALLBACK_MODEL
        fallback_client = openai.AsyncOpenAI(
            api_key=config.FALLBACK_API_KEY,
            base_url=config.FALLBACK_BASE_URL,
        )
        content = await _call_llm(fallback_client, config.FALLBACK_MODEL, messages)

    elapsed = round((time.time() - t0) * 1000)

    if content is None:
        print(f"WARNING: 所有LLM均超时，返回原文")
        return original_text, model_used, used_fallback, elapsed

    result = _strip_thinking(content)
    if not result:
        print(f"WARNING: LLM返回空内容，返回原文")
        return original_text, model_used, used_fallback, elapsed

    return result, model_used, used_fallback, elapsed
