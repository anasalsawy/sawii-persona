import unittest

from sawii_persona.subsystems import SubsystemHealth


class SubsystemHealthTests(unittest.TestCase):
    def test_active_calls_are_visible_without_changing_health_state(self):
        health = SubsystemHealth("a_llm")

        health.begin()

        snapshot = health.snapshot()
        self.assertEqual(snapshot["active_count"], 1)
        self.assertEqual(snapshot["status"], "idle")

        health.ok()
        health.end()

        snapshot = health.snapshot()
        self.assertEqual(snapshot["active_count"], 0)
        self.assertEqual(snapshot["status"], "alive")

    def test_active_count_does_not_go_negative(self):
        health = SubsystemHealth("b_llm")
        health.end()
        self.assertEqual(health.snapshot()["active_count"], 0)


if __name__ == "__main__":
    unittest.main()
