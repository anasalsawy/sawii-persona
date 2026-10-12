"""Locks in the latency fix: reasoning models must be asked to skip thinking.

Measured on the live provider (Featherless, Qwen3.8-Flash-Next): a ~130-word
reply took 26s with thinking on and 3.3s with it off. These tests make sure the
hint is sent by default, can be turned off, and that the provider-fallback path
still exists (so a provider that rejects the field is not fatal).
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from sawii_persona.config import ModelEndpoint, load_settings  # noqa: E402
from sawii_persona.llm import OpenAICompatLLM  # noqa: E402


def _ep(no_think: bool) -> ModelEndpoint:
    return ModelEndpoint(base_url="https://example.test/v1", api_key="k", model="m", no_think=no_think)


class NoThinkTests(unittest.TestCase):
    def test_body_asks_the_model_to_skip_thinking_by_default(self):
        llm = OpenAICompatLLM(_ep(True), name="A")
        body = llm._body([{"role": "user", "content": "hi"}], None, 0.7, 128, stream=True)
        self.assertEqual(body.get("chat_template_kwargs"), {"enable_thinking": False})

    def test_body_omits_the_hint_when_disabled(self):
        llm = OpenAICompatLLM(_ep(False), name="A")
        body = llm._body([{"role": "user", "content": "hi"}], None, 0.7, 128, stream=True)
        self.assertNotIn("chat_template_kwargs", body)

    def test_endpoint_defaults_to_no_think_on(self):
        self.assertTrue(ModelEndpoint("https://x/v1", "k", "m").no_think)

    def test_env_flag_can_turn_it_off(self):
        os.environ["SAWII_DISABLE_THINKING"] = "false"
        try:
            s = load_settings()
            self.assertFalse(s.a.no_think)
            self.assertFalse(s.b.no_think)
        finally:
            os.environ.pop("SAWII_DISABLE_THINKING", None)

    def test_env_flag_can_turn_it_on(self):
        os.environ["SAWII_DISABLE_THINKING"] = "true"
        try:
            self.assertTrue(load_settings().a.no_think)
        finally:
            os.environ.pop("SAWII_DISABLE_THINKING", None)


if __name__ == "__main__":
    unittest.main()
