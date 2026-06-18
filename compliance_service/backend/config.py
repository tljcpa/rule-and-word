import os
from dotenv import load_dotenv

load_dotenv()

EMBED_API_KEY = os.getenv("EMBED_API_KEY", "local")
EMBED_BASE_URL = os.getenv("EMBED_BASE_URL", "http://localhost:8001/v1")
EMBED_MODEL = os.getenv("EMBED_MODEL", "bge-m3")
EMBED_VECTOR_SIZE = int(os.getenv("EMBED_VECTOR_SIZE", "1024"))

PRIMARY_API_KEY = os.getenv("PRIMARY_API_KEY", "local")
PRIMARY_BASE_URL = os.getenv("PRIMARY_BASE_URL", "http://localhost:8002/v1")
PRIMARY_MODEL = os.getenv("PRIMARY_MODEL", "Qwen2.5-7B-Instruct-GPTQ-Int4")

FALLBACK_API_KEY = os.getenv("FALLBACK_API_KEY", "local")
FALLBACK_BASE_URL = os.getenv("FALLBACK_BASE_URL", "http://localhost:8002/v1")
FALLBACK_MODEL = os.getenv("FALLBACK_MODEL", "Qwen2.5-7B-Instruct-GPTQ-Int4")

QDRANT_HOST = os.getenv("QDRANT_HOST", "localhost")
QDRANT_PORT = int(os.getenv("QDRANT_PORT", "6333"))

SENSITIVE_THRESHOLD = float(os.getenv("SENSITIVE_THRESHOLD", "0.85"))
RULES_THRESHOLD = float(os.getenv("RULES_THRESHOLD", "0.75"))
SENSITIVE_TOPK = int(os.getenv("SENSITIVE_TOPK", "5"))
RULES_TOPK = int(os.getenv("RULES_TOPK", "2"))

TEMPERATURE = float(os.getenv("TEMPERATURE", "0.1"))
TOP_P = float(os.getenv("TOP_P", "0.5"))
MAX_TOKENS = int(os.getenv("MAX_TOKENS", "350"))
LLM_TIMEOUT = float(os.getenv("LLM_TIMEOUT", "30.0"))

REDIS_HOST = os.getenv("REDIS_HOST", "localhost")
REDIS_PORT = int(os.getenv("REDIS_PORT", "6379"))
REDIS_DB = int(os.getenv("REDIS_DB", "0"))
REDIS_PASSWORD = os.getenv("REDIS_PASSWORD", "") or None
REDIS_ENABLED = os.getenv("REDIS_ENABLED", "false").lower() == "true"
# 缓存 key 统一前缀，便于运维区分与批量清理
REDIS_KEY_PREFIX = os.getenv("REDIS_KEY_PREFIX", "rewrite_strategy:")
# 改写策略缓存的过期时间，默认 7 天（单位：秒）
REDIS_TTL_SECONDS = int(os.getenv("REDIS_TTL_SECONDS", str(7 * 24 * 3600)))

LOG_DIR = os.getenv("LOG_DIR", "logs")
