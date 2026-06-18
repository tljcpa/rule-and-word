import json

import pytest
import fakeredis.aioredis

import config
import pipeline
from pipeline import cache_key, get_cached_strategy, set_cached_strategy


# ---------------- cache_key 纯函数测试（不依赖 Redis） ----------------

def test_cache_key_stable():
    # 同样的 violations 多次生成同样的 key
    v = [{"word": "违规", "reason": "精确匹配"}]
    assert cache_key(v) == cache_key(v)


def test_cache_key_order_independent():
    # 违规词集合相同、顺序不同，应得到同一 key（key 内部对 word 排序）
    a = [{"word": "违规", "reason": "x"}, {"word": "敏感词", "reason": "y"}]
    b = [{"word": "敏感词", "reason": "y"}, {"word": "违规", "reason": "x"}]
    assert cache_key(a) == cache_key(b)


def test_cache_key_ignores_reason():
    # reason 带语义分数会抖动（如"语义匹配0.87"），不应进入 key，
    # 否则同一组词因分数微变就 miss。
    a = [{"word": "违规", "reason": "语义匹配0.87"}]
    b = [{"word": "违规", "reason": "精确匹配"}]
    assert cache_key(a) == cache_key(b)


def test_cache_key_different_words():
    a = [{"word": "违规", "reason": "x"}]
    b = [{"word": "敏感词", "reason": "x"}]
    assert cache_key(a) != cache_key(b)


def test_cache_key_dedups_words():
    # 重复词不影响 key
    a = [{"word": "违规", "reason": "x"}, {"word": "违规", "reason": "y"}]
    b = [{"word": "违规", "reason": "x"}]
    assert cache_key(a) == cache_key(b)


def test_cache_key_has_prefix():
    v = [{"word": "违规", "reason": "x"}]
    assert cache_key(v).startswith(config.REDIS_KEY_PREFIX)


# ---------------- Redis 读写测试（用 fakeredis 代替真实 Redis） ----------------

@pytest.fixture
def fake_redis(monkeypatch):
    """
    用 fakeredis 替换 pipeline 的全局 Redis 客户端，并开启 REDIS_ENABLED。
    这样 get/set_cached_strategy 走真实代码路径，但不连真实 Redis。
    """
    client = fakeredis.aioredis.FakeRedis(decode_responses=True)
    monkeypatch.setattr(config, "REDIS_ENABLED", True)
    monkeypatch.setattr(pipeline, "_redis_client", client)
    yield client


async def test_set_then_get_roundtrip(fake_redis):
    violations = [{"word": "违规", "reason": "精确匹配"}]
    await set_cached_strategy(violations, "改写后的合规文本")
    got = await get_cached_strategy(violations)
    assert got == {"result": "改写后的合规文本"}


async def test_get_miss_returns_none(fake_redis):
    violations = [{"word": "从未缓存过的词", "reason": "x"}]
    assert await get_cached_strategy(violations) is None


async def test_set_writes_ttl(fake_redis):
    violations = [{"word": "违规", "reason": "x"}]
    await set_cached_strategy(violations, "文本")
    key = cache_key(violations)
    ttl = await fake_redis.ttl(key)
    # TTL 应被设置成配置值（允许已流逝 1 秒的误差）
    assert 0 < ttl <= config.REDIS_TTL_SECONDS


async def test_value_is_valid_json(fake_redis):
    violations = [{"word": "违规", "reason": "x"}]
    await set_cached_strategy(violations, "文本内容")
    raw = await fake_redis.get(cache_key(violations))
    parsed = json.loads(raw)
    assert parsed["result"] == "文本内容"


async def test_chinese_not_escaped(fake_redis):
    # ensure_ascii=False，中文应原样存储而非 \uXXXX
    violations = [{"word": "违规", "reason": "x"}]
    await set_cached_strategy(violations, "中文内容")
    raw = await fake_redis.get(cache_key(violations))
    assert "中文内容" in raw


async def test_empty_violations_no_cache(fake_redis):
    # 没有违规词时不应读写缓存
    assert await get_cached_strategy([]) is None
    await set_cached_strategy([], "不该写")
    keys = await fake_redis.keys("*")
    assert keys == []


# ---------------- 降级路径测试（REDIS_ENABLED 关闭时不缓存） ----------------

async def test_disabled_get_returns_none(monkeypatch):
    monkeypatch.setattr(config, "REDIS_ENABLED", False)
    monkeypatch.setattr(pipeline, "_redis_client", None)
    violations = [{"word": "违规", "reason": "x"}]
    assert await get_cached_strategy(violations) is None


async def test_disabled_set_is_noop(monkeypatch):
    # 关闭时 set 不抛异常、安静返回
    monkeypatch.setattr(config, "REDIS_ENABLED", False)
    monkeypatch.setattr(pipeline, "_redis_client", None)
    violations = [{"word": "违规", "reason": "x"}]
    await set_cached_strategy(violations, "文本")  # 不应抛错


async def test_get_redis_returns_none_when_disabled(monkeypatch):
    # _get_redis 在关闭时返回 None（降级信号）
    monkeypatch.setattr(config, "REDIS_ENABLED", False)
    monkeypatch.setattr(pipeline, "_redis_client", None)
    assert await pipeline._get_redis() is None
