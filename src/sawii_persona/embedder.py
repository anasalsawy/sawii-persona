"""Embeddings for context broadening.

Primary: a real semantic embedder (default Featherless Qwen3-Embedding-8B).
Fallback: a deterministic local feature-hash embedder so the system never
blocks on an outage. Every stored vector is tagged with the model that made
it; vectors from a different model are treated as absent, never compared.
"""
from __future__ import annotations

import asyncio
import hashlib
import logging
import re

import numpy as np

log = logging.getLogger(__name__)

HASH_DIM = 512
HASH_MODEL = "local-hash-512"
_WORD = re.compile(r"[a-z0-9؀-ۿ']+")


def _bucket(token: str) -> tuple[int, float]:
    h = hashlib.blake2b(token.encode(), digest_size=8).digest()
    idx = int.from_bytes(h[:4], "little") % HASH_DIM
    sign = 1.0 if h[4] & 1 else -1.0
    return idx, sign


def hash_embed(text: str) -> list[float]:
    vec = np.zeros(HASH_DIM, dtype=np.float32)
    words = _WORD.findall((text or "").lower())
    for w in words:
        i, s = _bucket("w:" + w)
        vec[i] += s
        if len(w) > 4:  # crude stemming: shared prefixes
            i, s = _bucket("p:" + w[:5])
            vec[i] += 0.5 * s
        padded = f"#{w}#"
        for k in range(len(padded) - 2):
            i, s = _bucket("c:" + padded[k:k + 3])
            vec[i] += 0.25 * s
    n = float(np.linalg.norm(vec))
    return (vec / n).tolist() if n else vec.tolist()


def to_blob(vec: list[float] | None) -> bytes | None:
    if vec is None:
        return None
    return np.asarray(vec, dtype=np.float32).tobytes()


def from_blob(blob: bytes | None) -> np.ndarray | None:
    if not blob:
        return None
    return np.frombuffer(blob, dtype=np.float32)


def cosine(a, b) -> float:
    if a is None or b is None:
        return 0.0
    a = np.asarray(a, dtype=np.float32)
    b = np.asarray(b, dtype=np.float32)
    if a.shape != b.shape:
        return 0.0
    na, nb = float(np.linalg.norm(a)), float(np.linalg.norm(b))
    if not na or not nb:
        return 0.0
    return float(np.dot(a, b) / (na * nb))


def cosine_matrix(query: np.ndarray, matrix: np.ndarray) -> np.ndarray:
    if matrix.size == 0:
        return np.zeros(0, dtype=np.float32)
    q = query / (np.linalg.norm(query) or 1.0)
    norms = np.linalg.norm(matrix, axis=1)
    norms[norms == 0] = 1.0
    return (matrix @ q) / norms


class Embedder:
    """Remote semantic embedder with honest local fallback."""

    def __init__(self, llm=None, *, timeout: float = 8.0):
        self.llm = llm
        self.timeout = timeout
        self._remote_ok = llm is not None

    @property
    def model(self) -> str:
        if self.llm is not None and self._remote_ok:
            return getattr(self.llm, "model", "remote")
        return HASH_MODEL

    async def embed(self, texts: list[str]) -> tuple[str, list[list[float]]]:
        texts = [t[:8000] for t in texts]
        if self.llm is not None:
            try:
                vecs = await asyncio.wait_for(self.llm.embed(texts), self.timeout)
                if vecs and len(vecs) == len(texts):
                    self._remote_ok = True
                    return getattr(self.llm, "model", "remote"), vecs
            except Exception as exc:  # outage: degrade, never block a turn
                log.warning("embedder fallback to local hash: %s", exc)
        return HASH_MODEL, [hash_embed(t) for t in texts]

    async def embed_one(self, text: str) -> tuple[str, list[float]]:
        model, vecs = await self.embed([text])
        return model, vecs[0]
