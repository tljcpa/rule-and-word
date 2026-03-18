import asyncio
import json
import os
import time
from datetime import datetime
from detector import detect
from rewriter import rewrite
from normalizer import normalize
import config

_log_lock = asyncio.Lock()


def _ensure_log_dir():
    os.makedirs(config.LOG_DIR, exist_ok=True)


async def _write_log(record: dict):
    _ensure_log_dir()
    log_path = os.path.join(config.LOG_DIR, "requests.jsonl")
    line = json.dumps(record, ensure_ascii=False) + "\n"
    async with _log_lock:
        await asyncio.to_thread(_append_line, log_path, line)


def _append_line(log_path: str, line: str):
    with open(log_path, "a", encoding="utf-8") as f:
        f.write(line)


async def get_cached_strategy(violations: list[dict]) -> dict | None:
    """
    预留：查询改写策略缓存
    后期接Redis实现：
      1. REDIS_ENABLED改为true
      2. 连接Redis查询
      3. 命中返回strategy字典，未命中返回None
    """
    return None


async def set_cached_strategy(violations: list[dict], result_text: str) -> None:
    """
    预留：写入改写策略缓存
    后期接Redis实现：
      1. 提取替换策略
      2. 以violations hash为key写入Redis
      3. 设置TTL 7天
    """
    pass


async def process(text: str, request_id: str = "") -> dict:
    t_start = time.time()

    t0 = time.time()
    normalized = normalize(text)
    t_normalize = round((time.time() - t0) * 1000)

    t0 = time.time()
    violations, rules, embed_ms = await detect(text, normalized)
    t_detect = round((time.time() - t0) * 1000)

    cached_strategy = await get_cached_strategy(violations)
    result, model_used, used_fallback, t_rewrite = await rewrite(
        text, violations, rules, cached_strategy
    )

    if violations and result != text:
        await set_cached_strategy(violations, result)

    t_total = round((time.time() - t_start) * 1000)

    print(
        f"[耗时] normalize:{t_normalize}ms | "
        f"embed:{embed_ms}ms | "
        f"detect:{t_detect}ms | "
        f"rewrite:{t_rewrite}ms | "
        f"total:{t_total}ms | "
        f"model:{model_used} | "
        f"fallback:{used_fallback}"
    )

    await _write_log({
        "timestamp": datetime.now().isoformat(),
        "request_id": request_id,
        "embed_base_url": config.EMBED_BASE_URL,
        "embed_model": config.EMBED_MODEL,
        "embed_ms": embed_ms,
        "primary_base_url": config.PRIMARY_BASE_URL,
        "primary_model": config.PRIMARY_MODEL,
        "fallback_base_url": config.FALLBACK_BASE_URL,
        "fallback_model": config.FALLBACK_MODEL,
        "rewrite_model_used": model_used,
        "used_fallback": used_fallback,
        "temperature": config.TEMPERATURE,
        "top_p": config.TOP_P,
        "max_tokens": config.MAX_TOKENS,
        "llm_timeout": config.LLM_TIMEOUT,
        "sensitive_threshold": config.SENSITIVE_THRESHOLD,
        "rules_threshold": config.RULES_THRESHOLD,
        "sensitive_topk": config.SENSITIVE_TOPK,
        "rules_topk": config.RULES_TOPK,
        "text_length": len(text),
        "result_length": len(result),
        "hit": len(violations) > 0,
        "violations_count": len(violations),
        "violations": violations,
        "normalize_ms": t_normalize,
        "detect_ms": t_detect,
        "rewrite_ms": t_rewrite,
        "total_ms": t_total,
    })

    return {
        "result": result,
        "hit": len(violations) > 0,
        "violations": violations,
        "model_used": model_used,
        "used_fallback": used_fallback,
        "latency_ms": t_total,
        "latency_detail": {
            "normalize_ms": t_normalize,
            "embed_ms": embed_ms,
            "detect_ms": t_detect,
            "rewrite_ms": t_rewrite,
        },
        "request_id": request_id,
    }
