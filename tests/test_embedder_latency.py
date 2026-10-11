import asyncio
import unittest

from sawii_persona.embedder import HASH_MODEL, Embedder


class _CountingLLM:
    model = "remote-test"

    def __init__(self, fail=False, delay=0.0):
        self.calls = 0
        self.fail = fail
        self.delay = delay

    async def embed(self, texts):
        self.calls += 1
        if self.delay:
            await asyncio.sleep(self.delay)
        if self.fail:
            raise RuntimeError("boom")
        return [[1.0, 0.0, 0.0] for _ in texts]


class EmbedderLatencyTests(unittest.IsolatedAsyncioTestCase):
    async def test_repeated_cue_hits_remote_once(self):
        llm = _CountingLLM(delay=0.01)
        emb = Embedder(llm)
        a, b = await asyncio.gather(emb.embed_one("same cue"), emb.embed_one("same cue"))
        c = await emb.embed_one("same cue")
        self.assertEqual(llm.calls, 1)
        self.assertEqual(a, b)
        self.assertEqual(a, c)
        self.assertNotEqual(a[0], HASH_MODEL)

    async def test_failure_opens_circuit_and_skips_remote(self):
        llm = _CountingLLM(fail=True)
        emb = Embedder(llm)
        m1, _ = await emb.embed_one("first")
        m2, _ = await emb.embed_one("second")
        self.assertEqual((m1, m2), (HASH_MODEL, HASH_MODEL))
        self.assertEqual(llm.calls, 1)
        emb._skip_remote_until = 0.0
        await emb.embed_one("third")
        self.assertEqual(llm.calls, 2)

    async def test_hash_fallback_is_not_cached(self):
        llm = _CountingLLM(fail=True)
        emb = Embedder(llm)
        await emb.embed_one("cue")
        llm.fail = False
        emb._skip_remote_until = 0.0
        model, _ = await emb.embed_one("cue")
        self.assertNotEqual(model, HASH_MODEL)


if __name__ == "__main__":
    unittest.main()
