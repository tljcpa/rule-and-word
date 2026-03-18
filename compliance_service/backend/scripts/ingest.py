#!/usr/bin/env python3
import asyncio
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import httpx
import pandas as pd

# ==================== 配置区 ====================
SENSITIVE_EXCEL = "/root/compliance_project/data/敏感词表.xlsx"
SENSITIVE_COL = "敏感词"        # 实际列名，按需修改
RULES_EXCEL = "/root/compliance_project/data/平台规则表.xlsx"
RULES_COL = "规则内容"          # 实际列名，按需修改
BATCH_SIZE = 32
# ===============================================

import config

EMBED_URL = f"{config.EMBED_BASE_URL}/embeddings"
EMBED_MODEL = config.EMBED_MODEL
from vector_store import init_collections, upsert_sensitive_words, upsert_platform_rules


async def embed_batch(texts: list[str]) -> list[list[float]]:
    async with httpx.AsyncClient(timeout=60.0) as client:
        resp = await client.post(
            EMBED_URL,
            json={"input": texts, "model": EMBED_MODEL},
        )
        resp.raise_for_status()
    data = resp.json()
    return [item["embedding"] for item in data["data"]]


async def ingest_sensitive_words():
    df = pd.read_excel(SENSITIVE_EXCEL)
    if SENSITIVE_COL not in df.columns:
        raise ValueError(f"列'{SENSITIVE_COL}'不存在，可用列：{list(df.columns)}")

    raw_words: list[str] = []
    for cell in df[SENSITIVE_COL].dropna():
        for w in str(cell).split("、"):
            w = w.strip()
            if len(w) >= 2:
                raw_words.append(w)

    words = list(set(raw_words))
    print(f"敏感词去重后共 {len(words)} 条")

    total = 0
    for i in range(0, len(words), BATCH_SIZE):
        batch = words[i: i + BATCH_SIZE]
        vectors = await embed_batch(batch)
        records = [
            {"word": w, "vector": v, "category": ""}
            for w, v in zip(batch, vectors)
        ]
        await upsert_sensitive_words(records)
        total += len(batch)
        print(f"  已写入敏感词 {total}/{len(words)}")

    print(f"敏感词导入完成，共 {total} 条")


async def ingest_platform_rules():
    df = pd.read_excel(RULES_EXCEL)
    if RULES_COL not in df.columns:
        raise ValueError(f"列'{RULES_COL}'不存在，可用列：{list(df.columns)}")

    rules_text: list[str] = [
        str(cell).strip() for cell in df[RULES_COL].dropna() if str(cell).strip()
    ]
    print(f"平台规则共 {len(rules_text)} 条")

    total = 0
    for i in range(0, len(rules_text), BATCH_SIZE):
        batch = rules_text[i: i + BATCH_SIZE]
        vectors = await embed_batch(batch)
        records = [
            {
                "rule_id": str(i + j),
                "summary": text[:100],
                "full_text": text,
                "vector": v,
            }
            for j, (text, v) in enumerate(zip(batch, vectors))
        ]
        await upsert_platform_rules(records)
        total += len(batch)
        print(f"  已写入规则 {total}/{len(rules_text)}")

    print(f"平台规则导入完成，共 {total} 条")


async def main():
    await init_collections()
    await ingest_sensitive_words()
    await ingest_platform_rules()


if __name__ == "__main__":
    asyncio.run(main())
