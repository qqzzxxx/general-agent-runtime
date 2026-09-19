"""Ordinary semantic proposals through the real control/archive/claim boundaries."""
import contextlib
import io
import json
import re
import unittest
from unittest.mock import patch
from unittest.mock import Mock

from scripts import test_supervisor_control as fixtures

sc = fixtures.sc
executor_claim = fixtures.executor_claim
import ordinary_dispatch as od


def proposal():
    return {"logical_task": "summary", "logical_stage": "check evidence",
            "objective": "Produce a checked summary of the supplied evidence.",
            "inputs": ["evidence/source.txt"], "outputs": ["reports/summary.md"],
            "acceptance_criteria": ["Every finding cites supporting evidence."],
            "max_time": 900, "max_retries": 2}


class OrdinaryDispatchTests(unittest.TestCase):
    def setUp(self):
        self.f = fixtures.SupervisorControlTests()
        self.f.setUp()
        self.addCleanup(self.f.tearDown)
        self.o = self.f.configured_orchestrator()
        self.root = self.f.root

    def begin(self, value=None):
        turn = sc.begin_supervisor_turn(self.root, self.f.PROJECT,
                                        ordinary_dispatch_required=True)
        state = self.f.read_state()
        decision = {"decision": "CONTINUE", "reason": "Check the supplied evidence"}
        state["last_supervisor_decision"] = dict(decision)
        state["decision_history"].append(decision)
        state["status"] = "WAITING_EXECUTOR"
        state["ordinary_task_proposal"] = proposal() if value is None else value
        self.f._json(self.o.PROJECT_STATE, state)
        return turn

    def finish(self, turn):
        return sc.finish_supervisor_turn(self.root, turn, processed=True,
                                         candidate_validator=self.o.validate_supervisor_candidate_snapshot)

    def task(self):
        return sc._parse_dispatch_bytes(self.o.TO_ZCODE.read_bytes())

    def claim(self, task):
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            return executor_claim.acquire(self.root, *[task[k] for k in sc.IDENTITY_KEYS])

    def authorize(self, turn):
        return self.o.register_dispatched_task(self.o.load_runtime(), self.f.read_state(),
                                               expected_control_revision=turn["revision"])

    def test_success_preserves_semantics_and_requires_archive_authorization(self):
        turn = self.begin()
        self.assertFalse(self.finish(turn))
        task = self.task()
        state = self.f.read_state()
        for key in ("objective", "inputs", "outputs", "acceptance_criteria", "max_time"):
            self.assertEqual(task[key.upper()], proposal()[key])
        self.assertEqual(task["EXECUTOR_PROTOCOL"], list(od.EXECUTOR_PROTOCOL))
        self.assertEqual(task["FORBIDDEN_ACTIONS"], list(od.BASE_RESTRICTIONS))
        self.assertEqual(task["ATTEMPT"], 1)
        self.assertEqual(task["MESSAGE_ID"], 700120)
        self.assertEqual(state["next_message_id"], task["MESSAGE_ID"] + 1)
        self.assertNotIn("ordinary_task_proposal", state)
        self.assertEqual(state["last_supervisor_decision"], state["decision_history"][-1])
        self.assertEqual(state["last_supervisor_decision"]["message_id"], task["MESSAGE_ID"])
        self.assertEqual(self.claim(task), executor_claim.EXIT_ERROR)
        self.assertFalse(sc.list_dispatches(self.root, self.f.PROJECT))
        self.authorize(turn)
        self.assertEqual(sc.list_dispatches(self.root, self.f.PROJECT)[0]["integrity"], "AUTHORIZED_VALID")
        self.assertEqual(self.claim(task), executor_claim.EXIT_ACQUIRED)
        self.assertEqual(self.claim(task), executor_claim.EXIT_CLAIM_EXISTS)
        wire = self.o.TO_ZCODE.read_bytes()
        with self.assertRaises(sc.ControlError):
            self.finish(turn)
        self.assertEqual(self.o.TO_ZCODE.read_bytes(), wire)

    def test_invalid_proposals_never_reserve_or_publish(self):
        cases = [{}, {**proposal(), "NONCE": "model-nonce"},
                 {**proposal(), "objective": " "}, {**proposal(), "inputs": [{}]},
                 {**proposal(), "outputs": []}, {**proposal(), "acceptance_criteria": []},
                 {**proposal(), "max_time": True}, {**proposal(), "max_time": 2701},
                 {**proposal(), "max_retries": 3},
                 {**proposal(), "execution": {"capabilities": {"shell": "allow"}}},
                 {**proposal(), "execution": {"autonomy": "UNBOUNDED"}},
                 {**proposal(), "FINAL_VERIFICATION_REQUEST": {}}]
        for value in cases:
            with self.subTest(value=value):
                turn = self.begin(value)
                self.assertTrue(self.finish(turn))
                result = sc.load_control(self.root)["last_supervisor_turn_result"]
                self.assertTrue(result["candidate_validation_failed"])
                self.assertFalse(od.preparation_path(self.root, turn).exists())
                self.assertFalse(self.o.TO_ZCODE.exists())
                self.assertIsNone(self.f.read_runtime()["authorized_dispatch"])

    def test_execution_policy_is_semantic_sealed_and_not_a_permission_override(self):
        value = {**proposal(), "execution": {"autonomy": "HIGH", "read_paths": ["evidence/source.txt"]}}
        turn = self.begin(value)
        self.assertFalse(self.finish(turn))
        self.authorize(turn)
        import executor_entry
        view = executor_entry.enter(self.root, contract_version=2)
        self.assertEqual(view["status"], "READY", view)
        self.assertEqual(view["contract"]["autonomy"]["level"], "HIGH")
        self.assertEqual(view["contract"]["capabilities"]["filesystem"]["project_read_paths"], ["evidence/source.txt"])
        self.assertEqual(view["contract"]["capabilities"]["shell"]["available"], "session_dependent")
        self.assertEqual(self.task()["EXECUTION"]["autonomy"], "HIGH")
        self.o.TO_ZCODE.write_bytes(self.o.TO_ZCODE.read_bytes().replace(b'"HIGH"', b'"LOW"'))
        self.assertEqual(executor_entry.enter(self.root, contract_version=2, resume_token=view["session"])["status"], "NOT_AUTHORIZED")

    def test_phase5_network_grant_is_sealed_and_cannot_expand_after_claim(self):
        value = {**proposal(), "execution": {"autonomy": "HIGH",
                 "capabilities": {"network": "https_get"}, "network_urls": ["https://example.com/"]}}
        turn = self.begin(value)
        self.assertFalse(self.finish(turn))
        self.authorize(turn)
        import executor_entry
        view = executor_entry.enter(self.root, contract_version=2)
        self.assertEqual(view["status"], "READY", view)
        self.assertEqual(view["contract"]["capabilities"]["network"]["urls"], ["https://example.com/"])
        self.assertEqual(self.task()["EXECUTION"]["network_urls"], ["https://example.com/"])
        self.o.TO_ZCODE.write_bytes(self.o.TO_ZCODE.read_bytes().replace(b'example.com', b'other.example'))
        self.assertEqual(executor_entry.enter(self.root, contract_version=2,
                         resume_token=view["session"])["status"], "NOT_AUTHORIZED")

    def test_outcome_context_reaches_executor_v2_under_existing_authority(self):
        context = {"desired_outcome": "A usable product", "quality_bar": ["Clear next action"],
                   "hard_constraints": [{"constraint": "Preserve information and actions", "source": "PROJECT_GOAL.md"}],
                   "current_facts": ["One page of tables"],
                   "revisable_assumptions": ["Layout, wording and implementation may change"]}
        value = {**proposal(), "outcome_context": context,
                 "execution": {"autonomy": "HIGH", "capabilities": {"network": "none"}}}
        turn = self.begin(value)
        self.assertFalse(self.finish(turn))
        task = self.task()
        self.assertEqual(task["OUTCOME_CONTEXT"], context)
        self.assertEqual(self.claim(task), executor_claim.EXIT_ERROR)
        self.authorize(turn)
        import executor_entry
        view = executor_entry.enter(self.root, contract_version=2)
        self.assertEqual(view["status"], "READY", view)
        self.assertEqual(view["contract"]["context"]["task_data"]["OUTCOME_CONTEXT"], context)
        self.assertEqual(view["contract"]["capabilities"]["network"]["mode"], "none")
        self.o.TO_ZCODE.write_bytes(self.o.TO_ZCODE.read_bytes().replace(b'A usable product', b'A different product'))
        self.assertEqual(executor_entry.enter(self.root, contract_version=2,
                         resume_token=view["session"])["status"], "NOT_AUTHORIZED")

    def test_invalid_outcome_context_cannot_reserve_or_grant_capabilities(self):
        valid = {"desired_outcome": "Clear console", "quality_bar": [], "hard_constraints": [],
                 "current_facts": [], "revisable_assumptions": []}
        cases = [{}, {**valid, "NONCE": "injected"}, {**valid, "quality_bar": "one"},
                 {**valid, "hard_constraints": [{"constraint": "freeze layout"}]},
                 {**valid, "hard_constraints": [{"constraint": "freeze layout", "source": ""}]},
                 {**valid, "execution": {"capabilities": {"network": "host"}}}]
        for context in cases:
            with self.subTest(context=context):
                turn = self.begin({**proposal(), "outcome_context": context})
                self.assertTrue(self.finish(turn))
                self.assertFalse(od.preparation_path(self.root, turn).exists())
                self.assertFalse(self.o.TO_ZCODE.exists())

    def test_model_cannot_supply_projection_or_wire(self):
        for field in ("current_task", "next_message_id", "message_id", "wire"):
            with self.subTest(field=field):
                turn = self.begin()
                state = self.f.read_state()
                if field == "wire":
                    self.o.TO_ZCODE.write_bytes(fixtures.wire(self.f.task))
                elif field == "message_id":
                    state["last_supervisor_decision"][field] = 700200
                else:
                    state[field] = 700200
                self.f._json(self.o.PROJECT_STATE, state)
                self.assertTrue(self.finish(turn))
                self.assertFalse(od.preparation_path(self.root, turn).exists())

    def test_stale_revision_does_not_construct(self):
        turn = self.begin()
        sc.submit_intervention(self.root, b"Use a narrower source set.")
        self.assertTrue(self.finish(turn))
        self.assertTrue(sc.load_control(self.root)["last_supervisor_turn_result"]["stale"])
        self.assertFalse(od.preparation_path(self.root, turn).exists())
        self.assertFalse(self.o.TO_ZCODE.exists())
        self.assertEqual(self.f.read_state()["status"], "SUPERVISOR_TURN")

    def test_pause_before_preparation_restores_resumable_semantic_state(self):
        turn = self.begin()
        sc.set_pause(self.root)
        self.assertTrue(self.finish(turn))
        self.assertFalse(od.preparation_path(self.root, turn).exists())
        self.assertEqual(self.f.read_state()["status"], "SUPERVISOR_TURN")
        self.assertIsNone(self.f.read_state()["current_task"])
        self.assertFalse(self.f.read_runtime()["retired_message_ids"])

    def test_human_decision_full_wire_ignores_leftover_failed_ordinary_proposal(self):
        turn = sc.begin_supervisor_turn(self.root, self.f.PROJECT,
                                        {"reason": "HUMAN_DECISION_RESUME"})
        state = self.f.read_state()
        decision = {"decision": "CONTINUE", "reason": "Human Decision compatibility"}
        state.update(status="WAITING_EXECUTOR", ordinary_task_proposal={"invalid": "leftover"},
                     current_task={k: self.f.task[k] for k in sc.IDENTITY_KEYS},
                     last_supervisor_decision=decision, decision_history=[decision])
        self.f._json(self.o.PROJECT_STATE, state)
        self.o.TO_ZCODE.write_bytes(fixtures.wire(self.f.task))
        self.assertFalse(self.finish(turn))
        self.assertFalse(od.preparation_path(self.root, turn).exists())

    def test_revision_after_preparation_prevents_registration(self):
        turn = self.begin()
        self.assertFalse(self.finish(turn))
        task = self.task()
        sc.submit_intervention(self.root, b"Reassess source quality.")
        with self.assertRaises(RuntimeError):
            self.authorize(turn)
        self.assertEqual(self.claim(task), executor_claim.EXIT_ERROR)

    def test_restart_reuses_exact_reservation_at_every_write_boundary(self):
        for point in ("ordinary_after_reservation", "ordinary_after_state", "ordinary_after_inbox",
                      "supervisor_after_decision_receipt", "supervisor_after_candidate_origin"):
            with self.subTest(point=point):
                turn = self.begin()
                def crash(name):
                    if name == point:
                        raise SystemExit("simulated crash")
                with patch.object(sc, "_failure_point", side_effect=crash):
                    with self.assertRaises(SystemExit):
                        self.finish(turn)
                record = sc._read_json(od.preparation_path(self.root, turn))
                saved = od.preparation_path(self.root, turn).read_bytes()
                if self.o.TO_ZCODE.exists():
                    self.assertEqual(self.claim(record["task"]), executor_claim.EXIT_ERROR)
                result = sc.reconcile_inflight_turn(self.root,
                                                    self.o.validate_supervisor_candidate_snapshot)
                self.assertTrue(result["decision_committed"], result)
                self.assertEqual(self.task(), record["task"])
                self.assertEqual(od.preparation_path(self.root, turn).read_bytes(), saved)
                self.assertIsNone(sc.reconcile_inflight_turn(self.root))

    def test_restart_with_stale_revision_burns_reservation(self):
        turn = self.begin()
        def crash(name):
            if name == "ordinary_after_reservation":
                raise SystemExit()
        with patch.object(sc, "_failure_point", side_effect=crash):
            with self.assertRaises(SystemExit):
                self.finish(turn)
        reserved = sc._read_json(od.preparation_path(self.root, turn))["task"]
        sc.submit_intervention(self.root, b"Correct the objective.")
        self.assertTrue(sc.reconcile_inflight_turn(self.root)["stale"])
        next_turn = self.begin()
        self.assertFalse(self.finish(next_turn))
        self.assertGreater(self.task()["MESSAGE_ID"], reserved["MESSAGE_ID"])
        self.assertNotEqual(self.task()["NONCE"], reserved["NONCE"])

    def test_claimed_retry_has_fresh_identity_and_bounded_attempts(self):
        tasks = []
        for attempt in range(1, 4):
            turn = self.begin()
            self.assertFalse(self.finish(turn))
            task = self.authorize(turn)
            self.assertEqual(self.claim(task), executor_claim.EXIT_ACQUIRED)
            self.assertEqual(task["ATTEMPT"], attempt)
            tasks.append(task)
            runtime = self.o.load_runtime()
            self.o.retire_executor(runtime, task, "EXECUTOR_TIMEOUT")
            self.o.save_runtime(runtime)
        self.assertEqual(len({t["MESSAGE_ID"] for t in tasks}), 3)
        self.assertEqual(len({t["NONCE"] for t in tasks}), 3)
        self.assertEqual(len({t["TASK_ID"] for t in tasks}), 1)
        turn = self.begin()
        self.assertTrue(self.finish(turn))
        self.assertIn("retry budget exhausted", sc.load_control(self.root)["last_supervisor_turn_result"]["error"])
        for task in tasks:
            self.assertTrue(executor_claim.claim_dir(self.root, task["MESSAGE_ID"], task["NONCE"]).exists())

    def test_restart_rejects_edited_state(self):
        turn = self.begin()
        def crash(name):
            if name == "ordinary_after_reservation":
                raise SystemExit()
        with patch.object(sc, "_failure_point", side_effect=crash):
            with self.assertRaises(SystemExit):
                self.finish(turn)
        state = self.f.read_state()
        state["ordinary_task_proposal"]["objective"] = "Changed after reservation"
        self.f._json(self.o.PROJECT_STATE, state)
        result = sc.reconcile_inflight_turn(self.root, self.o.validate_supervisor_candidate_snapshot)
        self.assertFalse(result["decision_committed"])
        self.assertFalse(self.o.TO_ZCODE.exists())

    def test_production_invoke_uses_semantics_and_registers_runtime_projection(self):
        def model_result(*args, **kwargs):
            state = self.f.read_state()
            decision = {"decision": "CONTINUE", "reason": "Review the evidence"}
            state["decision_history"].append(decision)
            state.update(last_supervisor_decision=dict(decision), status="WAITING_EXECUTOR",
                         ordinary_task_proposal=proposal())
            self.f._json(self.o.PROJECT_STATE, state)
            return Mock(returncode=0)
        with patch.object(self.o, "find_codex", return_value="codex"), \
                patch.object(self.o, "goal_anchor_gate", return_value=True), \
                patch.object(self.o, "build_codex_prompt", return_value="fixture"), \
                patch.object(self.o.subprocess, "run", side_effect=model_result):
            self.o.invoke_codex(self.o.load_runtime(), "SUPERVISOR_TURN")
        task = self.task()
        self.assertEqual(self.f.read_runtime()["authorized_dispatch"]["MESSAGE_ID"], task["MESSAGE_ID"])
        self.assertEqual(self.claim(task), executor_claim.EXIT_ACQUIRED)

    def test_invalid_semantics_use_existing_restart_persistent_repair_budget(self):
        def model_result(*args, **kwargs):
            state = self.f.read_state()
            decision = {"decision": "CONTINUE", "reason": "Malformed proposal"}
            state["decision_history"].append(decision)
            state.update(last_supervisor_decision=dict(decision), status="WAITING_EXECUTOR",
                         ordinary_task_proposal={**proposal(), "max_time": "15 minutes"})
            self.f._json(self.o.PROJECT_STATE, state)
            return Mock(returncode=0)
        with patch.object(self.o, "find_codex", return_value="codex"), \
                patch.object(self.o, "goal_anchor_gate", return_value=True), \
                patch.object(self.o, "build_codex_prompt", return_value="fixture"), \
                patch.object(self.o.subprocess, "run", side_effect=model_result):
            self.o.invoke_codex(self.o.load_runtime(), "SUPERVISOR_TURN")
            self.assertEqual(self.o.load_runtime()["pending_supervisor_event"]["decision_attempts"], 1)
            self.o.invoke_codex(self.o.load_runtime(), "SUPERVISOR_TURN")
        self.assertEqual(self.f.read_state()["status"], "HUMAN_REVIEW")
        self.assertTrue(self.o.load_runtime()["pending_supervisor_event"]["retry_exhausted"])
        self.assertIsNone(self.o.load_runtime()["authorized_dispatch"])
        self.assertFalse(self.o.TO_ZCODE.exists())

    def test_global_uniqueness_and_no_reuse_after_failed_validation(self):
        # An abandoned candidate still reserves its MESSAGE_ID, without spending
        # an Executor attempt. A new project with a lower counter cannot reuse it.
        first = self.begin()
        self.assertTrue(sc.finish_supervisor_turn(self.root, first, processed=True,
            candidate_validator=Mock(side_effect=RuntimeError("reject for test"))))
        task = self.task()
        other = self.root / "projects" / "another"
        self.f._json(other / "project_state.json", {
            "project_id": "another", "status": "SUPERVISOR_TURN", "current_task": None,
            "next_message_id": 1, "decision_history": []})
        self.f._json(self.root / "control" / "ACTIVE_PROJECT.json", {
            "schema_version": 1, "project_id": "another", "project_root": "projects/another"})
        next_turn = sc.begin_supervisor_turn(self.root, "another", ordinary_dispatch_required=True)
        state = sc._read_json(other / "project_state.json")
        decision = {"decision": "CONTINUE", "reason": "New project"}
        state.update(status="WAITING_EXECUTOR", ordinary_task_proposal=proposal(),
                     decision_history=[decision], last_supervisor_decision=dict(decision))
        self.f._json(other / "project_state.json", state)
        self.assertFalse(sc.finish_supervisor_turn(self.root, next_turn, processed=True))
        self.assertGreater(self.task()["MESSAGE_ID"], task["MESSAGE_ID"])
        self.assertNotEqual(self.task()["TASK_ID"], task["TASK_ID"])
        self.assertNotEqual(self.task()["NONCE"], task["NONCE"])
        self.assertEqual(self.task()["ATTEMPT"], 1)

    def test_retry_intent_cannot_increase_original_limit(self):
        turn = self.begin({**proposal(), "max_retries": 0})
        self.assertFalse(self.finish(turn))
        self.authorize(turn)
        next_turn = self.begin({**proposal(), "max_retries": 2})
        self.assertTrue(self.finish(next_turn))
        self.assertIn("retry budget exhausted", sc.load_control(self.root)["last_supervisor_turn_result"]["error"])

    def test_canonical_protocol_preserves_legacy_executor_contract(self):
        template = (fixtures.REPO / "control" / "EXECUTOR_TASK_TEMPLATE.md").read_text(encoding="utf-8")
        wire = json.loads(re.findall(r"```json\s*\n(.*?)\n```", template, re.S)[0])
        self.assertEqual(wire["EXECUTOR_PROTOCOL"], list(od.EXECUTOR_PROTOCOL))
        self.assertEqual(wire["FORBIDDEN_ACTIONS"], list(od.BASE_RESTRICTIONS))

    def test_measurement_compares_equal_work_without_token_or_latency_inference(self):
        from scripts.measure_runtime_owned_dispatch import report
        measured = report()
        output = measured["authored_dispatch_surface"]
        old = output["before_fixture"]["executor_task"]
        new = output["after_fixture"]["ordinary_task_proposal"]
        for key in ("objective", "inputs", "outputs", "acceptance_criteria", "stop_conditions", "max_time", "max_retries"):
            self.assertEqual(old[key.upper()], new[key])
        self.assertIsNone(measured["provider_token_savings"])
        self.assertIsNone(measured["latency_savings_seconds"])
        self.assertEqual(output["reduction_characters"], output["before"]["characters"] - output["after"]["characters"])
class SupervisorContractAuthorityGuidanceTests(unittest.TestCase):
    """The delegation contract teaches Runtime-projected context; the Executor
    authority boundary itself stays mechanical and fail-closed."""

    def test_contract_states_authority_boundary_and_projection_path(self):
        text = " ".join(od.SUPERVISOR_CONTRACT.split())
        self.assertIn("read_paths authorize only project working-scope paths", text)
        self.assertIn("RESEARCH_STATE.md", text)
        self.assertIn("project_state.json", text)
        self.assertIn("never Executor inputs", text)
        self.assertIn("Runtime-projected", text)
        self.assertIn("distill whatever it needs from those records into inputs", text)
        # The wording that invited authority files as supplied inputs is gone.
        self.assertNotIn("Set read_paths for supplied inputs", text)

    def test_supervisor_still_receives_research_state_for_decisions(self):
        # Availability for the SUPERVISOR is unchanged: research state reaches the
        # decision prompt via Runtime projection, never via Executor read_paths.
        text = " ".join(od.SUPERVISOR_CONTRACT.split())
        self.assertIn("update project memory as needed", text)


if __name__ == "__main__":
    unittest.main()
