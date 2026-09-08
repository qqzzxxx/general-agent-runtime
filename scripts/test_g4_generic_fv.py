"""G4 targeted regression: Generic Final Verification Framework (G4-01..20).

Centerpiece: the BUSINESS differential matrix — the pre-G4 (G3) production evaluator and
the G4 policy-driven evaluator must produce IDENTICAL mechanical_pass and identical
failing-claim sets across every V1.5 business semantics shape.
"""
import copy
import importlib.util
import json
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

CANDIDATE = Path(__file__).resolve().parents[1]
LAB = CANDIDATE  # Optional audit fixtures must never be loaded from a sibling tree.
PRE_G4 = LAB / "audit" / "implementation_g4" / "pre_g4_orchestrator.py"


def load(name, source_path, root):
    spec = importlib.util.spec_from_file_location(name, source_path)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    for attr, val in dict(
        ROOT=root, CONTROL=root / "control", LOGS=root / "logs",
        HANDOFF_ARCHIVE=root / "handoff" / "archive", REPORTS=root / "reports",
        PROJECT_STATE=root / "control" / "project_state.json",
        RUNTIME_STATE=root / "control" / "orchestrator_runtime.json",
        SUPERVISOR_RULES=root / "control" / "CODEX_SUPERVISOR_RUNTIME.md",
        RESEARCH_STATE=root / "RESEARCH_STATE.md",
        COMMERCIAL_GOAL=root / "control" / "CROSS_BORDER_GOAL.md",
        TO_ZCODE=root / "TO_ZCODE.md", SUPERVISOR_BRIEF=root / "SUPERVISOR_BRIEF.md",
        ZCODE_DONE=root / "ZCODE_DONE.flag",
        ZCODE_LAST_PROCESSED=root / "ZCODE_LAST_PROCESSED.txt",
        STOP_FLAG=root / "control" / "STOP", HUMAN_REVIEW_FLAG=root / "control" / "HUMAN_REVIEW",
        LOCK_FILE=root / "control" / ".orchestrator.lock",
        CODEX_LAST_OUTPUT=root / "CODEX_LAST_OUTPUT.txt",
        USER_ATTENTION=root / "control" / "USER_ATTENTION.json",
        USER_STATUS_REPORT=root / "reports" / "USER_STATUS.md",
        ACTIVE_PROJECT_FILE=root / "control" / "ACTIVE_PROJECT.json",
    ).items():
        setattr(m, attr, val)
    m.DESKTOP_NOTIFICATIONS_ENABLED = False
    m.USER_NOTIFICATION_CONSOLE_ENABLED = False
    return m


def wire(v):
    return "```json\n" + json.dumps(v, ensure_ascii=False, indent=2) + "\n```\n"


def claim(cid, ctype, impact="HIGH"):
    return {"claim_id": cid, "claim": f"claim {cid}", "claim_type": ctype,
            "decision_impact": impact, "evidence_pointers": [f"ev/{cid}"],
            "verification_standard": "standard"}


def failing_claims(issues):
    """Extract claim ids from claim-scoped issues ('<ID>: ...'). Any short, space-free
    uppercase-ish token counts (A1, S3, C10, ...) — not just C-prefixed ids."""
    import re
    out = set()
    for issue in issues:
        first = issue.split(":", 1)[0].strip()
        if first and " " not in first and len(first) <= 8 and re.fullmatch(r"[A-Za-z0-9._-]+", first):
            out.add(first)
    return out


class G4Base(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="g4-")
        self.root = Path(self.temp.name)
        (self.root / "control").mkdir()
        (self.root / "reports").mkdir()
        shutil.copytree(CANDIDATE / "profiles", self.root / "profiles")
        self.cand = load("g4_candidate", CANDIDATE / "orchestrator.py", self.root)
        # loader tests mutate the sandbox copy: PROFILES_DIR points at the sandbox profiles
        self.cand.PROFILES_DIR = self.root / "profiles"
        self.pre = (load("g4_pre", PRE_G4, self.root)
                    if PRE_G4.exists() else None)  # pre-G4 snapshot exists only in the lab
        self.business_policy = self.cand.load_policy_document(
            self.root / "profiles" / "BUSINESS_RESEARCH", "BUSINESS_RESEARCH_FV_V1")

    def tearDown(self):
        self.temp.cleanup()

    def gate_for(self, claims, policy_id=None, policy_version=None):
        gate = {"POLICY_VERSION": 1,
                "CLAIMS_HASH": self.cand.canonical_claims_hash(claims),
                "CLAIM_COUNT": len(claims), "CRITICAL_CLAIMS": claims}
        if policy_id is not None:
            gate["POLICY_ID"] = policy_id
        if policy_version is not None:
            gate["POLICY_VERSION"] = policy_version
        return {"TASK_KIND": "FINAL_VERIFICATION", "MESSAGE_ID": 800001,
                "TASK_ID": "FV", "STAGE_ID": "S", "ATTEMPT": 1, "NONCE": "n",
                "CLAIM_PROTOCOL_VERSION": 1, "OBJECTIVE": "verify", "OUTPUTS": [],
                "FINAL_VERIFICATION_GATE": gate}

    def fv_state(self, claims, *, status="PENDING", policy_id=None, policy_version=None,
                 commercial=True):
        state = {"status": "WAITING_EXECUTOR",
                 "phase": "CROSS_BORDER_ECOMMERCE_AGENT_V2" if commercial else "GENERAL_TEST",
                 "commercial_authorized": commercial,
                 "started_at": "2030-06-01T00:00:00+00:00",
                 "current_task": None, "profile": None}
        if policy_id:
            state["profile"] = policy_id
        state["final_verification"] = {
            "policy_version": 1, "required": True, "status": status,
            "critical_claims": claims,
            "claims_hash": self.cand.canonical_claims_hash(claims)}
        if policy_id:
            state["final_verification"]["policy_id"] = f"{policy_id}_FV_V1"
            state["final_verification"]["policy_version"] = 1
        if policy_version is not None:
            state["final_verification"]["policy_version"] = policy_version
        return state

    def result_row(self, cid, status, checks=None):
        return {"claim_id": cid, "status": status, "checks": checks or {},
                "evidence_pointers": [], "auditor_note": ""}


class G4SchemaTests(G4Base):
    # G4-01: policy schema validation (valid + every invalid shape)
    def test_g4_01_policy_schema_validation(self):
        base = json.loads((self.root / "profiles" / "GENERAL" / "FINAL_VERIFICATION_POLICY.json")
                          .read_text(encoding="utf-8"))
        self.cand._validate_policy_document(base, "GENERAL_FV_V1")  # valid
        def mutate(**over):
            doc = copy.deepcopy(base)
            for k, v in over.items():
                if v is None:
                    doc.pop(k, None)
                else:
                    doc[k] = v
            return doc
        bad = [
            mutate(policy_schema_version=2), mutate(policy_id=None),
            mutate(policy_version=0), mutate(claim_count={"min": 5, "max": 3}),
            mutate(allowed_result_statuses=[]),
            mutate(impact_acceptance={"HIGH": ["SUPPORTED"]}),
            mutate(impact_acceptance={"HIGH": [], "MEDIUM": ["SUPPORTED"]}),  # empty acceptance
            mutate(unknown_claim_type="WHATEVER"),
            mutate(claim_types={"X": {"required_checks": [{"field": "f", "op": "MAGIC"}]}}),
            mutate(claim_types={"X": {"required_checks": [{"field": "f", "op": "EQUALS"}]}}),  # EQUALS w/o value
            mutate(claim_types={"X": {"required_checks": [{"field": "f", "op": "INTEGER_GTE", "value": "2"}]}}),
        ]
        for i, doc in enumerate(bad):
            with self.assertRaises(RuntimeError, msg=f"case {i}"):
                self.cand._validate_policy_document(doc, None)

    # G4-02: profile -> policy resolution
    def test_g4_02_profile_policy_resolution(self):
        for pid, pol_id in (("GENERAL", "GENERAL_FV_V1"),
                            ("ACADEMIC_RESEARCH", "ACADEMIC_FV_V1"),
                            ("SOFTWARE_ENGINEERING", "SOFTWARE_ENGINEERING_FV_V1"),
                            ("BUSINESS_RESEARCH", "BUSINESS_RESEARCH_FV_V1")):
            prof = self.cand.load_profile(pid)
            self.assertEqual(prof["final_verification_policy"]["policy_id"], pol_id)
            self.assertEqual(prof["final_verification_policy_id"], pol_id)

    # G4-03: invalid/missing policy -> fail closed via load_profile
    def test_g4_03_invalid_or_missing_policy_fails_closed(self):
        d = self.root / "profiles" / "GENERAL"
        (d / "FINAL_VERIFICATION_POLICY.json").unlink()
        with self.assertRaisesRegex(RuntimeError, "missing or invalid"):
            self.cand.load_profile("GENERAL")
        (d / "FINAL_VERIFICATION_POLICY.json").write_text('{"broken"', encoding="utf-8")
        with self.assertRaises(RuntimeError):
            self.cand.load_profile("GENERAL")

    # G4-04: unknown operator -> fail closed
    def test_g4_04_unknown_operator_fails_closed(self):
        doc = json.loads((self.root / "profiles" / "GENERAL" / "FINAL_VERIFICATION_POLICY.json")
                         .read_text(encoding="utf-8"))
        doc["claim_types"]["FACTUAL"]["required_checks"].append(
            {"field": "x", "op": "REGEX_MATCH", "value": ".*"})
        with self.assertRaisesRegex(RuntimeError, "unknown operator"):
            self.cand._validate_policy_document(doc, None)

    # G4-14: unknown claim type — FAIL_CLOSED outside business, ENVELOPE_ONLY inside
    def test_g4_14_unknown_claim_type(self):
        task = self.gate_for(claim("C1", "MYSTERY_TYPE") | {} and
                             [claim("C1", "MYSTERY_TYPE"), claim("C2", "MYSTERY_TYPE"),
                              claim("C3", "MYSTERY_TYPE")])
        brief = {"FINAL_VERIFICATION": {"POLICY_VERSION": 1,
                 "CLAIMS_HASH": task["FINAL_VERIFICATION_GATE"]["CLAIMS_HASH"],
                 "OVERALL_STATUS": "PASS",
                 "CLAIM_RESULTS": [self.result_row(f"C{i}", "SUPPORTED") for i in (1, 2, 3)]}}
        res_business = self.cand.evaluate_final_verification_receipt(task, brief, self.business_policy)
        self.assertTrue(res_business["mechanical_pass"])  # business: ENVELOPE_ONLY (V1.5 compat)
        academic = self.cand.load_policy_document(
            self.root / "profiles" / "ACADEMIC_RESEARCH", "ACADEMIC_FV_V1")
        res_academic = self.cand.evaluate_final_verification_receipt(task, brief, academic)
        self.assertFalse(res_academic["mechanical_pass"])  # academic: FAIL_CLOSED
        self.assertTrue(any("not in the active policy taxonomy" in i for i in res_academic["issues"]))


class G4BusinessDifferentialTests(G4Base):
    """G4-05: PRE-G4 evaluator vs G4 BUSINESS policy evaluator over the full matrix."""

    BUSINESS_TYPES = ("IP", "ECONOMICS", "CUSTOMER_PAIN", "DEMAND", "REGULATORY", "SAFETY")

    def build(self, claims, results, *, overall="PASS", claims_hash=None, receipt_hash=None):
        task = self.gate_for(claims)
        if claims_hash is not None:
            task["FINAL_VERIFICATION_GATE"]["CLAIMS_HASH"] = claims_hash
        brief = {"FINAL_VERIFICATION": {"POLICY_VERSION": 1,
                 "CLAIMS_HASH": receipt_hash or task["FINAL_VERIFICATION_GATE"]["CLAIMS_HASH"],
                 "OVERALL_STATUS": overall, "CLAIM_RESULTS": results}}
        return task, brief

    def good_checks(self, ctype):
        return {
            "IP": {"authoritative_identifier_check": "PASS",
                   "authoritative_source_pointers": ["USPTO"]},
            "ECONOMICS": {"mechanical_recalculation": "PASS",
                          "calculation_artifact": "workspace/recalc.json"},
            "CUSTOMER_PAIN": {"source_independence_check": "PASS",
                              "independent_underlying_sources": 3},
            "DEMAND": {"source_independence_check": "PASS",
                       "independent_underlying_sources": 2},
            "REGULATORY": {"authoritative_source_check": "PASS",
                           "authoritative_source_pointers": ["reg.gov"]},
            "SAFETY": {"authoritative_source_check": "PASS",
                       "authoritative_source_pointers": ["osha.gov"]},
        }[ctype]

    def fixtures(self):
        def triple(t1, t2, t3):
            claims = [claim("C1", t1), claim("C2", t2), claim("C3", t3, "MEDIUM")]
            results = [self.result_row("C1", "SUPPORTED", self.good_checks(t1)),
                       self.result_row("C2", "SUPPORTED", self.good_checks(t2)),
                       self.result_row("C3", "PARTIALLY_SUPPORTED", self.good_checks(t3))]
            return claims, results
        fx = {}
        # supported shapes per type
        for t in self.BUSINESS_TYPES:
            claims, results = triple(t, "IP", "ECONOMICS")
            claims[0], results[0] = claim("C1", t), self.result_row("C1", "SUPPORTED", self.good_checks(t))
            fx[f"supported_{t}"] = (claims, results, {})
        claims, results = triple("IP", "ECONOMICS", "CUSTOMER_PAIN")
        bad = copy.deepcopy(claims)
        # IP variants
        results2 = copy.deepcopy(results); results2[0]["checks"]["authoritative_identifier_check"] = "FAIL"
        fx["ip_bad_identifier"] = (claims, results2, {})
        results3 = copy.deepcopy(results); results3[0]["checks"]["authoritative_source_pointers"] = []
        fx["ip_missing_pointers"] = (claims, results3, {})
        # ECONOMICS variants
        results4 = copy.deepcopy(results); results4[1]["checks"]["mechanical_recalculation"] = "FAIL"
        fx["econ_failed_recalc"] = (claims, results4, {})
        results5 = copy.deepcopy(results); results5[1]["checks"]["calculation_artifact"] = ""
        fx["econ_missing_artifact"] = (claims, results5, {})
        # pain independence
        results6 = copy.deepcopy(results); results6[2]["checks"]["independent_underlying_sources"] = 1
        fx["pain_duplicate_source"] = (claims, results6, {})
        # HIGH unsupported / MEDIUM weak
        results7 = copy.deepcopy(results); results7[0]["status"] = "UNSUPPORTED"
        fx["high_unsupported"] = (claims, results7, {})
        results8 = copy.deepcopy(results); results8[2]["status"] = "WEAK"
        fx["medium_weak"] = (claims, results8, {})
        # coverage/hash/overall/statuses
        results9 = copy.deepcopy(results)[:2]  # missing C3
        fx["missing_claim"] = (claims, results9, {})
        results10 = copy.deepcopy(results); results10[2]["claim_id"] = "C1"  # dup C1, C3 missing
        fx["duplicate_claim"] = (claims, results10, {})
        results11 = copy.deepcopy(results); results11[1]["status"] = "SORT_OF_DONE"
        fx["unknown_status"] = (claims, results11, {})
        fx["overall_fail"] = (claims, copy.deepcopy(results), {"overall": "FAIL"})
        fx["wrong_claims_hash"] = (claims, copy.deepcopy(results),
                                   {"receipt_hash": "tampered"})  # receipt != gate hash
        return fx

    def test_g4_05_business_differential_matrix(self):
        matrix_results = []
        for name, (claims, results, opts) in self.fixtures().items():
            task, brief = self.build(claims, results, overall=opts.get("overall", "PASS"),
                                     claims_hash=opts.get("claims_hash"),
                                     receipt_hash=opts.get("receipt_hash"))
            post_default = self.cand.evaluate_final_verification_receipt(task, brief)
            post_policy = self.cand.evaluate_final_verification_receipt(task, brief, self.business_policy)
            if self.pre is not None:  # differential vs the pre-G4 build (lab-only artifact)
                pre_res = self.pre.evaluate_final_verification_receipt(task, brief)
                for label, post in (("default", post_default), ("explicit", post_policy)):
                    same_pass = pre_res["mechanical_pass"] == post["mechanical_pass"]
                    same_fail_set = failing_claims(pre_res["issues"]) == failing_claims(post["issues"])
                    same_count = len(pre_res["issues"]) == len(post["issues"])
                    self.assertTrue(same_pass and same_fail_set and same_count,
                                    f"{name}/{label}: pre={pre_res} post={post}")
            matrix_results.append({"fixture": name, "pass": post_default["mechanical_pass"],
                                   "failing": sorted(failing_claims(post_default["issues"]))})
        # charter §15 minimum coverage present
        names = {m["fixture"] for m in matrix_results}
        required = {"supported_IP", "ip_bad_identifier", "ip_missing_pointers",
                    "supported_ECONOMICS", "econ_failed_recalc", "econ_missing_artifact",
                    "supported_DEMAND", "pain_duplicate_source", "supported_REGULATORY",
                    "supported_SAFETY", "high_unsupported", "medium_weak", "missing_claim",
                    "duplicate_claim", "unknown_status", "overall_fail", "wrong_claims_hash"}
        self.assertTrue(required <= names, f"missing fixtures: {required - names}")
        # sanity: the all-good business fixture must PASS, every broken one must FAIL
        by_name = {m["fixture"]: m["pass"] for m in matrix_results}
        self.assertTrue(by_name["supported_IP"])
        self.assertFalse(by_name["ip_bad_identifier"])
        self.assertFalse(by_name["overall_fail"])
        self.assertFalse(by_name["wrong_claims_hash"])
        # Keep regression output inside the disposable fixture; test execution must not
        # mutate the lab parent or historical implementation evidence.
        out = self.root / "business_differential_matrix.json"
        out.write_text(json.dumps(matrix_results, indent=1), encoding="utf-8")


class G4GenericPolicyTests(G4Base):
    def gated_state(self, profile, claims, *, status="PENDING", policy_id=None,
                    policy_version=None):
        state = {"status": "WAITING_EXECUTOR", "phase": "GENERAL_TEST",
                 "commercial_authorized": False, "started_at": "2030-06-01T00:00:00+00:00",
                 "profile": profile, "current_task": None}
        fv = {"policy_version": 1, "required": True, "status": status,
              "critical_claims": claims,
              "claims_hash": self.cand.canonical_claims_hash(claims)}
        if policy_id:
            fv["policy_id"] = policy_id
        if policy_version is not None:
            fv["policy_version"] = policy_version
        state["final_verification"] = fv
        return state

    def academic_pass_fixture(self):
        claims = [claim("A1", "CORE_RESULT"), claim("A2", "BASELINE_COMPARISON", "MEDIUM"),
                  claim("A3", "REPRODUCIBILITY")]
        task = self.gate_for(claims, policy_id="ACADEMIC_FV_V1", policy_version=1)
        brief = {"FINAL_VERIFICATION": {"POLICY_VERSION": 1,
                 "CLAIMS_HASH": task["FINAL_VERIFICATION_GATE"]["CLAIMS_HASH"],
                 "OVERALL_STATUS": "PASS",
                 "CLAIM_RESULTS": [
                     self.result_row("A1", "SUPPORTED", {"reproduction_check": "PASS",
                                                         "result_artifact": "results.json"}),
                     self.result_row("A2", "PARTIALLY_SUPPORTED", {"baseline_check": "PASS",
                                                                   "baseline_artifact": "baseline.csv"}),
                     self.result_row("A3", "SUPPORTED", {"reproduction_check": "PASS",
                                                         "environment_config": ["env.yml"]})]}}
        return claims, task, brief

    # G4-06 + G4-11 (binding ok) + policy-bound dispatch
    def test_g4_06_academic_valid_pass(self):
        claims, task, brief = self.academic_pass_fixture()
        state = self.gated_state("ACADEMIC_RESEARCH", claims, policy_id="ACADEMIC_FV_V1",
                                 policy_version=1)
        state["current_task"] = task
        self.cand.validate_final_verification_dispatch(state, task)  # binding ok
        policy, route = self.cand._resolve_fv_policy_for_state(state)
        self.assertEqual(route, "profile")
        res = self.cand.evaluate_final_verification_receipt(task, brief, policy)
        self.assertTrue(res["mechanical_pass"], res["issues"])

    # G4-07: academic invalid required check -> FAIL
    def test_g4_07_academic_invalid_required_check(self):
        claims, task, brief = self.academic_pass_fixture()
        brief["FINAL_VERIFICATION"]["CLAIM_RESULTS"][0]["checks"]["reproduction_check"] = "FAIL"
        academic = self.cand.load_policy_document(
            self.root / "profiles" / "ACADEMIC_RESEARCH", "ACADEMIC_FV_V1")
        res = self.cand.evaluate_final_verification_receipt(task, brief, academic)
        self.assertFalse(res["mechanical_pass"])
        self.assertIn("A1", failing_claims(res["issues"]))

    # G4-08 + G4-09: software policy
    def swe_pass_fixture(self, *, drop_regression=False):
        claims = [claim("S1", "BUG_REPRODUCTION"), claim("S2", "PATCH_CORRECTNESS"),
                  claim("S3", "REGRESSION")]
        task = self.gate_for(claims, policy_id="SOFTWARE_ENGINEERING_FV_V1", policy_version=1)
        results = [self.result_row("S1", "SUPPORTED", {"reproduction_check": "PASS",
                                                       "reproduction_artifact": "trace.txt"}),
                   self.result_row("S2", "SUPPORTED", {"targeted_test_check": "PASS",
                                                       "patch_diff": "diff-001"}),
                   self.result_row("S3", "SUPPORTED", {"regression_suite_check": "PASS",
                                                       "regression_artifact": "suite.log"})]
        if drop_regression:
            results[2]["checks"].pop("regression_suite_check")
        brief = {"FINAL_VERIFICATION": {"POLICY_VERSION": 1,
                 "CLAIMS_HASH": task["FINAL_VERIFICATION_GATE"]["CLAIMS_HASH"],
                 "OVERALL_STATUS": "PASS", "CLAIM_RESULTS": results}}
        return claims, task, brief

    def test_g4_08_software_valid_pass(self):
        claims, task, brief = self.swe_pass_fixture()
        swe = self.cand.load_policy_document(
            self.root / "profiles" / "SOFTWARE_ENGINEERING", "SOFTWARE_ENGINEERING_FV_V1")
        res = self.cand.evaluate_final_verification_receipt(task, brief, swe)
        self.assertTrue(res["mechanical_pass"], res["issues"])

    def test_g4_09_software_missing_regression_fails(self):
        claims, task, brief = self.swe_pass_fixture(drop_regression=True)
        swe = self.cand.load_policy_document(
            self.root / "profiles" / "SOFTWARE_ENGINEERING", "SOFTWARE_ENGINEERING_FV_V1")
        res = self.cand.evaluate_final_verification_receipt(task, brief, swe)
        self.assertFalse(res["mechanical_pass"])
        self.assertIn("S3", failing_claims(res["issues"]))

    # G4-10: general valid pass
    def test_g4_10_general_valid_pass(self):
        claims = [claim("G1", "FACTUAL"), claim("G2", "NUMERICAL", "MEDIUM"),
                  claim("G3", "DELIVERABLE_INTEGRITY")]
        task = self.gate_for(claims, policy_id="GENERAL_FV_V1", policy_version=1)
        brief = {"FINAL_VERIFICATION": {"POLICY_VERSION": 1,
                 "CLAIMS_HASH": task["FINAL_VERIFICATION_GATE"]["CLAIMS_HASH"],
                 "OVERALL_STATUS": "PASS",
                 "CLAIM_RESULTS": [
                     self.result_row("G1", "SUPPORTED", {"source_support_check": "PASS",
                                                         "source_pointers": ["src"]}),
                     self.result_row("G2", "PARTIALLY_SUPPORTED", {"calculation_check": "PASS",
                                                                   "calculation_artifact": "calc.xlsx"}),
                     self.result_row("G3", "SUPPORTED", {"deliverable_check": "PASS",
                                                         "deliverable_path": "out/report.md"})]}}
        general = self.cand.load_policy_document(
            self.root / "profiles" / "GENERAL", "GENERAL_FV_V1")
        res = self.cand.evaluate_final_verification_receipt(task, brief, general)
        self.assertTrue(res["mechanical_pass"], res["issues"])

    # G4-11: state fv policy_id mismatch -> dispatch fails
    def test_g4_11_state_policy_mismatch_fails_dispatch(self):
        claims = [claim("A1", "CORE_RESULT"), claim("A2", "BASELINE_COMPARISON", "MEDIUM"),
                  claim("A3", "REPRODUCIBILITY")]
        task = self.gate_for(claims, policy_id="ACADEMIC_FV_V1", policy_version=1)
        state = self.gated_state("ACADEMIC_RESEARCH", claims, policy_id="ACADEMIC_FV_V1",
                                 policy_version=1)
        state["current_task"] = task
        self.cand.validate_final_verification_dispatch(state, task)  # aligned -> ok
        state["final_verification"]["policy_id"] = "SOMETHING_ELSE_FV_V1"
        with self.assertRaisesRegex(RuntimeError, "policy_id mismatch"):
            self.cand.validate_final_verification_dispatch(state, task)

    # G4-12: task gate policy binding missing/mismatched for a profile project
    def test_g4_12_gate_policy_binding_enforced(self):
        claims = [claim("A1", "CORE_RESULT"), claim("A2", "BASELINE_COMPARISON", "MEDIUM"),
                  claim("A3", "REPRODUCIBILITY")]
        task = self.gate_for(claims)  # no POLICY_ID
        state = self.gated_state("ACADEMIC_RESEARCH", claims, policy_id="ACADEMIC_FV_V1",
                                 policy_version=1)
        state["current_task"] = task
        with self.assertRaisesRegex(RuntimeError, "missing POLICY_ID"):
            self.cand.validate_final_verification_dispatch(state, task)
        task["FINAL_VERIFICATION_GATE"]["POLICY_ID"] = "SOFTWARE_ENGINEERING_FV_V1"
        with self.assertRaisesRegex(RuntimeError, "POLICY_ID mismatch"):
            self.cand.validate_final_verification_dispatch(state, task)

    # G4-13: wrong policy version -> fail (gate framework check + state policy binding)
    def test_g4_13_wrong_policy_version_fails(self):
        claims = [claim("A1", "CORE_RESULT"), claim("A2", "BASELINE_COMPARISON", "MEDIUM"),
                  claim("A3", "REPRODUCIBILITY")]
        task = self.gate_for(claims, policy_id="ACADEMIC_FV_V1", policy_version=2)
        state = self.gated_state("ACADEMIC_RESEARCH", claims, policy_id="ACADEMIC_FV_V1",
                                 policy_version=1)
        state["current_task"] = task
        # gate POLICY_VERSION=2 trips the framework version check (V1.5 wording, space)
        with self.assertRaisesRegex(RuntimeError, "policy version mismatch"):
            self.cand.validate_final_verification_dispatch(state, task)
        task["FINAL_VERIFICATION_GATE"]["POLICY_VERSION"] = 1  # framework ok now
        # state records policy_version 1 == resolved 1 -> ok; then record a WRONG version
        state["final_verification"]["policy_version"] = 2
        with self.assertRaisesRegex(RuntimeError, "policy_version mismatch"):
            self.cand.validate_final_verification_dispatch(state, task)

    # G4-15: cross-profile rule isolation (no cross-acceptance)
    def test_g4_15_cross_profile_rule_isolation(self):
        swe = self.cand.load_policy_document(
            self.root / "profiles" / "SOFTWARE_ENGINEERING", "SOFTWARE_ENGINEERING_FV_V1")
        academic = self.cand.load_policy_document(
            self.root / "profiles" / "ACADEMIC_RESEARCH", "ACADEMIC_FV_V1")
        business = self.cand.load_policy_document(
            self.root / "profiles" / "BUSINESS_RESEARCH", "BUSINESS_RESEARCH_FV_V1")
        general = self.cand.load_policy_document(
            self.root / "profiles" / "GENERAL", "GENERAL_FV_V1")
        # an ACADEMIC CORE_RESULT receipt must not pass under SWE rules
        claims = [claim("A1", "CORE_RESULT"), claim("A2", "CORE_RESULT", "MEDIUM"),
                  claim("A3", "CORE_RESULT")]
        task = self.gate_for(claims, policy_id="ACADEMIC_FV_V1")
        brief = {"FINAL_VERIFICATION": {"POLICY_VERSION": 1,
                 "CLAIMS_HASH": task["FINAL_VERIFICATION_GATE"]["CLAIMS_HASH"],
                 "OVERALL_STATUS": "PASS",
                 "CLAIM_RESULTS": [self.result_row("A1", "SUPPORTED",
                                     {"reproduction_check": "PASS", "result_artifact": "r.json"}),
                                   self.result_row("A2", "PARTIALLY_SUPPORTED",
                                     {"reproduction_check": "PASS", "result_artifact": "r2.json"}),
                                   self.result_row("A3", "SUPPORTED",
                                     {"reproduction_check": "PASS", "result_artifact": "r3.json"})]}}
        self.assertTrue(self.cand.evaluate_final_verification_receipt(
            task, brief, academic)["mechanical_pass"])
        # FAIL_CLOSED policies reject foreign taxonomy outright; BUSINESS declares
        # unknown_claim_type=ENVELOPE_ONLY (its V1.5-compatible mode), so foreign types
        # get the generic envelope there — the DECLARED mode is what is asserted.
        for foreign, name in ((swe, "SWE"), (general, "GENERAL")):
            res = self.cand.evaluate_final_verification_receipt(task, brief, foreign)
            self.assertFalse(res["mechanical_pass"],
                             f"{name} policy must not accept ACADEMIC claims")
        res_business = self.cand.evaluate_final_verification_receipt(task, brief, business)
        self.assertTrue(res_business["mechanical_pass"])  # ENVELOPE_ONLY, by declaration
        # ...and a BUSINESS-taxonomy receipt is rejected by every other profile's
        # FAIL_CLOSED policy (their taxonomies do not contain BUSINESS types):
        business_claims = [claim("B1", "IP"), claim("B2", "ECONOMICS", "MEDIUM"),
                           claim("B3", "COMPETITION")]
        business_task = self.gate_for(business_claims, policy_id="BUSINESS_RESEARCH_FV_V1")
        business_brief = {"FINAL_VERIFICATION": {"POLICY_VERSION": 1,
                          "CLAIMS_HASH": business_task["FINAL_VERIFICATION_GATE"]["CLAIMS_HASH"],
                          "OVERALL_STATUS": "PASS",
                          "CLAIM_RESULTS": [
                              self.result_row("B1", "SUPPORTED",
                                              {"authoritative_identifier_check": "PASS",
                                               "authoritative_source_pointers": ["USPTO"]}),
                              self.result_row("B2", "SUPPORTED",
                                              {"mechanical_recalculation": "PASS",
                                               "calculation_artifact": "w/r.json"}),
                              self.result_row("B3", "SUPPORTED", {})]}}
        for foreign, name in ((academic, "ACADEMIC"), (swe, "SWE"), (general, "GENERAL")):
            res = self.cand.evaluate_final_verification_receipt(
                business_task, business_brief, foreign)
            self.assertFalse(res["mechanical_pass"],
                             f"{name} policy must not accept BUSINESS taxonomy claims")
        # data-level isolation: no policy embeds another profile's taxonomy
        texts = {p: (Path(self.root / "profiles" / p / "FINAL_VERIFICATION_POLICY.json")
                     .read_text(encoding="utf-8")) for p in
                 ("GENERAL", "ACADEMIC_RESEARCH", "SOFTWARE_ENGINEERING", "BUSINESS_RESEARCH")}
        for p, text in texts.items():
            for foreign_type in ("BUG_REPRODUCTION", "CORE_RESULT", "CUSTOMER_PAIN"):
                if (p == "SOFTWARE_ENGINEERING" and foreign_type == "BUG_REPRODUCTION") or \
                   (p == "ACADEMIC_RESEARCH" and foreign_type == "CORE_RESULT") or \
                   (p == "BUSINESS_RESEARCH" and foreign_type == "CUSTOMER_PAIN"):
                    continue
                self.assertNotIn(f'"{foreign_type}"', text, f"{p} leaks {foreign_type}")

    # G4-16: newer Executor receipt invalidates a GENERIC policy PASS
    def test_g4_16_newer_receipt_invalidates_generic_pass(self):
        claims = [claim("A1", "CORE_RESULT"), claim("A2", "BASELINE_COMPARISON", "MEDIUM"),
                  claim("A3", "REPRODUCIBILITY")]
        self.cand.atomic_json(self.cand.PROJECT_STATE, {
            "schema_version": 3, "status": "COMPLETE", "phase": "ACADEMIC_TEST",
            "profile": "ACADEMIC_RESEARCH", "started_at": "2030-06-01T00:00:00+00:00",
            "deadline_at": "2099-01-01T00:00:00+00:00",
            "final_verification": {"policy_version": 1, "required": True, "status": "PASS",
                                   "policy_id": "ACADEMIC_FV_V1", "policy_version": 1,
                                   "critical_claims": claims,
                                   "claims_hash": self.cand.canonical_claims_hash(claims),
                                   "verification_message_id": 800001,
                                   "verification_receipt_sha256": "abc"}})
        runtime = {"last_final_verification_message_id": 800001,
                   "last_final_verification_receipt_sha256": "abc",
                   "last_final_verification_claims_hash": self.cand.canonical_claims_hash(claims),
                   "last_final_verification_overall_status": "PASS",
                   "last_final_verification_mechanical_pass": True,
                   "last_consumed_message_id": 800002,  # newer work
                   "last_consumed_brief_sha256": "newer"}
        state = self.cand.read_project_state()
        allowed, reason = self.cand.final_verification_terminal_check(runtime, state)
        self.assertFalse(allowed)
        self.assertIn("newer Executor receipt", reason)

    # G4-17: isolated Project A/B policy isolation via ACTIVE_PROJECT
    def test_g4_17_isolated_projects_policy_isolation(self):
        claims_a = [claim("A1", "CORE_RESULT"), claim("A2", "CORE_RESULT", "MEDIUM"),
                    claim("A3", "CORE_RESULT")]
        pa = self.root / "projects" / "proj-a"
        pa.mkdir(parents=True)
        self.cand.atomic_json(pa / "project_state.json", {
            "schema_version": 3, "project": "proj-a", "status": "WAITING_EXECUTOR",
            "phase": "ACADEMIC_TEST", "profile": "ACADEMIC_RESEARCH",
            "commercial_authorized": False,
            "started_at": "2030-06-01T00:00:00+00:00",
            "deadline_at": "2099-01-01T00:00:00+00:00",
            "final_verification": {"policy_version": 1, "required": True, "status": "PENDING",
                                   "policy_id": "ACADEMIC_FV_V1", "policy_version": 1,
                                   "critical_claims": claims_a,
                                   "claims_hash": self.cand.canonical_claims_hash(claims_a)}})
        claims_b = [claim("B1", "IP"), claim("B2", "ECONOMICS"), claim("B3", "OTHER", "MEDIUM")]
        pb = self.root / "projects" / "proj-b"
        pb.mkdir(parents=True)
        self.cand.atomic_json(pb / "project_state.json", {
            "schema_version": 3, "project": "proj-b", "status": "WAITING_EXECUTOR",
            "phase": "BUSINESS_TEST", "profile": "BUSINESS_RESEARCH",
            "commercial_authorized": False,
            "started_at": "2030-06-01T00:00:00+00:00",
            "deadline_at": "2099-01-01T00:00:00+00:00",
            "final_verification": {"policy_version": 1, "required": True, "status": "PENDING",
                                   "policy_id": "BUSINESS_RESEARCH_FV_V1", "policy_version": 1,
                                   "critical_claims": claims_b,
                                   "claims_hash": self.cand.canonical_claims_hash(claims_b)}})
        self.cand.atomic_json(self.cand.ACTIVE_PROJECT_FILE, {
            "schema_version": 1, "project_id": "proj-a", "project_root": "projects/proj-a"})
        self.cand.activate_project_scope()
        policy_a, route_a = self.cand._resolve_fv_policy_for_state(self.cand.read_project_state())
        self.assertEqual((route_a, policy_a["policy_id"]), ("profile", "ACADEMIC_FV_V1"))
        self.cand.atomic_json(self.cand.ACTIVE_PROJECT_FILE, {
            "schema_version": 1, "project_id": "proj-b", "project_root": "projects/proj-b"})
        self.cand.activate_project_scope()
        policy_b, route_b = self.cand._resolve_fv_policy_for_state(self.cand.read_project_state())
        self.assertEqual((route_b, policy_b["policy_id"]), ("profile", "BUSINESS_RESEARCH_FV_V1"))
        # namespace isolation: identical claim ids in project B evaluated under A's
        # FAIL_CLOSED policy are rejected (BUSINESS types unknown to ACADEMIC), and B
        # evaluated under its OWN policy with proper checks passes.
        res = self.cand.evaluate_final_verification_receipt(
            self.gate_for(claims_b, policy_id="BUSINESS_RESEARCH_FV_V1"),
            {"FINAL_VERIFICATION": {"POLICY_VERSION": 1,
             "CLAIMS_HASH": self.cand.canonical_claims_hash(claims_b),
             "OVERALL_STATUS": "PASS",
             "CLAIM_RESULTS": [self.result_row(c["claim_id"], "SUPPORTED") for c in claims_b]}},
            policy_a)
        self.assertFalse(res["mechanical_pass"])
        good = self.cand.evaluate_final_verification_receipt(
            self.gate_for(claims_b, policy_id="BUSINESS_RESEARCH_FV_V1"),
            {"FINAL_VERIFICATION": {"POLICY_VERSION": 1,
             "CLAIMS_HASH": self.cand.canonical_claims_hash(claims_b),
             "OVERALL_STATUS": "PASS",
             "CLAIM_RESULTS": [
                 self.result_row("B1", "SUPPORTED", {"authoritative_identifier_check": "PASS",
                                                     "authoritative_source_pointers": ["USPTO"]}),
                 self.result_row("B2", "SUPPORTED", {"mechanical_recalculation": "PASS",
                                                     "calculation_artifact": "w/r.json"}),
                 self.result_row("B3", "PARTIALLY_SUPPORTED", {})]}},
            policy_b)
        self.assertTrue(good["mechanical_pass"])

    # G4-18: legacy commercial V1.5 compatibility (2-arg evaluator, no profile)
    def test_g4_18_legacy_commercial_compatibility(self):
        claims = [claim("C1", "IP"), claim("C2", "ECONOMICS"),
                  claim("C3", "CUSTOMER_PAIN", "MEDIUM")]
        task = self.gate_for(claims)
        brief = {"FINAL_VERIFICATION": {"POLICY_VERSION": 1,
                 "CLAIMS_HASH": task["FINAL_VERIFICATION_GATE"]["CLAIMS_HASH"],
                 "OVERALL_STATUS": "PASS",
                 "CLAIM_RESULTS": [
                     self.result_row("C1", "SUPPORTED", {"authoritative_identifier_check": "PASS",
                                                         "authoritative_source_pointers": ["USPTO"]}),
                     self.result_row("C2", "SUPPORTED", {"mechanical_recalculation": "PASS",
                                                         "calculation_artifact": "w/r.json"}),
                     self.result_row("C3", "PARTIALLY_SUPPORTED",
                                     {"source_independence_check": "PASS",
                                      "independent_underlying_sources": 2})]}}
        state = {"status": "WAITING_EXECUTOR",
                 "phase": "CROSS_BORDER_ECOMMERCE_AGENT_V2", "commercial_authorized": True,
                 "started_at": "2030-06-01T00:00:00+00:00", "current_task": None}
        state["final_verification"] = {
            "policy_version": 1, "required": True, "status": "PENDING",
            "critical_claims": claims,
            "claims_hash": self.cand.canonical_claims_hash(claims)}
        state["current_task"] = task
        self.cand.validate_final_verification_dispatch(state, task)  # legacy: no POLICY_ID needed
        policy, route = self.cand._resolve_fv_policy_for_state(state)
        self.assertEqual(route, "legacy_v1_5")
        res = self.cand.evaluate_final_verification_receipt(task, brief, policy)
        self.assertTrue(res["mechanical_pass"], res["issues"])

    # G4-19: legacy grandfather semantics preserved
    def test_g4_19_legacy_grandfather(self):
        state = {"status": "COMPLETE", "phase": "CROSS_BORDER_ECOMMERCE_AGENT_V2",
                 "commercial_authorized": True, "started_at": "2029-01-01T00:00:00+00:00"}
        runtime = {"final_verification_enforce_after": "2030-01-01T00:00:00+00:00"}
        allowed, reason = self.cand.final_verification_terminal_check(runtime, state)
        self.assertTrue(allowed)
        self.assertIn("not gated", reason)
        # a future legacy commercial run stays gated
        state["started_at"] = "2031-01-01T00:00:00+00:00"
        allowed, _ = self.cand.final_verification_terminal_check(runtime, state)
        self.assertFalse(allowed)

    # G4-20: M1-M8 markers + recovery functions intact
    def test_g4_20_markers_and_recovery(self):
        src = (CANDIDATE / "orchestrator.py").read_text(encoding="utf-8")
        for marker in ("FIX-F17", "FIX-F01", "FIX-F03", "FIX-F04", "FIX-F05",
                       "FIX-F06", "FIX-F18", "consecutive_noop_codex_turns",
                       "replay_consumed_receipt_event", "_lock_owner_dead",
                       "dispatch_registered_at", 'SUPERVISOR_MODEL = "gpt-5.6-sol"',
                       'SUPERVISOR_REASONING_EFFORT = "high"',
                       "def activate_project_scope", "def load_active_project",
                       "def consume_executor_receipt"):
            self.assertIn(marker, src)
        self.assertIn("FIX-F16", (CANDIDATE / "scripts" / "executor_claim.py").read_text(encoding="utf-8"))
        # canonical hash algorithm unchanged: known vector must match production value
        claims = [{"claim_id": "C1", "claim": "x", "claim_type": "OTHER",
                   "decision_impact": "HIGH", "evidence_pointers": [], "verification_standard": "s"}]
        import hashlib
        expected = hashlib.sha256(json.dumps(claims, ensure_ascii=False, sort_keys=True,
                                             separators=(",", ":")).encode("utf-8")).hexdigest()
        self.assertEqual(self.cand.canonical_claims_hash(claims), expected)


if __name__ == "__main__":
    unittest.main()
