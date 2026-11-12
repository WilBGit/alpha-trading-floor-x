import importlib.util
import unittest
from pathlib import Path


SCRIPT = Path(__file__).parents[1] / "scripts" / "x_topic_policy.py"
spec = importlib.util.spec_from_file_location("x_topic_policy", SCRIPT)
assert spec is not None and spec.loader is not None
policy = importlib.util.module_from_spec(spec)
spec.loader.exec_module(policy)


class XTopicPolicyTests(unittest.TestCase):
    def test_infers_benchmark_lanes(self):
        self.assertEqual(policy.infer_lane("SPY held support today"), "SPY")
        self.assertEqual(policy.infer_lane("QQQ rose after earnings"), "QQQ")
        self.assertEqual(policy.infer_lane("NVDA jumped after guidance"), "FINVIZ_STOCK")

    def test_does_not_repeat_last_lane(self):
        posts = [{"text": "SPY update"}]
        result = policy.choose_lane(posts, "2026-08-03")
        self.assertNotEqual(result["lane"], "SPY")

    def test_prefers_least_used_eligible_lane(self):
        posts = [
            {"text": "SPY update one"},
            {"text": "QQQ update one"},
            {"text": "SPY update two"},
        ]
        result = policy.choose_lane(posts, "2026-08-03")
        self.assertEqual(result["lane"], "FINVIZ_STOCK")

    def test_selection_is_stable_for_audit(self):
        posts = [{"text": "SPY update"}]
        first = policy.choose_lane(posts, "2026-08-03")
        second = policy.choose_lane(posts, "2026-08-03")
        self.assertEqual(first, second)


if __name__ == "__main__":
    unittest.main()
