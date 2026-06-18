import asyncio
import hashlib
import json
import os
import time
from datetime import datetime
from detector import detect
from rewriter import rewrite
from normalizer import normalize
import config

# redis 是可选依赖：REDIS_ENABLED 关闭或未安装时，整条缓存链路优雅降级为"不缓存"
try:
    import redis.asyncio as aioredis
except ImportError:
    aioredis = None

_log_lock = asyncio.Lock()

# Redis 连接复用：进程内只建一个连接池，避免每次请求新建连接
_redis_client = None
_redis_lock = asyncio.Lock()


def cache_key(violations: list[dict]) -> str:
    """
    根据 violations 内容生成稳定的缓存 key。

    设计要点：
    1. 只取每条 violation 的 word 字段——reason（如"语义匹配0.87"）带分数会抖动，
       纳入 key 会让本应命中的缓存频繁 miss，所以排除。
    2. 对 word 去重 + 排序，保证"违规词集合相同、顺序不同"时命中同一缓存。
    3. 用 SHA256 取十六进制摘要，配合前缀构成最终 key。
    """
    words = sorted({v["word"] for v in violations})
    raw = "\x00".join(words)
    digest = hashlib.sha256(raw.encode("utf-8")).hexdigest()
    return f"{config.REDIS_KEY_PREFIX}{digest}"


async def _get_redis():
    """
    懒加载 Redis 客户端。

    返回 None 表示当前不可用（未启用 / 未安装 / 连接失败），
    调用方据此走"不缓存"降级路径，绝不让缓存故障影响主流程。
    """
    if not config.REDIS_ENABLED:
        return None
    if aioredis is None:
        return None
    global _redis_client
    if _redis_client is not None:
        return _redis_client
    async with _redis_lock:
        # 双重检查：拿到锁后再确认一次，避免并发重复建连
        if _redis_client is not None:
            return _redis_client
        try:
            client = aioredis.Redis(
                host=config.REDIS_HOST,
                port=config.REDIS_PORT,
                db=config.REDIS_DB,
                password=config.REDIS_PASSWORD,
                decode_responses=True,
            )
            await client.ping()
            _redis_client = client
        except Exception as e:
            # 连接失败只打日志、不抛异常，主流程继续（无缓存）
            print(f"WARNING: Redis 连接失败，本次降级为不缓存：{e}")
            _redis_client = None
    return _redis_client


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
    查询改写策略缓存。

    命中返回 dict（形如 {"result": "<历史改写全文>"}），未命中或不可用返回 None。
    任何异常都吞掉并返回 None——缓存只是加速手段，绝不能因为它让主流程失败。
    """
    if not violations:
        return None
    client = await _get_redis()
    if client is None:
        return None
    try:
        key = cache_key(violations)
        raw = await client.get(key)
        if raw is None:
            return None
        return json.loads(raw)
    except Exception as e:
        print(f"WARNING: 读取缓存失败，按未命中处理：{e}")
        return None


async def set_cached_strategy(violations: list[dict], result_text: str) -> None:
    """
    写入改写策略缓存。

    以 violations 的内容 hash 为 key，存入本次改写全文，带 TTL（默认 7 天）。
    Redis 不可用或写入失败时静默跳过，不影响已经返回给用户的改写结果。
    """
    if not violations:
        return
    client = await _get_redis()
    if client is None:
        return
    try:
        key = cache_key(violations)
        value = json.dumps({"result": result_text}, ensure_ascii=False)
        await client.set(key, value, ex=config.REDIS_TTL_SECONDS)
    except Exception as e:
        print(f"WARNING: 写入缓存失败，跳过：{e}")


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
