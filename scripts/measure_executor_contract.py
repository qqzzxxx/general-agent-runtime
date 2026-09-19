"""Phase 4 reproducible surface census and scripted UI iteration, never a model A/B."""
from __future__ import annotations

from html.parser import HTMLParser
import json
from pathlib import Path

import audit_intelligence_overhead as audit
import executor_completion as completion
import executor_contract as contract
import executor_entry as entry
import executor_fence as fence
import executor_finish as finish
import executor_work as work
from measure_executor_entry import comparison
from test_executor_contract import ExecutorContractTests, semantic, task_fields

ROOT = Path(__file__).resolve().parents[1]
EVIDENCE = ROOT / "evidence/v1.4-executor-contract-v2"
DRAFT = '<form><input id="email" type="email"><button>Go</button></form>'
REVISED = '<form><label for="email">Email address</label><input id="email" name="email" type="email" autocomplete="email" required><button type="submit">Create account</button></form>'


class FormChecks(HTMLParser):
    def __init__(self):
        super().__init__()
        self.inputs, self.labels, self.buttons = [], [], []
        self.in_button = False

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == "input":
            self.inputs.append(attrs.get("id"))
        elif tag == "label":
            self.labels.append(attrs.get("for"))
        elif tag == "button":
            self.in_button = True
            self.buttons.append("")

    def handle_data(self, text):
        if self.in_button:
            self.buttons[-1] += text

    def handle_endtag(self, tag):
        if tag == "button":
            self.in_button = False

    @classmethod
    def inspect(cls, text):
        parser = cls()
        parser.feed(text)
        return {"associated_labels": bool(parser.inputs) and all(
                    name is not None and name in parser.labels for name in parser.inputs),
                "descriptive_submit": "Create account" in parser.buttons}


def ui_run(version, iterate):
    """Scripted client supplies edits; independent parser evaluates both equally."""
    fixture = ExecutorContractTests()
    fixture.setUp()
    try:
        identity = fixture.dispatch()
        ready = entry.enter(fixture.root, contract_version=version)
        if ready["status"] != "READY":
            raise AssertionError(ready)
        session = ready["session"] if version == 2 else ready["attempt"]["claim_token"]
        candidate_root = fence.attempt_root(fixture.project, identity)
        candidate = candidate_root / "workspace/signup.html"
        trace, explicit_checks = [], 0
        for index, source in enumerate([DRAFT, REVISED] if iterate else [DRAFT], 1):
            if version == 1:
                # Follow V1's actual per-work-batch checkpoint contract.
                fence.check(fixture.root, identity, claim_token=session)
                explicit_checks += 1
                candidate.write_text(source, encoding="utf-8")
                observed = candidate.read_text(encoding="utf-8")
            else:
                reply = work.perform(fixture.root, session=session, request={
                    "op": "write", "path": "workspace/signup.html", "text": source})
                if reply["status"] != "OK":
                    raise AssertionError(reply)
                reply = work.perform(fixture.root, session=session, request={
                    "op": "read", "area": "work", "path": "workspace/signup.html"})
                if reply["status"] != "OK":
                    raise AssertionError(reply)
                observed = reply["text"]
            checks = FormChecks.inspect(observed)
            trace.append({"revision": index, "inspection": checks,
                          "decision": "submit" if all(checks.values()) or not iterate else "revise"})
        passed = all(trace[-1]["inspection"].values())
        result = semantic("COMPLETED" if passed else "PARTIAL",
                          [{"path": "workspace/signup.html", "role": "deliverable"}])
        result["findings"] = [f"Source checks: {sum(trace[-1]['inspection'].values())}/2 passed."]
        result["evidence"] = ["workspace/signup.html: associated label and descriptive submit source inspection"]
        result["limitations"] = ["Scripted source checks only; rendered appearance and interaction behavior not tested."]
        result["completion"] = {"Acceptance self-check": trace[-1]["inspection"]}
        if version == 1:
            fence.check(fixture.root, identity, claim_token=session)
            explicit_checks += 1
            (candidate_root / "finish.json").write_text(json.dumps(result), encoding="utf-8")
            done = finish.finish(fixture.root, claim_token=session, result_path="finish.json")
        else:
            done = work.perform(fixture.root, session=session, request={"op": "finish", "result": result})
        if done["status"] != "FINISHED":
            raise AssertionError(done)
        record = completion.lookup_entries(fixture.root, identity["MESSAGE_ID"])[0]
        output = (fixture.project / "workspace/signup.html").read_text(encoding="utf-8")
        return {"contract_version": version, "scripted_iteration": iterate, "trace": trace,
                "explicit_client_fence_calls": explicit_checks,
                "client_identity_argument_values_for_checks": 5 * explicit_checks,
                "published_source": output, "final_checks": FormChecks.inspect(output),
                "outcome": record["RECEIPT"]["STATUS"],
                "semantic_result": result,
                "provenance_intact": completion.entry_hashes_intact(record)}
    finally:
        fixture.doCleanups()


def report():
    before = audit.read(EVIDENCE / "v1-executor-prompt.md")
    saved_phase3 = json.loads(audit.read(ROOT / "evidence/v1.4-runtime-owned-completion/context-comparison.json"))
    if audit.measure(before) != saved_phase3["sources_after"]["permanent_prompt"]:
        raise ValueError("V1 snapshot differs from the completed Phase 3 baseline")
    after = audit.read(ROOT / "control/ZCODE_SCHEDULED_AUTOMATION_PROMPT.md")
    # Same semantic task and working-style request, no artificial V1 padding.
    fixture = ExecutorContractTests()
    fixture.setUp()
    try:
        fixture.dispatch()
        import supervisor_control as sc
        task = sc._parse_dispatch_bytes(fixture.o.TO_ZCODE.read_bytes())
        v1 = entry.enter(fixture.root)
        v2 = entry.enter(fixture.root, resume_token=v1["attempt"]["claim_token"], contract_version=2)
        if v1["status"] != "READY" or v2["status"] != "READY":
            raise AssertionError((v1, v2))
        # Replace random token/path/time values only; measure both complete views.
        v1["attempt"]["claim_token"] = v2["session"] = "<SESSION>"
        v1["attempt"]["expires_at"] = "<EXPIRES_AT>"
        for name in v1["paths"]:
            v1["paths"][name] = "<" + name.upper() + ">"
    finally:
        fixture.doCleanups()
    before_task, after_task = entry.task_view(task), contract.project(task)
    runs = {"v1_stage_only_control": ui_run(1, False),
            "v1_iterative_control": ui_run(1, True), "v2_high_iterative": ui_run(2, True)}
    return {"schema": "EXECUTOR-CONTRACT-V2-OFFLINE-V1",
            "baseline": "Completed Phase 3 Executor contract (called V1 for this comparison)",
            "measurement": "BOM-stripped, LF-normalized Unicode characters; compact JSON; no token conversion",
            "permanent_template_characters": comparison(len(before), len(after)),
            "bound_permanent_prompt_characters": comparison(
                len(before.replace("<RUNTIME_ROOT>", audit.ROOT_LABEL)),
                len(after.replace("<RUNTIME_ROOT>", audit.ROOT_LABEL))),
            "task_view_characters": comparison(len(audit.compact(before_task)), len(audit.compact(after_task))),
            "complete_entry_response_characters": comparison(len(audit.compact(v1)), len(audit.compact(v2))),
            "bound_prompt_plus_entry_characters": comparison(
                len(before.replace("<RUNTIME_ROOT>", audit.ROOT_LABEL)) + len(audit.compact(v1)),
                len(after.replace("<RUNTIME_ROOT>", audit.ROOT_LABEL)) + len(audit.compact(v2))),
            "sources": {"v1_prompt": audit.measure(before), "v2_prompt": audit.measure(after),
                        "v1_entry_snapshot": audit.measure(audit.read(EVIDENCE / "v1-executor-entry.py"))},
            "fixtures": {"v1_task_view": before_task, "v2_contract": after_task,
                         "v1_entry_response": v1, "v2_entry_response": v2},
            "scripted_ui_runs": runs,
            "limitations": [
                "No live model, provider, installed ZCode prompt, tool transcript, latency or token measurements.",
                "Edits and iteration choices are scripted. V1 can iterate too; its iterative control reaches identical source checks and semantic outcome.",
                "The stage-only control demonstrates that file production does not satisfy these criteria; it does not represent observed V1 model behavior.",
                "Two structural HTML checks are not a visual, accessibility or interaction audit. Browser and GUI are not granted in this fixture.",
                "Full V2 entry is larger for this fixture: explicit autonomy/capabilities cost context; the permanent contract removes repeated mechanics.",
                "Filesystem-only fixture: no shell, network, browser or GUI grant; same-user bypass is outside the guarantee.",
                "Runtime revalidates each operation internally; fewer client checkpoints do not imply fewer checks, helper processes or less Runtime IO."],
            "model_quality_improvement": None, "provider_token_savings": None, "latency_savings_seconds": None}


if __name__ == "__main__":
    print(json.dumps(report(), ensure_ascii=False, indent=2))
