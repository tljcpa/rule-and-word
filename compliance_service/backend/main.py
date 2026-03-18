import json
import os
import sys
from contextlib import asynccontextmanager
from collections import Counter

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

sys.path.insert(0, os.path.dirname(__file__))

import config
from pipeline import process
from vector_store import init_collections, get_all_sensitive_words
from detector import build_automaton


@asynccontextmanager
async def lifespan(app: FastAPI):
    await init_collections()
    words = await get_all_sensitive_words()
    build_automaton(words)
    print(f"服务启动完成，已加载{len(words)}个敏感词")
    yield


app = FastAPI(lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


class ProcessRequest(BaseModel):
    text: str = Field(..., min_length=1, max_length=5000)
    request_id: str = ""


@app.post("/process")
async def process_text(req: ProcessRequest):
    return await process(req.text, req.request_id)


@app.get("/stats")
async def stats():
    log_path = os.path.join(config.LOG_DIR, "requests.jsonl")
    if not os.path.exists(log_path):
        return {"error": "暂无日志数据"}

    records = []
    with open(log_path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                records.append(json.loads(line))

    if not records:
        return {"error": "暂无日志数据"}

    total = len(records)
    hits = sum(1 for r in records if r.get("hit"))
    fallbacks = sum(1 for r in records if r.get("used_fallback"))

    def percentile(values, p):
        if not values:
            return 0
        sorted_v = sorted(values)
        idx = int(len(sorted_v) * p / 100)
        return sorted_v[min(idx, len(sorted_v) - 1)]

    def lat_stats(field):
        vals = [r.get(field, 0) for r in records]
        return {
            "avg": round(sum(vals) / len(vals)),
            "p50": percentile(vals, 50),
            "p99": percentile(vals, 99),
        }

    violation_words = []
    for r in records:
        for v in r.get("violations", []):
            violation_words.append(v["word"])
    top10 = [{"word": w, "count": c} for w, c in Counter(violation_words).most_common(10)]

    last = records[-1]
    return {
        "total_requests": total,
        "hit_rate": round(hits / total, 4),
        "fallback_rate": round(fallbacks / total, 4),
        "latency": {
            "normalize": lat_stats("normalize_ms"),
            "embed": lat_stats("embed_ms"),
            "detect": lat_stats("detect_ms"),
            "rewrite": lat_stats("rewrite_ms"),
            "total": lat_stats("total_ms"),
        },
        "models": {
            "embed_model": last.get("embed_model"),
            "embed_base_url": last.get("embed_base_url"),
            "primary_model": last.get("primary_model"),
            "primary_base_url": last.get("primary_base_url"),
            "fallback_model": last.get("fallback_model"),
        },
        "current_config": {
            "temperature": config.TEMPERATURE,
            "top_p": config.TOP_P,
            "max_tokens": config.MAX_TOKENS,
            "llm_timeout": config.LLM_TIMEOUT,
            "sensitive_threshold": config.SENSITIVE_THRESHOLD,
            "rules_threshold": config.RULES_THRESHOLD,
            "sensitive_topk": config.SENSITIVE_TOPK,
            "rules_topk": config.RULES_TOPK,
        },
        "violations_top10": top10,
    }


@app.get("/health")
async def health():
    from detector import _automaton
    word_count = len(_automaton) if _automaton else 0
    try:
        from vector_store import get_client
        client = get_client()
        await client.get_collections()
        qdrant_status = "connected"
    except Exception:
        qdrant_status = "error"
    return {"status": "ok", "qdrant": qdrant_status, "automaton_words": word_count}
