"""Memory Routing V2: snapshot-chain routing, marker semantics, offsets.

Covers the routing matrix of docs/v1.4-memory-routing-v2-design.md §5/§8 and
verifies utf8_offset pointers end to end through the real supervisor_inspect
--reason history path for CJK text under CRLF/LF and BOM/no-BOM storage.
"""
import json
import sys
import tempfile
import unittest
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parent
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import supervisor_context as ctx
import supervisor_control as sc
import supervisor_inspect

REPO = Path(__file__).resolve().parents[1]
MARKER = "<!-- supervisor-context-v2: current-memory -->"


def chain_file(events=3):
    """A realistic newest-first snapshot chain with CJK content."""
    parts = ["# Project Memory", "", MARKER, "",
             "## Current implementation facts",
             "- 当前采用 Kaiser 窗（β=8.6），本轮已完成基线对比。", "",
             "## Unresolved questions",
             "- 报告阶段是否需要双语对照。", ""]
    for k, event in enumerate(["第三轮审查", "第二轮审查", "首轮复盘"]):
        parts += [f"## Historical memory before {event}",
                  "以下原文完整保留；过期状态由以上章节替代。", "",
                  "# Project Memory", "", MARKER, "",
                  "## Current implementation facts",
                  f"- 本轮采用 Chebyshev 窗（order {4 - k}），当前即此。", "",
                  "## Hard constraints (with sources)",
                  f"- 旧约束 {k}（仅历史阶段）。", ""]
    parts += ["## Historical decisions", "- 初始阶段无产物。", ""]
    return "\n".join(parts)


class RoutingMatrixTests(unittest.TestCase):
    def route(self, text):
        return ctx.route_memory(text.encode("utf-8"))

    def test_chain_defers_whole_snapshots_including_inner_h2s(self):
        inline, deferred, routing = self.route(chain_file())
        self.assertEqual(routing["mode"], "snapshot_chain")
        self.assertEqual(routing["reason"], None)
        self.assertEqual(len(deferred), 4)  # three wrappers + tail decisions
        self.assertIn("Kaiser", inline)
        self.assertNotIn("Chebyshev", inline)
        self.assertNotIn("旧约束", inline)
        self.assertNotIn("初始阶段无产物", inline)
        self.assertIn("Unresolved questions", inline)  # current H2 tier stays
        self.assertEqual(routing["deferred_units"], len(deferred))
        self.assertEqual(routing["current_snapshot"]["marker"], True)
        self.assertEqual(routing["current_snapshot"]["line"], 1)

    def test_wrapper_fusion_uses_the_models_own_label(self):
        inline, deferred, _ = self.route(chain_file())
        labels = [d["heading"] for d in deferred]
        self.assertEqual(labels[0], "## Historical memory before 第三轮审查")
        self.assertEqual(labels[-1], "## Historical decisions")
        # The fused unit spans the wrapper note AND the nested snapshot.
        first = deferred[0]
        self.assertGreater(first["characters"], 60)
        self.assertEqual(first["line"], 11)

    def test_marker_copies_inside_history_are_inert(self):
        inline, deferred, routing = self.route(chain_file())
        self.assertEqual(routing["mode"], "snapshot_chain")
        self.assertEqual(routing["current_snapshot"]["marker"], True)
        self.assertEqual(len(deferred), 4)

    def test_marker_only_in_history_fails_visible_full_inline(self):
        text = ("# Project Memory\ncurrent\n"
                "## Historical memory before x\n" + MARKER + "\nold\n")
        inline, deferred, routing = self.route(text)
        self.assertEqual(routing["mode"], "unresolved")
        self.assertEqual(routing["reason"], "marker_not_in_current_snapshot")
        self.assertEqual(deferred, [])
        self.assertIn("old", inline)
        self.assertIn("## Historical memory before x", inline)

    def test_marker_only_in_preamble_fails_visible(self):
        text = (MARKER + "\nintro\n# Project Memory\ncurrent\n")
        inline, deferred, routing = self.route(text)
        self.assertEqual(routing["mode"], "unresolved")
        self.assertEqual(deferred, [])
        self.assertIn("current", inline)

    def test_no_marker_legacy_file_is_unchanged_full_inline(self):
        text = chain_file().replace(MARKER + "\n", "")
        inline, deferred, routing = self.route(text)
        self.assertEqual(routing["mode"], "full_inline")
        self.assertEqual(deferred, [])
        self.assertEqual(inline, text)

    def test_flat_h2_style_routes_exactly_as_before(self):
        text = (MARKER + "\n## Hard constraints\nkeep\n"
                "## Decision history\nOLD\n## Unresolved questions\nNEW\n"
                "## Custom heading\nACTIVE\n")
        inline, deferred, routing = self.route(text)
        self.assertEqual(routing["mode"], "h2_sections")
        self.assertEqual(len(deferred), 1)
        self.assertEqual(deferred[0]["heading"], "## Decision history")
        self.assertEqual(deferred[0]["line"], 4)
        self.assertNotIn("OLD", inline)
        for keep in ("keep", "NEW", "ACTIVE"):
            self.assertIn(keep, inline)

    def test_flat_h2_gate_stays_file_level_for_compat(self):
        # Pre-V2 files may carry the marker inside a deferred section only.
        text = ("## Hard constraints\nkeep\n## Decision history\n"
                + MARKER + "\nOLD\n## Unresolved questions\nNEW\n")
        inline, deferred, routing = self.route(text)
        self.assertEqual(routing["mode"], "h2_sections")
        self.assertNotIn("OLD", inline)

    def test_unclassified_h1_stays_inline_and_bends_history_around_it(self):
        text = ("# Project Memory\n" + MARKER + "\ncurrent\n"
                "## Historical memory before x\nold-snapshot\n"
                "# Free notes\nVISIBLE_NOTES\n"
                "## Historical memory before y\nold-two\n")
        inline, deferred, routing = self.route(text)
        self.assertEqual(routing["mode"], "snapshot_chain")
        self.assertIn("VISIBLE_NOTES", inline)
        headings = [d["heading"] for d in deferred]
        self.assertEqual(headings, ["## Historical memory before x",
                                    "## Historical memory before y"])
        self.assertNotIn("old-snapshot", inline)
        self.assertNotIn("old-two", inline)

    def test_invented_wrapper_title_stays_inline_snapshot_still_defers(self):
        text = ("# Project Memory\n" + MARKER + "\ncurrent\n"
                "## My old memory drawer\nstub line\n"
                "# Project Memory\nOLD_SNAPSHOT\n")
        inline, deferred, routing = self.route(text)
        self.assertEqual(routing["mode"], "snapshot_chain")
        self.assertIn("stub line", inline)  # unrecognized wrapper stays visible
        self.assertNotIn("OLD_SNAPSHOT", inline)
        self.assertEqual(deferred[0]["heading"], "# Project Memory (superseded snapshot)")

    def test_bare_appended_snapshots_each_get_a_generated_label(self):
        text = ("# Project Memory\n" + MARKER + "\ncurrent\n"
                "# Project Memory\nOLD1\n# Project Memory\nOLD2\n")
        inline, deferred, routing = self.route(text)
        self.assertEqual(routing["mode"], "snapshot_chain")
        self.assertEqual([d["heading"] for d in deferred],
                         ["# Project Memory (superseded snapshot)",
                          "# Project Memory (superseded snapshot)"])
        self.assertNotIn("OLD1", inline)

    def test_archive_inside_current_snapshot_defers_and_current_resumes(self):
        text = ("# Project Memory\n" + MARKER + "\n"
                "## Current implementation facts\nfacts\n"
                "## Archive\nOLD_ARCH\n"
                "## Unresolved questions\nstill-current\n")
        inline, deferred, routing = self.route(text)
        self.assertEqual(routing["mode"], "snapshot_chain")
        self.assertEqual([d["heading"] for d in deferred], ["## Archive"])
        self.assertNotIn("OLD_ARCH", inline)
        self.assertIn("still-current", inline)
        self.assertIn("facts", inline)

    def test_fenced_heading_like_text_is_never_a_unit_boundary(self):
        text = ("# Project Memory\n" + MARKER + "\n```markdown\n"
                "## Historical memory before fake\n## Archive\n```\n"
                "## Unresolved questions\nreal\n")
        inline, deferred, routing = self.route(text)
        self.assertEqual(deferred, [])
        self.assertIn("## Historical memory before fake", inline)
        self.assertIn("real", inline)

    def test_unterminated_fence_protects_to_eof(self):
        text = ("# Project Memory\n" + MARKER + "\ncurrent\n```\n"
                "## Historical memory before x\nstill fenced\n")
        inline, deferred, _ = self.route(text)
        self.assertEqual(deferred, [])
        self.assertIn("still fenced", inline)

    def test_preamble_before_current_snapshot_stays_inline(self):
        text = ("operator note\n## Archive\npre-history\n"
                "# Project Memory\n" + MARKER + "\ncurrent\n")
        inline, deferred, routing = self.route(text)
        self.assertEqual(routing["mode"], "snapshot_chain")
        self.assertIn("operator note", inline)
        self.assertIn("pre-history", inline)  # preamble never silently drops

    def test_memory_view_compat_returns_two_tuple(self):
        inline, deferred = ctx.memory_view(chain_file())
        self.assertNotIn("Chebyshev", inline)
        self.assertEqual(len(deferred), 4)

    def test_empty_memory_is_empty_inline(self):
        inline, deferred, routing = self.route("")
        self.assertEqual(inline, "")
        self.assertEqual(deferred, [])
        self.assertEqual(routing["mode"], "full_inline")


class OffsetRoundTripTests(unittest.TestCase):
    """utf8_offset must land byte-exactly through the real read helper.

    Convention: utf8_offset is the unit's first byte offset in the stored file
    (a leading BOM is counted), so `supervisor_inspect --offset` seeks exactly
    onto the unit for BOM and no-BOM files alike. Units are document-ordered,
    so each unit spans from its offset to the next unit's offset (EOF for the
    last); the read helper must return exactly those stored bytes.
    """

    def _round_trip(self, payload: bytes, name: str):
        with tempfile.TemporaryDirectory(prefix="mr2-offset-") as tmp:
            root = Path(tmp)
            memory = root / "RESEARCH_STATE.md"
            memory.write_bytes(payload)
            inline, deferred, routing = ctx.route_memory(payload)
            self.assertNotEqual(deferred, [], "fixture must defer something")
            offsets = [d["utf8_offset"] for d in deferred]
            self.assertEqual(offsets, sorted(offsets))
            bounds = offsets + [len(payload)]
            for entry, start, stop in zip(deferred, bounds, bounds[1:]):
                result = supervisor_inspect.inspect(
                    root, "RESEARCH_STATE.md", "history",
                    offset=start, limit=stop - start)
                self.assertEqual(result["status"], "READ", name)
                self.assertEqual(result["reason"], "history")
                self.assertEqual(result["bytes_read"], stop - start, name)
                content = result["content"]
                self.assertTrue(content.startswith(entry["heading"]),
                                f"{name}@{start}: {content[:40]!r}")
                self.assertEqual(content.encode("utf-8"), payload[start:stop], name)
            return routing

    def test_lf_no_bom_cjk_chain_round_trips(self):
        routing = self._round_trip(chain_file().encode("utf-8"), "lf/no-bom")
        self.assertEqual(routing["mode"], "snapshot_chain")

    def test_crlf_no_bom_cjk_chain_round_trips(self):
        text = chain_file().replace("\n", "\r\n")
        self._round_trip(text.encode("utf-8"), "crlf/no-bom")

    def test_crlf_bom_cjk_chain_round_trips(self):
        text = chain_file().replace("\n", "\r\n")
        routing = self._round_trip(b"\xef\xbb\xbf" + text.encode("utf-8"), "crlf/bom")
        self.assertEqual(routing["mode"], "snapshot_chain")
        # The BOM is part of the stored file, so offset 0 content starts after it.
        self.assertGreater(routing["current_snapshot"]["line"], 0)

    def test_lf_bom_cjk_chain_round_trips(self):
        self._round_trip(b"\xef\xbb\xbf" + chain_file().encode("utf-8"), "lf/bom")

    def test_pointer_entries_are_complete_json_records(self):
        text = chain_file()
        _, deferred, _ = ctx.route_memory(text.encode("utf-8"))
        for entry in deferred:
            self.assertEqual(set(entry), {"heading", "line", "characters",
                                          "utf8_offset"})
            self.assertGreater(entry["characters"], 20)
            self.assertGreaterEqual(entry["utf8_offset"], 0)


class ManifestAndBuildTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="mr2-build-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.state = {"status": "SUPERVISOR_TURN", "current_task": None,
                      "decision_history": [], "next_message_id": 700100}
        self.goal = self.root / "PROJECT_GOAL.md"
        self.memory = self.root / "RESEARCH_STATE.md"
        self.state_path = self.root / "project_state.json"
        self.goal.write_text("Produce the study; keep evidence.", encoding="utf-8")
        self.memory.write_text(chain_file(), encoding="utf-8", newline="\n")

    def prompt(self):
        self.state_path.write_text(json.dumps(self.state), encoding="utf-8")
        return ctx.build(root=self.root, reason="ORCHESTRATOR_START", event={},
                         state=self.state, state_path=self.state_path,
                         memory_path=self.memory, goal_path=self.goal,
                         rules_path=REPO / "control/CODEX_SUPERVISOR_RUNTIME.md",
                         profile=None, goal_anchor="", scope="", human_block="",
                         interventions=[], receipt=None, receipt_path=None,
                         fallback_brief=None,
                         now="2026-09-17T00:00:00+00:00", nonce="fixed")

    def test_manifest_reports_routing_and_deferred_unit_count(self):
        prompt = self.prompt()
        manifest = prompt.context_manifest
        routing = manifest["memory_routing"]
        self.assertEqual(routing["mode"], "snapshot_chain")
        self.assertEqual(routing["deferred_units"], 4)
        self.assertEqual(manifest["deferred_memory_sections"], 4)
        self.assertEqual(routing["current_snapshot"]["marker"], True)
        self.assertGreater(routing["current_snapshot"]["characters"], 100)
        sc._validate_context_manifest(manifest)

    def test_prompt_carries_current_snapshot_and_pointer_table(self):
        prompt = self.prompt()
        self.assertIn("Kaiser", prompt)
        self.assertIn("HISTORY AVAILABLE ON DEMAND", prompt)
        self.assertIn("utf8_offset", prompt)
        self.assertNotIn("Chebyshev", prompt)
        self.assertNotIn("初始阶段无产物", prompt)

    def test_prompt_pointer_offsets_round_trip_in_place(self):
        prompt = self.prompt()
        import re
        offsets = [int(m) for m in re.findall(r'"utf8_offset": (\d+)', prompt)]
        self.assertEqual(len(offsets), 4)
        payload = self.memory.read_bytes()
        for offset in offsets:
            window = payload[offset:offset + 80].decode("utf-8", errors="ignore")
            self.assertTrue(window.startswith("## Historical"),
                            window[:40])

    def test_unreadable_memory_fails_visible_with_reason(self):
        self.memory.write_bytes(b"# Project Memory\n\xff\xfe broken\n")
        prompt = self.prompt()
        self.assertIn("[UNAVAILABLE: RESEARCH_STATE.md: not UTF-8]", prompt)
        routing = prompt.context_manifest["memory_routing"]
        self.assertEqual(routing["mode"], "full_inline")
        self.assertEqual(routing["reason"], "not_utf8")
        self.assertEqual(prompt.context_manifest["deferred_memory_sections"], 0)

    def test_missing_memory_file_fails_visible_with_reason(self):
        self.memory.unlink()
        prompt = self.prompt()
        self.assertIn("[UNAVAILABLE:", prompt)
        routing = prompt.context_manifest["memory_routing"]
        self.assertEqual(routing["mode"], "full_inline")
        self.assertEqual(routing["reason"], "unreadable_memory_file")


if __name__ == "__main__":
    unittest.main()
