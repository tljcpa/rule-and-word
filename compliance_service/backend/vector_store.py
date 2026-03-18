from qdrant_client import AsyncQdrantClient
from qdrant_client.models import (
    Distance,
    VectorParams,
    PointStruct,
)
import uuid
import config

_client: AsyncQdrantClient | None = None

def get_client() -> AsyncQdrantClient:
    global _client
    if _client is None:
        _client = AsyncQdrantClient(host=config.QDRANT_HOST, port=config.QDRANT_PORT)
    return _client

async def init_collections() -> None:
    client = get_client()
    existing = {c.name for c in (await client.get_collections()).collections}

    if "sensitive_words" not in existing:
        await client.create_collection(
            collection_name="sensitive_words",
            vectors_config=VectorParams(
                size=config.EMBED_VECTOR_SIZE,
                distance=Distance.COSINE,
            ),
        )

    if "platform_rules" not in existing:
        await client.create_collection(
            collection_name="platform_rules",
            vectors_config=VectorParams(
                size=config.EMBED_VECTOR_SIZE,
                distance=Distance.COSINE,
            ),
        )

async def upsert_sensitive_words(words: list[dict]) -> None:
    client = get_client()
    points = [
        PointStruct(
            id=str(uuid.uuid4()),
            vector=w["vector"],
            payload={"word": w["word"], "category": w.get("category", "")},
        )
        for w in words
    ]
    await client.upsert(collection_name="sensitive_words", points=points)

async def upsert_platform_rules(rules: list[dict]) -> None:
    client = get_client()
    points = [
        PointStruct(
            id=str(uuid.uuid4()),
            vector=r["vector"],
            payload={
                "rule_id": r.get("rule_id", ""),
                "summary": r["summary"],
                "full_text": r.get("full_text", r["summary"]),
            },
        )
        for r in rules
    ]
    await client.upsert(collection_name="platform_rules", points=points)

async def search_sensitive(vector: list[float]) -> list[dict]:
    client = get_client()
    results = await client.search(
        collection_name="sensitive_words",
        query_vector=vector,
        limit=config.SENSITIVE_TOPK,
        score_threshold=config.SENSITIVE_THRESHOLD,
    )
    return [{"word": r.payload["word"], "score": r.score} for r in results]

async def search_rules(vector: list[float]) -> list[dict]:
    client = get_client()
    results = await client.search(
        collection_name="platform_rules",
        query_vector=vector,
        limit=config.RULES_TOPK,
        score_threshold=config.RULES_THRESHOLD,
    )
    return [{"summary": r.payload["summary"]} for r in results]

async def get_all_sensitive_words() -> list[str]:
    client = get_client()
    words: list[str] = []
    offset = None
    while True:
        records, offset = await client.scroll(
            collection_name="sensitive_words",
            limit=1000,
            offset=offset,
            with_payload=True,
            with_vectors=False,
        )
        words.extend(r.payload["word"] for r in records)
        if offset is None:
            break
    return words
