import asyncio
import time
import httpx
import ahocorasick
import config

_automaton: ahocorasick.Automaton | None = None

def build_automaton(words: list[str]) -> None:
    """
    构建 Aho-Corasick 自动机。
    词汇先经过 normalize（繁→简、NFKC、小写）再加入，
    保证与 exact_match 搜索的 normalized_text 一致。
    """
    global _automaton
    from normalizer import normalize
    A = ahocorasick.Automaton()
    added = 0
    for idx, word in enumerate(words):
        normed = normalize(word)
        if len(normed) >= 2:          # 过滤归一化后长度不足的词
            A.add_word(normed, (idx, normed))
            added += 1
    if added == 0:
        # 没有任何有效词：不调用 make_automaton（空 trie 调用后仍是 trie 状态，
        # iter 会抛 AttributeError）。直接置空，让 exact_match 走"无自动机"分支返回 []。
        _automaton = None
        return
    A.make_automaton()
    _automaton = A

def exact_match(normalized_text: str) -> list[dict]:
    if _automaton is None:
        return []
    seen: set[str] = set()
    results: list[dict] = []
    for _, (_, word) in _automaton.iter(normalized_text):
        if word not in seen:
            seen.add(word)
            results.append({"word": word, "reason": "精确匹配"})
    return results

async def embed_text(text: str) -> tuple[list[float], int]:
    t0 = time.time()
    async with httpx.AsyncClient(timeout=10.0) as client:
        resp = await client.post(
            f"{config.EMBED_BASE_URL}/embeddings",
            headers={"Authorization": f"Bearer {config.EMBED_API_KEY}"},
            json={"input": text, "model": config.EMBED_MODEL},
        )
        resp.raise_for_status()
    data = resp.json()
    vector = data["data"][0]["embedding"]
    elapsed = round((time.time() - t0) * 1000)
    return vector, elapsed

async def semantic_search(
    normalized_text: str,
) -> tuple[list[dict], list[dict], int]:
    from vector_store import search_sensitive, search_rules

    vector, embed_ms = await embed_text(normalized_text)
    sensitive_hits, rule_hits = await asyncio.gather(
        search_sensitive(vector),
        search_rules(vector),
    )
    sem_violations = [
        {"word": h["word"], "reason": f"语义匹配{h['score']:.2f}"}
        for h in sensitive_hits
    ]
    return sem_violations, rule_hits, embed_ms

async def detect(
    original_text: str,
    normalized_text: str,
) -> tuple[list[dict], list[dict], int]:
    exact_task = asyncio.to_thread(exact_match, normalized_text)
    semantic_task = semantic_search(normalized_text)

    exact_hits, (sem_hits, rules, embed_ms) = await asyncio.gather(
        exact_task, semantic_task
    )

    # Deduplicate violations by word
    seen: set[str] = set()
    violations: list[dict] = []
    for v in exact_hits + sem_hits:
        if v["word"] not in seen:
            seen.add(v["word"])
            violations.append(v)

    return violations, rules, embed_ms
