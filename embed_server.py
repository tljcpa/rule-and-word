import asyncio
import time
from contextlib import asynccontextmanager

import torch
from fastapi import FastAPI
from pydantic import BaseModel
from sentence_transformers import SentenceTransformer
import uvicorn

_model: SentenceTransformer | None = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    global _model
    t0 = time.time()
    _model = SentenceTransformer(
        "/root/compliance_project/models/bge-m3",
        device="cuda",
    )
    elapsed = round((time.time() - t0) * 1000)
    mem = torch.cuda.memory_allocated() / 1024 ** 3
    print(f"bge-m3 加载完成，耗时{elapsed}ms，显存占用{mem:.2f}GB")
    yield


app = FastAPI(lifespan=lifespan)


class EmbedRequest(BaseModel):
    input: str | list[str]
    model: str = "bge-m3"


@app.post("/v1/embeddings")
async def embeddings(req: EmbedRequest):
    t0 = time.time()
    texts = [req.input] if isinstance(req.input, str) else req.input
    vectors = await asyncio.to_thread(
        lambda: _model.encode(texts, normalize_embeddings=True).tolist()
    )
    elapsed = round((time.time() - t0) * 1000)
    print(f"[embed] {len(texts)}条 耗时{elapsed}ms")
    return {
        "object": "list",
        "data": [
            {"object": "embedding", "index": i, "embedding": v}
            for i, v in enumerate(vectors)
        ],
        "model": req.model,
    }


@app.get("/health")
async def health():
    if _model is None:
        from fastapi import HTTPException
        raise HTTPException(status_code=503, detail="model not loaded")
    return {"status": "ok"}


if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8001)
