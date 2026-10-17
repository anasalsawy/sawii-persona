import asyncio
import json
import os
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from email.utils import format_datetime

import httpx

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from sawii_persona.config import ModelEndpoint, load_settings  # noqa: E402
from sawii_persona.llm import LLMError, OpenAICompatLLM, ProviderPacer  # noqa: E402
from sawii_persona.mind import Mind  # noqa: E402
from sawii_persona.subsystems import SubsystemHealth  # noqa: E402


def _success():
    return {"choices": [{"message": {"content": "ok"}, "finish_reason": "stop"}]}


class ProviderPacerTests(unittest.IsolatedAsyncioTestCase):
    async def test_models_with_same_account_share_serial_spacing(self):
        starts = []
        active = 0
        max_active = 0

        async def handler(request):
            nonlocal active, max_active
            starts.append(asyncio.get_running_loop().time())
            active += 1
            max_active = max(max_active, active)
            await asyncio.sleep(0.01)
            active -= 1
            return httpx.Response(200, json=_success())

        client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        pacer = ProviderPacer(min_interval_seconds=0.03, retry_attempts=0)
        endpoint_a = ModelEndpoint("https://provider.test/v1", "shared-key", "a")
        endpoint_b = ModelEndpoint("https://provider-alias.test/v1", "shared-key", "b")
        first = OpenAICompatLLM(endpoint_a, name="A", client=client, pacer=pacer)
        second = OpenAICompatLLM(endpoint_b, name="B", client=client, pacer=pacer)
        try:
            await asyncio.gather(
                first.chat([{"role": "user", "content": "one"}]),
                second.chat([{"role": "user", "content": "two"}]),
            )
        finally:
            await client.aclose()

        self.assertEqual(max_active, 1)
        self.assertEqual(len(starts), 2)
        self.assertGreaterEqual(starts[1] - starts[0], 0.025)

    async def test_background_lobes_can_call_during_a_streaming_response(self):
        release_stream = asyncio.Event()
        started = []

        class HeldStream(httpx.AsyncByteStream):
            async def __aiter__(self):
                yield b'data: {"choices":[{"delta":{"content":"A is speaking"}}]}\n\n'
                await release_stream.wait()
                yield b"data: [DONE]\n\n"

        async def handler(request):
            model = json.loads(request.content)["model"]
            started.append(model)
            if model == "a":
                return httpx.Response(200, stream=HeldStream())
            return httpx.Response(200, json=_success())

        client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        pacer = ProviderPacer(min_interval_seconds=0, max_concurrent_requests=3)
        a = OpenAICompatLLM(ModelEndpoint("https://provider.test/v1", "shared-key", "a"),
                            client=client, pacer=pacer)
        a.on_health = SubsystemHealth("a_llm")
        b = OpenAICompatLLM(ModelEndpoint("https://provider.test/v1", "shared-key", "b"),
                            client=client, pacer=pacer)
        c = OpenAICompatLLM(ModelEndpoint("https://provider.test/v1", "shared-key", "c"),
                            client=client, pacer=pacer)
        stream = a.stream([{"role": "user", "content": "talk"}])
        try:
            first = await anext(stream)
            self.assertEqual(first["text"], "A is speaking")
            self.assertEqual(a.on_health.snapshot()["active_count"], 1)
            b_result, c_result = await asyncio.wait_for(asyncio.gather(
                b.chat([{"role": "user", "content": "memory"}]),
                c.chat([{"role": "user", "content": "gut read"}]),
            ), timeout=0.5)
            self.assertEqual((b_result.text, c_result.text), ("ok", "ok"))
            self.assertCountEqual(started, ["a", "b", "c"])
            release_stream.set()
            async for _ in stream:
                pass
            self.assertEqual(a.on_health.snapshot()["active_count"], 0)
            self.assertEqual(a.on_health.snapshot()["status"], "alive")
        finally:
            release_stream.set()
            await stream.aclose()
            await client.aclose()

    async def test_chat_retries_rate_limit_and_honors_retry_after(self):
        calls = 0

        async def handler(request):
            nonlocal calls
            calls += 1
            if calls == 1:
                return httpx.Response(429, headers={"Retry-After": "0"}, json={"error": "busy"})
            return httpx.Response(200, json=_success())

        client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        llm = OpenAICompatLLM(
            ModelEndpoint("https://provider.test/v1", "k", "m"), client=client,
            pacer=ProviderPacer(min_interval_seconds=0, retry_attempts=1))
        try:
            result = await llm.chat([{"role": "user", "content": "hi"}])
        finally:
            await client.aclose()

        self.assertEqual(result.text, "ok")
        self.assertEqual(calls, 2)

    async def test_rate_limit_cooldown_blocks_other_models_on_same_key(self):
        starts = []
        calls = {}

        async def handler(request):
            model = json.loads(request.content)["model"]
            calls[model] = calls.get(model, 0) + 1
            starts.append(asyncio.get_running_loop().time())
            if model == "a" and calls[model] == 1:
                await asyncio.sleep(0.01)
                return httpx.Response(429, headers={"Retry-After": "0.06"}, json={"error": "busy"})
            return httpx.Response(200, json=_success())

        client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        pacer = ProviderPacer(min_interval_seconds=0, retry_attempts=0)
        first = OpenAICompatLLM(
            ModelEndpoint("https://provider.test/v1", "shared-key", "a"), client=client, pacer=pacer)
        second = OpenAICompatLLM(
            ModelEndpoint("https://provider.test/v1", "shared-key", "b"), client=client, pacer=pacer)
        try:
            with self.assertRaisesRegex(LLMError, "HTTP 429"):
                await first.chat([{"role": "user", "content": "one"}])
            await second.chat([{"role": "user", "content": "two"}])
        finally:
            await client.aclose()

        self.assertEqual(len(starts), 2)
        self.assertGreaterEqual(starts[1] - starts[0], 0.05)
        self.assertEqual(calls, {"a": 1, "b": 1})

    async def test_streaming_and_embedding_use_same_retry_path(self):
        calls = {"chat": 0, "embed": 0}

        async def handler(request):
            if request.url.path.endswith("/embeddings"):
                calls["embed"] += 1
                if calls["embed"] == 1:
                    return httpx.Response(429, headers={"Retry-After": "0"}, json={"error": "busy"})
                return httpx.Response(200, json={"data": [{"index": 0, "embedding": [0.1]}]})
            calls["chat"] += 1
            if calls["chat"] == 1:
                return httpx.Response(503, headers={"Retry-After": "0"}, json={"error": "busy"})
            return httpx.Response(
                200,
                text='data: {"choices":[{"delta":{"content":"ok"}}]}\n\ndata: [DONE]\n\n',
                headers={"content-type": "text/event-stream"},
            )

        client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        llm = OpenAICompatLLM(
            ModelEndpoint("https://provider.test/v1", "k", "m"), client=client,
            pacer=ProviderPacer(min_interval_seconds=0, retry_attempts=1))
        try:
            chunks = [chunk async for chunk in llm.stream([{"role": "user", "content": "hi"}])]
            embeddings = await llm.embed(["hi"])
        finally:
            await client.aclose()

        self.assertEqual([chunk["text"] for chunk in chunks if chunk["type"] == "text"], ["ok"])
        self.assertEqual(embeddings, [[0.1]])
        self.assertEqual(calls, {"chat": 2, "embed": 2})

    def test_retry_after_supports_seconds_and_http_dates(self):
        self.assertEqual(ProviderPacer.retry_delay({"retry-after": "4"}, 0), 4)
        retry_at = datetime.now(timezone.utc) + timedelta(seconds=20)
        delay = ProviderPacer.retry_delay({"retry-after": format_datetime(retry_at, usegmt=True)}, 0)
        self.assertGreaterEqual(delay, 18)
        self.assertLessEqual(delay, 20)
        self.assertIsNone(ProviderPacer.retry_delay({"retry-after": "31"}, 0))

    async def test_exhausted_rate_limit_remains_an_explicit_error(self):
        calls = 0

        async def handler(request):
            nonlocal calls
            calls += 1
            return httpx.Response(429, json={"error": "daily limit exhausted"})

        client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        llm = OpenAICompatLLM(
            ModelEndpoint("https://provider.test/v1", "k", "m"), client=client,
            pacer=ProviderPacer(min_interval_seconds=0, retry_attempts=0))
        try:
            with self.assertRaisesRegex(LLMError, "HTTP 429"):
                await llm.chat([{"role": "user", "content": "hi"}])
        finally:
            await client.aclose()

        self.assertEqual(calls, 1)

    async def test_retry_after_beyond_limit_surfaces_without_waiting(self):
        calls = 0

        async def handler(request):
            nonlocal calls
            calls += 1
            return httpx.Response(429, headers={"Retry-After": "60"}, json={"error": "daily quota"})

        client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        llm = OpenAICompatLLM(
            ModelEndpoint("https://provider.test/v1", "k", "m"), client=client,
            pacer=ProviderPacer(min_interval_seconds=0, retry_attempts=2, max_retry_wait_seconds=1))
        try:
            with self.assertRaisesRegex(LLMError, "HTTP 429"):
                await llm.chat([{"role": "user", "content": "hi"}])
        finally:
            await client.aclose()

        self.assertEqual(calls, 1)
        with self.assertRaisesRegex(LLMError, "cooldown active"):
            await llm.chat([{"role": "user", "content": "another request"}])
        self.assertEqual(calls, 1)

    async def test_configured_token_and_daily_caps_fail_explicitly(self):
        pacer = ProviderPacer(
            min_interval_seconds=0, retry_attempts=0,
            tokens_per_minute=8, requests_per_day=1)
        async with pacer.request("provider", estimated_tokens=8):
            pass

        with self.assertRaisesRegex(LLMError, "daily request safety limit"):
            async with pacer.request("provider", estimated_tokens=1):
                self.fail("daily quota should block before sending")

        other = ProviderPacer(min_interval_seconds=0, tokens_per_minute=8)
        with self.assertRaisesRegex(LLMError, "estimate exceeds"):
            async with other.request("provider", estimated_tokens=9):
                self.fail("oversized request should block before sending")

    async def test_chat_reserves_prompt_and_output_token_estimate(self):
        calls = 0

        async def handler(request):
            nonlocal calls
            calls += 1
            return httpx.Response(200, json=_success())

        client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        llm = OpenAICompatLLM(
            ModelEndpoint("https://provider.test/v1", "k", "m"), client=client,
            pacer=ProviderPacer(min_interval_seconds=0, tokens_per_minute=32))
        try:
            with self.assertRaisesRegex(LLMError, "estimate exceeds"):
                await llm.chat([{"role": "user", "content": "hi"}], max_tokens=32)
        finally:
            await client.aclose()

        self.assertEqual(calls, 0)

    def test_headers_are_provider_neutral_except_for_featherless_compatibility(self):
        generic = OpenAICompatLLM(ModelEndpoint("https://api.groq.com/openai/v1", "k", "m"))
        featherless = OpenAICompatLLM(ModelEndpoint("https://api.featherless.ai/v1", "k", "m"))

        self.assertEqual(generic._headers()["User-Agent"], "sawii-persona/0.1")
        self.assertNotIn("Origin", generic._headers())
        self.assertEqual(featherless._headers()["Origin"], "https://featherless.ai")

    def test_free_mode_settings_are_opt_in_and_configurable(self):
        with tempfile.TemporaryDirectory() as data_dir:
            defaults = load_settings(data_dir=data_dir, db_path=os.path.join(data_dir, "default.db"))
            free = load_settings(data_dir=data_dir, db_path=os.path.join(data_dir, "free.db"),
                                 free_mode=True, free_request_interval_seconds=3.0, free_retry_attempts=1,
                                 free_max_retry_wait_seconds=5.0, free_requests_per_minute=10,
                                 free_max_concurrent_requests=2, free_tokens_per_minute=7000,
                                 free_requests_per_day=900)

        self.assertTrue(defaults.free_mode)
        self.assertEqual(defaults.free_requests_per_minute, 10)
        self.assertTrue(free.free_mode)
        self.assertEqual(free.free_request_interval_seconds, 3.0)
        self.assertEqual(free.free_retry_attempts, 1)
        self.assertEqual(free.free_max_retry_wait_seconds, 5.0)
        self.assertEqual(free.free_requests_per_minute, 10)
        self.assertEqual(free.free_max_concurrent_requests, 2)
        self.assertEqual(free.free_tokens_per_minute, 7000)
        self.assertEqual(free.free_requests_per_day, 900)

    def test_shared_provider_env_settings_can_replace_featherless_defaults(self):
        names = ("SAWII_PROVIDER_BASE_URL", "SAWII_PROVIDER_API_KEY", "SAWII_FEATHERLESS_API_KEY")
        previous = {name: os.environ.get(name) for name in names}
        os.environ["SAWII_PROVIDER_BASE_URL"] = "https://api.example.test/v1"
        os.environ["SAWII_PROVIDER_API_KEY"] = "provider-key"
        os.environ["SAWII_FEATHERLESS_API_KEY"] = "featherless-key"
        try:
            with tempfile.TemporaryDirectory() as data_dir:
                settings = load_settings(data_dir=data_dir, db_path=os.path.join(data_dir, "provider.db"))
        finally:
            for name, value in previous.items():
                if value is None:
                    os.environ.pop(name, None)
                else:
                    os.environ[name] = value

        for endpoint in (settings.a, settings.b, settings.c, settings.embed):
            self.assertEqual(endpoint.base_url, "https://api.example.test/v1")
            self.assertEqual(endpoint.api_key, "provider-key")

    def test_featherless_key_is_not_sent_to_a_different_provider(self):
        names = ("SAWII_PROVIDER_BASE_URL", "SAWII_PROVIDER_API_KEY", "SAWII_FEATHERLESS_API_KEY")
        previous = {name: os.environ.get(name) for name in names}
        os.environ["SAWII_PROVIDER_BASE_URL"] = "https://api.other-provider.test/v1"
        os.environ.pop("SAWII_PROVIDER_API_KEY", None)
        os.environ["SAWII_FEATHERLESS_API_KEY"] = "featherless-secret"
        try:
            with tempfile.TemporaryDirectory() as data_dir:
                settings = load_settings(data_dir=data_dir, db_path=os.path.join(data_dir, "no-leak.db"))
        finally:
            for name, value in previous.items():
                if value is None:
                    os.environ.pop(name, None)
                else:
                    os.environ[name] = value

        self.assertEqual(settings.a.base_url, "https://api.other-provider.test/v1")
        self.assertEqual(settings.a.api_key, "")

    def test_distinct_lobes_can_use_three_distinct_provider_accounts(self):
        values = {
            "SAWII_A_BASE_URL": "https://api.groq.com/openai/v1",
            "SAWII_A_API_KEY": "groq-test-key",
            "SAWII_A_MODEL": "openai/gpt-oss-120b",
            "SAWII_B_BASE_URL": "https://generativelanguage.googleapis.com/v1beta/openai",
            "SAWII_B_API_KEY": "gemini-test-key",
            "SAWII_B_MODEL": "gemini-2.5-flash",
            "SAWII_C_BASE_URL": "https://openrouter.ai/api/v1",
            "SAWII_C_API_KEY": "openrouter-test-key",
            "SAWII_C_MODEL": "openrouter/free",
        }
        previous = {name: os.environ.get(name) for name in values}
        os.environ.update(values)
        try:
            with tempfile.TemporaryDirectory() as data_dir:
                settings = load_settings(data_dir=data_dir, db_path=os.path.join(data_dir, "multi.db"),
                                         embed_enabled=False)
                mind = Mind(settings)
                try:
                    self.assertEqual(mind.a.model, "openai/gpt-oss-120b")
                    self.assertEqual(mind.b.model, "gemini-2.5-flash")
                    self.assertEqual(mind.c.model, "openrouter/free")
                    self.assertEqual(len({mind.a._provider_key, mind.b._provider_key, mind.c._provider_key}), 3)
                    self.assertIs(mind.a.pacer, mind.b.pacer)
                finally:
                    mind.store.close()
        finally:
            for name, value in previous.items():
                if value is None:
                    os.environ.pop(name, None)
                else:
                    os.environ[name] = value

    def test_openrouter_key_selects_c_observer_without_rerouting_a_and_b(self):
        previous = os.environ.get("OPENROUTER_API_KEY")
        os.environ["OPENROUTER_API_KEY"] = "openrouter-test-key"
        try:
            with tempfile.TemporaryDirectory() as data_dir:
                settings = load_settings(data_dir=data_dir, db_path=os.path.join(data_dir, "auto-openrouter.db"))
        finally:
            if previous is None:
                os.environ.pop("OPENROUTER_API_KEY", None)
            else:
                os.environ["OPENROUTER_API_KEY"] = previous

        self.assertEqual(settings.a.base_url, "https://api.featherless.ai/v1")
        self.assertEqual(settings.b.base_url, "https://api.featherless.ai/v1")
        self.assertEqual(settings.c.base_url, "https://openrouter.ai/api/v1")
        self.assertEqual(settings.c.model, "openrouter/free")
        self.assertEqual(settings.c.api_key, "openrouter-test-key")
        self.assertNotEqual(settings.a.api_key, settings.c.api_key)

    def test_free_mode_wires_one_provider_pacer_across_lobes(self):
        with tempfile.TemporaryDirectory() as data_dir:
            settings = load_settings(data_dir=data_dir, db_path=os.path.join(data_dir, "mind.db"),
                                     free_mode=True)
            mind = Mind(settings)
            try:
                self.assertIsNotNone(mind.provider_pacer)
                self.assertIs(mind.a.pacer, mind.provider_pacer)
                self.assertIs(mind.b.pacer, mind.provider_pacer)
                self.assertIs(mind.c.pacer, mind.provider_pacer)
                self.assertEqual(mind.provider_pacer.max_concurrent_requests, 3)
            finally:
                mind.store.close()

    def test_free_mode_suppresses_optional_consultants_by_default(self):
        with tempfile.TemporaryDirectory() as data_dir:
            settings = load_settings(data_dir=data_dir, db_path=os.path.join(data_dir, "consult.db"),
                                     free_mode=True, consult_models=["optional/model"])
            mind = Mind(settings)
            try:
                self.assertEqual(mind.consultants, {})
            finally:
                mind.store.close()

            settings = load_settings(data_dir=data_dir, db_path=os.path.join(data_dir, "consult-enabled.db"),
                                     free_mode=True, free_mode_consultants=True,
                                     consult_models=["optional/model"])
            mind = Mind(settings)
            try:
                self.assertEqual(list(mind.consultants), ["model"])
                self.assertIs(mind.consultants["model"].pacer, mind.provider_pacer)
            finally:
                mind.store.close()

    def test_remote_embeddings_can_be_disabled_for_chat_only_providers(self):
        with tempfile.TemporaryDirectory() as data_dir:
            settings = load_settings(data_dir=data_dir, db_path=os.path.join(data_dir, "no-embed.db"),
                                     embed_enabled=False, embed=ModelEndpoint(
                                         "https://provider.test/v1", "k", "not-an-embedding-model"))
            mind = Mind(settings)
            try:
                self.assertIsNone(mind.embedder.llm)
                self.assertEqual(mind.embedder.model, "local-hash-512")
            finally:
                mind.store.close()


if __name__ == "__main__":
    unittest.main()
