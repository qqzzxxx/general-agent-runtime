"""Checks for the offline census and its measurement boundaries."""
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from scripts import audit_intelligence_overhead as audit


class OverheadAuditTests(unittest.TestCase):
    def test_unicode_counts_are_not_token_estimates(self):
        metrics = audit.measure("A\n中")
        self.assertEqual(metrics["characters"], 3)
        self.assertEqual(metrics["utf8_bytes"], 5)
        self.assertNotIn("tokens", metrics)

    def test_wire_field_accounting_reconciles(self):
        payload = {"OBJECTIVE": "Summarize 中", "EXECUTOR_PROTOCOL": ["claim", "publish"]}
        metrics = audit.wire_metrics(payload)
        self.assertEqual(sum(metrics["field_characters"].values()) + metrics["framing_characters"],
                         metrics["compact_json"]["characters"])

    def test_current_builder_probe_is_offline_and_preserves_latest_context(self):
        original_write = Path.write_text
        written = []

        def write_fixture(path, *args, **kwargs):
            self.assertFalse(path.resolve().is_relative_to(audit.REPO.resolve()))
            self.assertTrue(any(part.startswith("overhead-audit-") for part in path.parts))
            written.append(path)
            return original_write(path, *args, **kwargs)

        # Guard the utility's input-file writes and forbid Agent subprocesses.
        with patch("subprocess.run", side_effect=AssertionError("process forbidden")), \
                patch.object(Path, "write_text", write_fixture):
            result = audit.synthetic_prompts()
        self.assertTrue(written)
        self.assertTrue(all(not path.exists() for path in written))
        for sample in result.values():
            for dimension in ("characters", "utf8_bytes"):
                self.assertEqual(sum(s[dimension] for s in sample["sections"].values()),
                                 sample["total"][dimension])
        self.assertGreater(result["history_tail"]["state_source_characters"], 18000)
        self.assertTrue(result["history_tail"]["latest_history_sentinel_visible"])
        self.assertEqual(result["steer"]["steer_text_copies"], 1)
        # Phase 1 replaces the delivered legacy dispatch section with semantics;
        # the historical Phase 0 sizes remain in the saved census, not this builder.
        self.assertLess(result["bootstrap"]["sections"]["runtime_contract"]["characters"], 14000)

    def test_historical_missing_duration_is_not_zero_and_embedded_paths_not_followed(self):
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            for i, value in enumerate((None, True, -1, 12.5, 0)):
                (directory / f"{i}.json").write_text(json.dumps({
                    "duration_seconds": value,
                    "invocation": {"event": {"committed_receipt_path": "DO_NOT_FOLLOW"}},
                    "outcome": {"committed": True}}), encoding="utf-8")
            result = audit.historical_durations(directory)
        self.assertEqual(result["count"], 2)
        self.assertEqual(result["sum_seconds"], 12.5)
        self.assertEqual(result["median_seconds"], 6.25)
        self.assertIsNone(result["protocol_attributable_seconds"])


if __name__ == "__main__":
    unittest.main()
