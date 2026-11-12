import importlib.util
import tempfile
import unittest
from pathlib import Path


SCRIPT = Path(__file__).parents[1] / "scripts" / "x_publisher.py"
spec = importlib.util.spec_from_file_location("simple_x_publisher", SCRIPT)
assert spec is not None and spec.loader is not None
publisher = importlib.util.module_from_spec(spec)
spec.loader.exec_module(publisher)


class SimpleXPublisherGuardrailTests(unittest.TestCase):
    def test_proofreader_blocks_shell_stripped_spy_price(self):
        ok, reason = publisher._proofread_text("SPY is 42.90, down 1.0%.")
        self.assertFalse(ok)
        self.assertEqual(reason, "implausible_spy_price")

    def test_proofreader_accepts_valid_spy_price(self):
        ok, reason = publisher._proofread_text("SPY is $742.90, down 1.0%.")
        self.assertTrue(ok)
        self.assertEqual(reason, "")

    def test_near_duplicate_is_blocked(self):
        previous = "The Fed held rates, keeping inflation risk in focus. SPY is $742.58. Watch yields and jobs."
        candidate = "The Fed held rates, keeping inflation risks in focus. SPY is $742.90. Watch yields and jobs."
        duplicate, score = publisher._is_near_duplicate(candidate, [previous])
        self.assertTrue(duplicate)
        self.assertGreaterEqual(score, 0.80)

    def test_dissimilar_post_is_allowed(self):
        previous = "The Fed held rates. SPY is $742.58. Watch yields."
        candidate = "A major earnings warning hit semiconductors as guidance weakened across the sector."
        duplicate, _ = publisher._is_near_duplicate(candidate, [previous])
        self.assertFalse(duplicate)

    def test_project_dry_run_blocks_live_publication(self):
        original_enabled = getattr(publisher, "AUTO_POST_ENABLED")
        original_mode = getattr(publisher, "PUBLISH_MODE")
        try:
            setattr(publisher, "AUTO_POST_ENABLED", False)
            setattr(publisher, "PUBLISH_MODE", "DRY_RUN")
            allowed, reason = publisher._publication_gate()
            self.assertFalse(allowed)
            self.assertEqual(reason, "auto_post_disabled")
        finally:
            setattr(publisher, "AUTO_POST_ENABLED", original_enabled)
            setattr(publisher, "PUBLISH_MODE", original_mode)

    def test_publish_file_reads_exact_dollar_text(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            post_file = Path(temp_dir) / "post.txt"
            post_file.write_text("SPY is $742.90. Watch $745 next.\n")
            self.assertEqual(
                publisher._read_post_file(post_file),
                "SPY is $742.90. Watch $745 next.",
            )


if __name__ == "__main__":
    unittest.main()
