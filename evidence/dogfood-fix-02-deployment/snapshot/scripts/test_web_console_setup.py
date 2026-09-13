"""P7 Setup Wizard pure-layer tests (offline, deterministic).

Covers the pure, offline half of the Project Setup surface in
`web_console_setup.py`: the deterministic Goal validator decision table
(Unicode, malformed Markdown, hostile content, publication-root and routing-
internals rules), the Supervisor configuration schema and capability honesty,
the input-registration request schema and inventory projection, the canonical
ZCode Automation prompt rendering (BOM handling, placeholder fail-closed), the
readiness decision table, the bounded Goal Workshop context pack/startup
prompt composition, and the Console-owned draft schema.

No clock, disk, randomness, subprocess, or network is touched: everything
here runs against plain values, mirroring the pure-layer convention of the
P3-P6 modules.
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parent
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import web_console_control
import web_console_setup as setup

VALID_GOAL = """# Project Goal — Cafe Analytics

## Objective

Build a reproducible sales-analytics notebook for the cafe dataset and
summarize the findings for the owner in plain language.

## Context

The cafe publishes a monthly CSV export; prior analysis lived in ad-hoc
spreadsheets that nobody can rerun.

## Available Inputs and Resources

- data/cafe-sales.csv (registered input)
- Python 3.12 with pandas

## Required Deliverables

- analysis/cafe-report.md with charts referenced from reports/
- a rerunnable notebook under workspace/

## Constraints

- No network access; use only the registered input.
- Keep all outputs under the supported publication roots.

## Non-Goals

- No dashboard development.

## Acceptance Criteria

- The notebook runs end to end on the registered input without errors.
- The report states the three highest-revenue weekday/hour pairs.
"""

VALID_GOAL_ALT = VALID_GOAL.replace("## Required Deliverables", "## Outputs")


def goal_with(section: str, body: str) -> str:
    return VALID_GOAL.replace(section, section + "\n" + body)


class GoalValidatorTests(unittest.TestCase):
    def test_valid_goal_passes_with_clean_checks(self):
        result = setup.validate_goal(
            project_id="cafe-001", project_type="GENERAL",
            goal_markdown=VALID_GOAL)
        self.assertTrue(result["valid"], result)
        self.assertEqual(result["errors"], [])
        self.assertEqual(result["schema_version"], 1)
        self.assertTrue(result["checks"]["objective"]["meaningful"])
        self.assertTrue(result["checks"]["deliverables"]["meaningful"])
        self.assertTrue(result["checks"]["acceptance_criteria"]["meaningful"])
        self.assertTrue(result["checks"]["project_id"]["valid"])
        self.assertTrue(result["checks"]["project_type"]["valid"])
        self.assertTrue(result["checks"]["goal_readability"]["ok"])
        self.assertEqual(result["checks"]["publication_roots"]["unsafe_found"], [])
        self.assertEqual(result["checks"]["routing_internals"]["found"], [])

    def test_deliverables_alone_or_acceptance_alone_suffice(self):
        only_deliverables = VALID_GOAL.replace(
            "## Acceptance Criteria\n\n- The notebook runs end to end on the "
            "registered input without errors.\n- The report states the three "
            "highest-revenue weekday/hour pairs.\n", "")
        self.assertTrue(setup.validate_goal(
            project_id="p1", project_type="GENERAL",
            goal_markdown=only_deliverables)["valid"])
        only_acceptance = VALID_GOAL.replace(
            "## Required Deliverables\n\n- analysis/cafe-report.md with "
            "charts referenced from reports/\n- a rerunnable notebook under "
            "workspace/\n", "")
        self.assertTrue(setup.validate_goal(
            project_id="p1", project_type="GENERAL",
            goal_markdown=only_acceptance)["valid"])

    def test_missing_objective_is_an_error(self):
        broken = VALID_GOAL.replace(
            "Build a reproducible sales-analytics notebook for the cafe "
            "dataset and\nsummarize the findings for the owner in plain "
            "language.\n", "")
        broken = broken.replace("## Objective\n\n", "## Objective\n")
        result = setup.validate_goal(
            project_id="p1", project_type="GENERAL", goal_markdown=broken)
        self.assertFalse(result["valid"])
        self.assertIn("GOAL_OBJECTIVE_MISSING",
                      [e["code"] for e in result["errors"]])

    def test_missing_both_deliverables_and_acceptance_is_an_error(self):
        broken = "\n".join(
            line for line in VALID_GOAL.splitlines()
            if "deliverable" not in line.lower()
            and "cafe-report" not in line
            and "acceptance" not in line.lower()
            and "highest-revenue" not in line
            and "runs end to end" not in line)
        result = setup.validate_goal(
            project_id="p1", project_type="GENERAL", goal_markdown=broken)
        self.assertFalse(result["valid"])
        self.assertIn("GOAL_ACCEPTANCE_MISSING",
                      [e["code"] for e in result["errors"]])

    def test_thin_objective_content_is_not_meaningful(self):
        thin = VALID_GOAL.replace(
            "Build a reproducible sales-analytics notebook for the cafe "
            "dataset and\nsummarize the findings for the owner in plain "
            "language.", "TBD.")
        result = setup.validate_goal(
            project_id="p1", project_type="GENERAL", goal_markdown=thin)
        self.assertFalse(result["valid"])
        self.assertIn("GOAL_OBJECTIVE_MISSING",
                      [e["code"] for e in result["errors"]])

    def test_project_id_decision_table(self):
        for bad in ("", "  ", ".hidden", "-lead", "café", "has space",
                    "a" * 65, "slash/es", "back\\slash", "colon:", None,
                    17, True):
            result = setup.validate_goal(
                project_id=bad, project_type="GENERAL",
                goal_markdown=VALID_GOAL)
            self.assertFalse(result["valid"], repr(bad))
            self.assertIn("PROJECT_ID_INVALID",
                          [e["code"] for e in result["errors"]])
        for good in ("a", "Z9", "proj-01", "x" * 64, "A.b_c-d9"):
            result = setup.validate_goal(
                project_id=good, project_type="GENERAL",
                goal_markdown=VALID_GOAL)
            self.assertNotIn("PROJECT_ID_INVALID",
                             [e["code"] for e in result["errors"]], good)

    def test_project_type_decision_table(self):
        for bad in ("", "general ", "SOFTWARE", "research", None, 3):
            result = setup.validate_goal(
                project_id="p1", project_type=bad, goal_markdown=VALID_GOAL)
            self.assertIn("PROJECT_TYPE_INVALID",
                          [e["code"] for e in result["errors"]], repr(bad))
        for good in setup.PROJECT_TYPES:
            result = setup.validate_goal(
                project_id="p1", project_type=good, goal_markdown=VALID_GOAL)
            self.assertNotIn("PROJECT_TYPE_INVALID",
                             [e["code"] for e in result["errors"]], good)

    def test_existing_project_id_is_rejected_never_overwritten(self):
        result = setup.validate_goal(
            project_id="cafe-001", project_type="GENERAL",
            goal_markdown=VALID_GOAL, project_id_exists=True)
        self.assertFalse(result["valid"])
        self.assertIn("PROJECT_EXISTS",
                      [e["code"] for e in result["errors"]])
        self.assertTrue(result["checks"]["overwrite_protection"]
                        ["project_id_exists"])

    def test_artifacts_output_root_is_rejected(self):
        hostile = goal_with(
            "## Required Deliverables",
            "- a monthly dashboard bundle under artifacts/")
        result = setup.validate_goal(
            project_id="p1", project_type="GENERAL", goal_markdown=hostile)
        self.assertFalse(result["valid"])
        codes = [e["code"] for e in result["errors"]]
        self.assertIn("PUBLICATION_ROOT_UNSUPPORTED", codes)
        self.assertTrue(any("artifacts" in e.get("matched", "")
                            for e in result["errors"]))

    def test_unsafe_authoritative_output_roots_are_rejected(self):
        for token in ("control/", "handoff/", "projects/"):
            hostile = goal_with(
                "## Required Deliverables",
                f"- export the summary to {token}outcomes.md")
            result = setup.validate_goal(
                project_id="p1", project_type="GENERAL",
                goal_markdown=hostile)
            self.assertIn("PUBLICATION_ROOT_UNSUPPORTED",
                          [e["code"] for e in result["errors"]], token)

    def test_supported_publication_roots_do_not_error(self):
        result = setup.validate_goal(
            project_id="p1", project_type="GENERAL",
            goal_markdown=VALID_GOAL_ALT)
        self.assertNotIn("PUBLICATION_ROOT_UNSUPPORTED",
                         [e["code"] for e in result["errors"]])

    def test_routing_internals_produce_warnings_not_errors(self):
        hostile = goal_with(
            "## Constraints",
            "- dispatch exactly 5 supervisor turns and exactly 3 executor "
            "tasks; MESSAGE_ID 700200 must use NONCE abc; keep the claim "
            "token and executor_fence check order; append to the completion "
            "ledger after each round.")
        result = setup.validate_goal(
            project_id="p1", project_type="GENERAL", goal_markdown=hostile)
        self.assertTrue(result["valid"], result)
        codes = [w["code"] for w in result["warnings"]]
        for expected in ("W_ROUTING_MESSAGE_ID", "W_ROUTING_NONCE",
                         "W_ROUTING_FENCE", "W_ROUTING_LEDGER",
                         "W_EXACT_TURN_COUNT", "W_EXACT_TASK_COUNT"):
            self.assertIn(expected, codes, expected)

    def test_unicode_and_emoji_goal_is_readable(self):
        unicode_goal = VALID_GOAL.replace(
            "Cafe Analytics", "咖啡分析 café ✅")
        result = setup.validate_goal(
            project_id="p1", project_type="GENERAL",
            goal_markdown=unicode_goal)
        self.assertTrue(result["valid"], result)

    def test_control_characters_are_rejected(self):
        hostile = VALID_GOAL.replace("Cafe", "Caf\x00e")
        result = setup.validate_goal(
            project_id="p1", project_type="GENERAL", goal_markdown=hostile)
        self.assertIn("GOAL_NOT_TEXT", [e["code"] for e in result["errors"]])

    def test_oversized_goal_is_rejected(self):
        result = setup.validate_goal(
            project_id="p1", project_type="GENERAL",
            goal_markdown=VALID_GOAL + "x" * (setup.GOAL_MAX_CHARS + 1))
        self.assertIn("GOAL_TOO_LARGE", [e["code"] for e in result["errors"]])

    def test_empty_and_whitespace_goal_is_rejected(self):
        for empty in ("", "   \n  \n"):
            result = setup.validate_goal(
                project_id="p1", project_type="GENERAL", goal_markdown=empty)
            self.assertIn("GOAL_EMPTY", [e["code"] for e in result["errors"]])

    def test_leading_bom_is_tolerated(self):
        result = setup.validate_goal(
            project_id="p1", project_type="GENERAL",
            goal_markdown="\ufeff" + VALID_GOAL)
        self.assertTrue(result["valid"], result)

    def test_validator_is_deterministic(self):
        first = setup.validate_goal(
            project_id="p1", project_type="GENERAL", goal_markdown=VALID_GOAL)
        second = setup.validate_goal(
            project_id="p1", project_type="GENERAL", goal_markdown=VALID_GOAL)
        self.assertEqual(first, second)


class SupervisorConfigTests(unittest.TestCase):
    def test_valid_config_with_nulls(self):
        config = setup.validate_supervisor_config(
            {"model": None, "reasoning_effort": None,
             "explanation_mode": "COMPACT"})
        self.assertEqual(config["explanation_mode"], "COMPACT")
        self.assertIsNone(config["model"])

    def test_valid_config_with_values(self):
        config = setup.validate_supervisor_config(
            {"model": "GPT-5.6 Sol", "reasoning_effort": "HIGH",
             "explanation_mode": "MINIMAL"})
        self.assertEqual(config["model"], "GPT-5.6 Sol")
        self.assertEqual(config["reasoning_effort"], "HIGH")

    def test_explanation_mode_enum(self):
        for mode in ("MINIMAL", "COMPACT", "DETAILED_ON_DEMAND"):
            setup.validate_supervisor_config(
                {"model": None, "reasoning_effort": None,
                 "explanation_mode": mode})
        for bad in ("compact", "VERBOSE", "", None, 3):
            with self.assertRaises(web_console_control.ControlRequestError):
                setup.validate_supervisor_config(
                    {"model": None, "reasoning_effort": None,
                     "explanation_mode": bad})

    def test_reasoning_effort_enum(self):
        for effort in setup.REASONING_EFFORTS:
            setup.validate_supervisor_config(
                {"model": None, "reasoning_effort": effort,
                 "explanation_mode": "COMPACT"})
        with self.assertRaises(web_console_control.ControlRequestError):
            setup.validate_supervisor_config(
                {"model": None, "reasoning_effort": "MAXIMUM",
                 "explanation_mode": "COMPACT"})

    def test_model_bounds(self):
        with self.assertRaises(web_console_control.ControlRequestError):
            setup.validate_supervisor_config(
                {"model": "m" * 81, "reasoning_effort": None,
                 "explanation_mode": "COMPACT"})
        with self.assertRaises(web_console_control.ControlRequestError):
            setup.validate_supervisor_config(
                {"model": "bad\x00model", "reasoning_effort": None,
                 "explanation_mode": "COMPACT"})

    def test_exact_keys_enforced(self):
        for bad in ({}, {"model": None},
                    {"model": None, "reasoning_effort": None,
                     "explanation_mode": "COMPACT", "root": "C:\\evil"},
                    {"model": None, "reasoning_effort": None,
                     "explanation_mode": "COMPACT", "project_id": "x"}):
            with self.assertRaises(web_console_control.ControlRequestError):
                setup.validate_supervisor_config(bad)

    def test_capability_report_defaults_to_not_reported(self):
        report = setup.capability_report(None)
        self.assertFalse(report["reported"])
        self.assertEqual(report["models"], [])
        report = setup.capability_report({"schema_version": 1})
        self.assertFalse(report["reported"])

    def test_capability_report_uses_reported_capabilities_when_present(self):
        status = {"schema_version": 1,
                  "supervisor_capabilities": {
                      "models": ["GPT-5.6 Sol", "GPT-6"],
                      "reasoning_efforts": ["LOW", "HIGH"]}}
        report = setup.capability_report(status)
        self.assertTrue(report["reported"])
        self.assertEqual(report["models"], ["GPT-5.6 Sol", "GPT-6"])
        self.assertEqual(report["reasoning_efforts"], ["LOW", "HIGH"])

    def test_capability_report_malformed_block_is_not_reported(self):
        status = {"schema_version": 1,
                  "supervisor_capabilities": {"models": "GPT-5.6"}}
        report = setup.capability_report(status)
        self.assertFalse(report["reported"])


class InputSchemaTests(unittest.TestCase):
    def test_paths_request(self):
        request = setup.validate_inputs_request(
            {"paths": ["C:\\data\\cafe.csv"]})
        self.assertEqual(request["paths"], ["C:\\data\\cafe.csv"])
        self.assertNotIn("decision", request)

    def test_none_needed_request(self):
        request = setup.validate_inputs_request({"decision": "NONE_NEEDED"})
        self.assertEqual(request, {"decision": "NONE_NEEDED"})

    def test_request_schema_failures(self):
        for bad in ({}, {"paths": []}, {"paths": "C:\\x"},
                    {"paths": ["a"] * (setup.INPUT_MAX_SELECTIONS + 1)},
                    {"paths": [17]}, {"decision": "REGISTERED"},
                    {"decision": "NONE_NEEDED", "paths": ["C:\\x"]},
                    {"paths": ["C:\\x"], "decision": None}):
            with self.assertRaises(web_console_control.ControlRequestError,
                                   msg=repr(bad)):
                setup.validate_inputs_request(bad)

    def test_inventory_projection_file_and_directory(self):
        inventory = setup.build_input_inventory([
            {"path": "C:\\data\\cafe.csv", "kind": "file", "entries": [
                {"path": "cafe.csv", "name": "cafe.csv", "kind": "file",
                 "size_bytes": 12}],
             "complete": True, "error": None},
            {"path": "C:\\data\\set", "kind": "directory", "entries": [
                {"path": "b.csv", "name": "b.csv", "kind": "file",
                 "size_bytes": 3},
                {"path": "sub/a.csv", "name": "a.csv", "kind": "file",
                 "size_bytes": 4},
                {"path": "sub", "name": "sub", "kind": "directory",
                 "size_bytes": None}],
             "complete": True, "error": None},
        ])
        self.assertEqual(inventory["decision"], "REGISTERED")
        self.assertTrue(inventory["complete"])
        self.assertEqual(inventory["total_entries"], 4)
        self.assertEqual([entry["path"] for entry in inventory["entries"]],
                         ["cafe.csv", "b.csv", "sub", "sub/a.csv"])

    def test_inventory_surfaces_selection_errors_and_truncation(self):
        inventory = setup.build_input_inventory([
            {"path": "C:\\gone", "kind": "file", "entries": [],
             "complete": False, "error": "INPUT_PATH_MISSING"},
            {"path": "C:\\big", "kind": "directory",
             "entries": [{"path": f"row-{i}.txt", "name": f"row-{i}.txt",
                          "kind": "file", "size_bytes": 1}
                         for i in range(setup.INPUT_WALK_MAX_ENTRIES + 5)],
             "complete": False, "error": None},
        ])
        self.assertFalse(inventory["complete"])
        self.assertTrue(any(sel["error"] == "INPUT_PATH_MISSING"
                            for sel in inventory["selections"]))
        self.assertLessEqual(len(inventory["entries"]),
                             setup.INPUT_INVENTORY_MAX_ENTRIES)
        self.assertTrue(inventory["honesty"]["notes"])

    def test_inventory_is_deterministic(self):
        selections = [{"path": "C:\\d", "kind": "directory", "entries": [
            {"path": "z.txt", "name": "z.txt", "kind": "file",
             "size_bytes": 1},
            {"path": "a.txt", "name": "a.txt", "kind": "file",
             "size_bytes": 2}], "complete": True, "error": None}]
        self.assertEqual(setup.build_input_inventory(selections),
                         setup.build_input_inventory(selections))


class ZcodePromptTests(unittest.TestCase):
    def test_render_replaces_all_placeholders(self):
        rendered = setup.render_zcode_prompt(
            "root=<RUNTIME_ROOT>\nagain=<RUNTIME_ROOT>\n",
            "C:\\rt\\café")
        self.assertEqual(rendered, "root=C:\\rt\\café\nagain=C:\\rt\\café\n")

    def test_render_requires_placeholder(self):
        with self.assertRaises(ValueError):
            setup.render_zcode_prompt("no placeholder here", "C:\\rt")

    def test_render_rejects_leftover_placeholder(self):
        with self.assertRaises(ValueError):
            setup.render_zcode_prompt("<RUNTIME_ROOT>", "<RUNTIME_ROOT>")

    def test_decode_strips_bom_and_requires_utf8(self):
        text = setup.decode_zcode_template(
            b"\xef\xbb\xbfExecutor: <RUNTIME_ROOT>\n")
        self.assertEqual(text, "Executor: <RUNTIME_ROOT>\n")
        self.assertEqual(setup.decode_zcode_template(b"A <RUNTIME_ROOT>"),
                         "A <RUNTIME_ROOT>")
        with self.assertRaises(ValueError):
            setup.decode_zcode_template(b"\xff\xfe\x00bad")
        with self.assertRaises(ValueError):
            setup.decode_zcode_template(b"")

    def test_automation_steps_are_documented(self):
        self.assertEqual(len(setup.ZCODE_AUTOMATION_STEPS), 6)
        self.assertTrue(any("Workspace" in step
                            for step in setup.ZCODE_AUTOMATION_STEPS))


class WorkshopPackTests(unittest.TestCase):
    def test_pack_contains_contract_type_roots_and_inventory(self):
        inventory = setup.build_input_inventory([
            {"path": "C:\\data\\cafe.csv", "kind": "file", "entries": [
                {"path": "cafe.csv", "name": "cafe.csv", "kind": "file",
                 "size_bytes": 12}], "complete": True, "error": None}])
        pack = setup.compose_goal_workshop_pack(
            doc_text="# Contract\n\nWrite goals as destinations.\n",
            runtime_facts={"label": "Alpha café", "root": "C:\\rt",
                           "status_schema_version": 1},
            project_type="ACADEMIC_RESEARCH",
            input_inventory=inventory)
        self.assertIn("# Contract", pack)
        self.assertIn("ACADEMIC_RESEARCH", pack)
        for root_name in ("workspace/", "evidence/", "reports/"):
            self.assertIn(root_name, pack)
        self.assertIn("cafe.csv", pack)
        self.assertIn("Alpha café", pack)
        self.assertNotIn("C:\\rt", pack)

    def test_pack_without_doc_or_inventory_is_honest(self):
        pack = setup.compose_goal_workshop_pack(
            doc_text=None,
            runtime_facts={"label": "R", "root": "C:\\rt",
                           "status_schema_version": None},
            project_type=None, input_inventory=None)
        self.assertIn("CONTRACT_UNAVAILABLE", pack)
        self.assertIn("no input inventory", pack)
        self.assertNotIn("C:\\rt", pack)

    def test_pack_is_bounded_and_deterministic(self):
        doc = "# Contract\n\n" + ("line of contract prose\n" * 5000)
        pack_a = setup.compose_goal_workshop_pack(
            doc_text=doc, runtime_facts={"label": "R", "root": "C:\\rt",
                                         "status_schema_version": 1},
            project_type="GENERAL", input_inventory=None)
        pack_b = setup.compose_goal_workshop_pack(
            doc_text=doc, runtime_facts={"label": "R", "root": "C:\\rt",
                                         "status_schema_version": 1},
            project_type="GENERAL", input_inventory=None)
        self.assertEqual(pack_a, pack_b)
        self.assertLessEqual(len(pack_a.encode("utf-8")),
                             setup.CONTEXT_PACK_MAX_BYTES)

    def test_startup_prompt_is_bounded_and_actionable(self):
        prompt = setup.compose_startup_prompt(project_type="GENERAL")
        self.assertIn("context pack", prompt)
        self.assertLessEqual(len(prompt), setup.STARTUP_PROMPT_MAX_CHARS)
        self.assertNotIn("http", prompt.lower())


class ReadinessTests(unittest.TestCase):
    def build_draft(self, **overrides):
        draft = setup.empty_draft()
        draft = setup.merge_goal_into_draft(
            draft, project_id="proj-new", project_type="GENERAL",
            goal_markdown=VALID_GOAL, source_name="goal.md")
        draft = setup.merge_inputs_into_draft(draft, {
            "decision": "NONE_NEEDED", "entries": [], "selections": [],
            "total_entries": 0, "complete": True, "honesty": {"notes": []}})
        draft = setup.merge_supervisor_into_draft(draft, {
            "model": None, "reasoning_effort": None,
            "explanation_mode": "COMPACT"})
        draft = setup.merge_zcode_acknowledgement(
            draft, "2026-09-12T00:00:00+00:00")
        for key, value in overrides.items():
            draft[key] = value
        return draft

    def ready_inputs(self):
        return {
            "draft": self.build_draft(),
            "goal_validation": setup.validate_goal(
                project_id="proj-new", project_type="GENERAL",
                goal_markdown=VALID_GOAL),
            "status_failure": None,
            "status_doc": {"schema_version": 1, "PROJECT_ID": None,
                           "runtime_status": "IDLE", "stop": False},
            "preflight": {"ok": True, "exit_code": 0, "head": "PREFLIGHT: OK"},
            "python_check": {"ok": True, "python_version": "3.12.1"},
            "project_id_exists": False,
            "profile_present": True,
        }

    def test_all_passing_inputs_yield_ready(self):
        document = setup.readiness_document(**self.ready_inputs())
        self.assertTrue(document["ready"])
        self.assertEqual(len(document["checks"]), 10)
        keys = [check["key"] for check in document["checks"]]
        self.assertEqual(keys, [
            "goal_loaded", "goal_anchor", "inputs_registered",
            "project_type_valid", "supervisor_config_valid",
            "runtime_healthy", "python_requirements", "control_plane_v12",
            "zcode_acknowledged", "preflight"])
        self.assertTrue(all(check["state"] == "PASS"
                            for check in document["checks"]))
        self.assertTrue(all(check["required"]
                            for check in document["checks"]))

    def test_each_failed_input_fails_its_check(self):
        cases = [
            ({"goal_validation": setup.validate_goal(
                project_id="proj-new", project_type="GENERAL",
                goal_markdown="no sections")}, "goal_loaded"),
            ({"project_id_exists": True}, "goal_anchor"),
            ({"draft": self.build_draft(inputs={"decision": None})},
             "inputs_registered"),
            ({"draft": self.build_draft(supervisor=None)},
             "supervisor_config_valid"),
            ({"status_failure": "CONTROL_PLANE_TIMEOUT",
              "status_doc": None}, "runtime_healthy"),
            ({"python_check": {"ok": False, "python_version": "3.8.0"}},
             "python_requirements"),
            ({"status_doc": {"schema_version": 2, "stop": False}},
             "control_plane_v12"),
            ({"profile_present": False}, "project_type_valid"),
            ({"preflight": {"ok": False, "exit_code": 1,
                            "head": "PREFLIGHT: BLOCKED"}},
             "preflight"),
        ]
        for overrides, expected_key in cases:
            inputs = self.ready_inputs()
            inputs.update(overrides)
            document = setup.readiness_document(**inputs)
            states = {check["key"]: check["state"]
                      for check in document["checks"]}
            self.assertEqual(states[expected_key], "FAIL",
                             (expected_key, states))
            self.assertFalse(document["ready"], expected_key)

    def test_zcode_acknowledgement_is_required(self):
        inputs = self.ready_inputs()
        inputs["draft"] = setup.empty_draft()
        document = setup.readiness_document(**inputs)
        states = {check["key"]: check["state"]
                  for check in document["checks"]}
        self.assertFalse(document["ready"])
        self.assertEqual(states["zcode_acknowledged"], "FAIL")

    def test_check_documents_carry_labels_and_details(self):
        document = setup.readiness_document(**self.ready_inputs())
        for check in document["checks"]:
            self.assertTrue(check["label"])
            self.assertTrue(isinstance(check["detail"], str))
            self.assertIn(check["state"], ("PASS", "FAIL", "WARN"))


class DraftTests(unittest.TestCase):
    def test_empty_draft_shape(self):
        draft = setup.empty_draft()
        self.assertEqual(draft["schema_version"], 1)
        self.assertIsNone(draft["project_id"])
        self.assertIsNone(draft["goal_markdown"])
        self.assertIsNone(draft["inputs"])
        self.assertIsNone(draft["supervisor"])
        self.assertFalse(draft["zcode"]["acknowledged"])

    def test_merges_accumulate(self):
        draft = setup.empty_draft()
        draft = setup.merge_goal_into_draft(
            draft, project_id="p1", project_type="GENERAL",
            goal_markdown=VALID_GOAL, source_name="g.md")
        draft = setup.merge_supervisor_into_draft(draft, {
            "model": "GPT-5.6 Sol", "reasoning_effort": "HIGH",
            "explanation_mode": "COMPACT"})
        draft = setup.merge_zcode_acknowledgement(
            draft, "2026-09-12T00:00:00+00:00")
        draft = setup.merge_none_needed_into_draft(draft)
        self.assertEqual(draft["project_id"], "p1")
        self.assertEqual(draft["supervisor"]["model"], "GPT-5.6 Sol")
        self.assertTrue(draft["zcode"]["acknowledged"])
        self.assertEqual(draft["inputs"]["decision"], "NONE_NEEDED")
        normalized, note = setup.normalize_draft(draft)
        self.assertIsNone(note)
        self.assertEqual(normalized, draft)

    def test_normalize_rejects_corrupt_documents(self):
        for bad in (None, [], "x", 3, {}, {"schema_version": 2},
                    {"schema_version": 1, "project_id": 17},
                    {"schema_version": 1, "goal_markdown": 99}):
            normalized, note = setup.normalize_draft(bad)
            self.assertIsNone(normalized, repr(bad))
            self.assertTrue(note)

    def test_merge_input_inventory_bounds(self):
        draft = setup.empty_draft()
        huge = {"decision": "REGISTERED", "entries": [
            {"path": f"e{i}", "name": f"e{i}", "kind": "file",
             "size_bytes": 1} for i in range(
                 setup.INPUT_INVENTORY_MAX_ENTRIES + 1)],
            "selections": [], "total_entries": 2001, "complete": True,
            "honesty": {"notes": []}}
        with self.assertRaises(web_console_control.ControlRequestError):
            setup.merge_inputs_into_draft(draft, huge)


if __name__ == "__main__":
    unittest.main()
