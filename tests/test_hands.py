"""Tests for the hands (B's execution agent: Cline by default)."""
import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from sawii_persona.config import load_settings  # noqa: E402
from sawii_persona.lanes.deep import ExternalAgentWorker, attach  # noqa: E402


class HandsTests(unittest.TestCase):
    def test_name_detects_the_agent(self):
        self.assertEqual(ExternalAgentWorker(mode="cli", url="", cmd="cline").name, "cline")
        self.assertEqual(ExternalAgentWorker(mode="cli", url="", cmd="devin run").name, "devin")
        self.assertEqual(ExternalAgentWorker(mode="cli", url="", cmd="my-agent").name, "agent")

    def test_cli_available_uses_which(self):
        with mock.patch("shutil.which", return_value="/usr/bin/cline"):
            self.assertTrue(ExternalAgentWorker.cli_available("cline"))
        with mock.patch("shutil.which", return_value=None):
            self.assertFalse(ExternalAgentWorker.cli_available("cline"))
        self.assertFalse(ExternalAgentWorker.cli_available(""))

    def test_hands_defaults(self):
        s = load_settings()
        self.assertEqual(s.hands_mode, "auto")
        self.assertEqual(s.hands_cmd, "cline")
        self.assertTrue(s.hands_url.startswith("http"))

    def test_hands_env_overrides(self):
        os.environ.update({"SAWII_HANDS_MODE": "http", "SAWII_HANDS_URL": "http://x:9",
                           "SAWII_HANDS_CMD": "devin"})
        try:
            s = load_settings()
            self.assertEqual(s.hands_mode, "http")
            self.assertEqual(s.hands_url, "http://x:9")
            self.assertEqual(s.hands_cmd, "devin")
        finally:
            for k in ("SAWII_HANDS_MODE", "SAWII_HANDS_URL", "SAWII_HANDS_CMD"):
                os.environ.pop(k, None)


class AttachTests(unittest.IsolatedAsyncioTestCase):
    async def test_attach_prefers_external_agent_when_cli_present(self):
        import tempfile
        from sawii_persona.mind import Mind
        with tempfile.TemporaryDirectory() as tmp:
            s = load_settings(data_dir=tmp, db_path=os.path.join(tmp, "d.db"), hands_cmd="cline")
            from tests.fakes import make_a, make_b, make_c
            mind = Mind(s, llms={"a": make_a(), "b": make_b(), "c": make_c(), "embed": None,
                                 "consultants": {}})
            with mock.patch("shutil.which", return_value="/usr/bin/cline"):
                attach(mind, s)
            self.assertIsNotNone(mind.deep)
            self.assertEqual(mind.deep.worker_factory().name, "cline")
            mind.store.close()

    async def test_attach_falls_back_to_plain_model_without_agent(self):
        import tempfile
        from sawii_persona.mind import Mind
        with tempfile.TemporaryDirectory() as tmp:
            s = load_settings(data_dir=tmp, db_path=os.path.join(tmp, "d.db"), hands_mode="auto",
                              hands_cmd="definitely-not-installed-xyz")
            from tests.fakes import make_a, make_b, make_c
            mind = Mind(s, llms={"a": make_a(), "b": make_b(), "c": make_c(), "embed": None,
                                 "consultants": {}})
            with mock.patch("shutil.which", return_value=None):
                attach(mind, s)
            self.assertEqual(mind.deep.worker_factory().name, "model")
            mind.store.close()


if __name__ == "__main__":
    unittest.main()
