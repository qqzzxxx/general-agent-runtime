"""Phase 6 contract comparison and native-browser UI demonstration.

All Runtime state is private and synthetic. Native Node/Chromium is used directly;
no ZCode automation, model invocation, production project or deployment is touched.
"""
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import time

import executor_capabilities as cap
import executor_completion as completion
import executor_work as work
import test_executor_contract as fixtures
from test_host_native_executor import digest_tree
from measure_capability_realization import DRAFT, REVISED

ROOT = Path(__file__).resolve().parents[1]
EVIDENCE = ROOT / "evidence/v1.4-host-native-executor"
DISCLOSURE = "Local demo only. No account or data is sent."
HANDLER = '''<p role="status" aria-live="polite"></p><script>
window.demoReady = true;
document.querySelector('form').addEventListener('submit', event => {
  event.preventDefault();
  document.querySelector('[role=status]').innerText = 'Demo ready for ' + document.querySelector('input').value;
});</script>'''


def source(html):
    return html.replace("Static design prototype. Account creation is not connected.", DISCLOSURE).replace("</form>", "</form>" + HANDLER)


def load_snapshot(name, filename):
    spec = importlib.util.spec_from_file_location(name, EVIDENCE / filename)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def text(path):
    return path.read_text(encoding="utf-8-sig").replace("\r\n", "\n")


def compact(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def run(output):
    deps = cap.browser_dependencies()
    if not all(deps.values()):
        raise RuntimeError("Native demonstration requires installed Node and Chromium")
    fixture = fixtures.ExecutorContractTests()
    fixture.setUp()
    try:
        task = {**fixtures.task_fields(), "OBJECTIVE": "Build and inspect a responsive local sign-up demo.",
            "ACCEPTANCE_CRITERIA": ["No horizontal overflow at 375x812 and 1280x900.",
                "Email has an associated label; submit says Create account; controls are at least 44 CSS pixels high.",
                "Native browser input and click run the page JavaScript and display the demo confirmation.",
                "Disclose that no real account or data is sent."],
            "FORBIDDEN_ACTIONS": ["Do not send data or create a real account.", "Do not mutate Runtime control or history."],
            "EXECUTION": {"autonomy": "HIGH"}}
        fixture.start(task)
        def normalized(value):
            raw = compact(value).replace(fixture.session, "<SESSION>")
            for actual, placeholder in ((fixture.work, "<ATTEMPT_WORK>"), (fixture.project, "<PROJECT_INPUTS>"), (fixture.root, "<RUNTIME_ROOT>")):
                raw = raw.replace(json.dumps(str(actual))[1:-1], placeholder)
            return json.loads(raw)
        old_cap = load_snapshot("phase5_cap", "phase5-executor-capabilities.py")
        old_contract = load_snapshot("phase5_contract", "phase5-executor-contract.py")
        old_contract.capabilities_layer = old_cap
        old_contract.CAPABILITY_MODES = old_cap.MODES
        before = {"schema_version": 2, "status": "READY", "action": "EXECUTE",
                  "session": "<SESSION>", "contract": old_contract.project(task)}
        after = normalized(fixture.ready)
        prompts = {"before": text(EVIDENCE / "phase5-executor-prompt.md"),
                   "after": text(ROOT / "control/ZCODE_SCHEDULED_AUTOMATION_PROMPT.md")}
        sizes = {}
        for name, ready in (("before", before), ("after", after)):
            bound = prompts[name].replace("<RUNTIME_ROOT>", "<AUDIT_RUNTIME_ROOT>")
            sizes[name] = {"permanent_template_characters": len(prompts[name]),
                "bound_prompt_characters": len(bound), "contract_characters": len(compact(ready["contract"])),
                "entry_characters": len(compact(ready)), "bound_prompt_plus_entry_characters": len(bound) + len(compact(ready))}
        before_control, before_history = digest_tree(fixture.root / "control"), digest_tree(fixture.root / "handoff")
        calls, observations = [], []
        for revision, html in (("v1", source(DRAFT)), ("v2", source(REVISED))):
            assert fixture.do("checkpoint")["status"] == "OK"
            # Independent host process writes candidate source. No work.write call.
            writer = subprocess.run([sys.executable, "-c",
                "import sys; from pathlib import Path; Path('workspace/signup.html').write_text(sys.stdin.read(), encoding='utf-8')"],
                input=html, cwd=fixture.work, capture_output=True, text=True, timeout=10)
            if writer.returncode:
                raise RuntimeError(writer.stderr)
            started = time.perf_counter()
            process = subprocess.run([deps["node"], str(ROOT / "scripts/host_native_ui_probe.mjs"), deps["browser"],
                                      "workspace/signup.html", revision], cwd=fixture.work, capture_output=True, text=True, timeout=45)
            if process.returncode:
                raise RuntimeError(process.stderr)
            observed = json.loads(process.stdout)
            calls.append({"native_operation": "Node/Chromium script, local file, CDP input/click and screenshots",
                          "revision": revision, "wall_seconds": round(time.perf_counter() - started, 3)})
            observations.append(observed)
        checks = ("no_horizontal_overflow", "associated_label", "descriptive_action", "controls_at_least_44px",
                  "local_submit_handler", "page_javascript_executed")
        assert any(not r["observation"]["no_horizontal_overflow"] for r in observations[0]["rounds"])
        assert all(r["observation"][key] for r in observations[-1]["rounds"] for key in checks), observations[-1]
        assert digest_tree(fixture.root / "control") == before_control
        assert digest_tree(fixture.root / "handoff") == before_history
        assert not (fixture.project / "workspace/signup.html").exists()
        assert not completion.lookup_entries(fixture.root, fixture.identity["MESSAGE_ID"])
        evidence_path = "evidence/native-browser-observations.json"
        (fixture.work / evidence_path).write_text(json.dumps(observations, indent=2) + "\n", encoding="utf-8")
        artifacts = [{"path": "workspace/signup.html", "role": "deliverable"}, {"path": evidence_path, "role": "evidence"}]
        artifacts += [{"path": r["screenshot"], "role": "evidence"} for obs in observations for r in obs["rounds"]]
        result = fixtures.semantic("COMPLETED", artifacts)
        result.update(findings=["Six browser checks pass at both viewports after scripted revision.",
                                "The native browser executed page JavaScript and input/click interaction."],
                      evidence=[a["path"] for a in artifacts],
                      limitations=["Scripted candidate selection and checks; no live ZCode or model-quality comparison.",
                          "Screenshots were captured; this scripted Executor makes no visual judgment.",
                          "Local demo only; no backend, account creation or live remote service tested."],
                      completion={"Acceptance self-check": ["All fixed local demo criteria met by source disclosure and recorded browser checks."]})
        done = fixture.do("finish", result=result)
        assert done["status"] == "FINISHED", done
        records = completion.lookup_entries(fixture.root, fixture.identity["MESSAGE_ID"])
        assert len(records) == 1 and completion.entry_hashes_intact(records[0])
        assert digest_tree(fixture.root / "control") == before_control
        assert work.perform(fixture.root, session=fixture.session, request={"op": "checkpoint"})["action"] == "STOP"
        assert completion.read_runtime_state(fixture.root)["authorized_dispatch"]["MESSAGE_ID"] == fixture.identity["MESSAGE_ID"]
        output.mkdir(parents=True, exist_ok=True)
        for a in artifacts:
            path = output / "ui" / a["path"]
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes((fixture.project / a["path"]).read_bytes())
        (output / "ui/workspace/signup-before.html").write_text(source(DRAFT), encoding="utf-8")
        for label, value in (("before-entry.json", before), ("after-entry.json", after),
                             ("semantic-result.json", result), ("completion-record.json", normalized(records[0]))):
            (output / label).write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        sources = {str(p.relative_to(ROOT)).replace("\\", "/"): {"sha256": hashlib.sha256(p.read_bytes()).hexdigest(),
                    "bytes": p.stat().st_size} for p in
                   [EVIDENCE / "phase5-executor-prompt.md", EVIDENCE / "phase5-executor-contract.py",
                    EVIDENCE / "phase5-executor-capabilities.py", ROOT / "control/ZCODE_SCHEDULED_AUTOMATION_PROMPT.md",
                    ROOT / "scripts/executor_entry.py", ROOT / "scripts/executor_contract.py", ROOT / "scripts/executor_capabilities.py",
                    ROOT / "scripts/host_native_ui_probe.mjs"]}
        return {"schema_version": 1, "comparison": sizes, "source_snapshots": sources,
            "before_method": "Saved Phase 5 projection/capability sources in the unchanged V2 envelope; same task.",
            "after_method": "Actual Phase 6 READY entry, with only session and absolute locations normalized.",
            "native_calls": calls, "browser_observations": observations,
            "authority_checks": {"control_unchanged_by_native_work_and_finish": True, "history_and_ledger_unchanged_by_native_work": True,
                "canonical_output_absent_before_finish": True, "no_completion_before_finish": True,
                "one_runtime_completion": True, "publication_provenance_intact": True,
                "no_next_task_authorized": True, "post_finish_checkpoint_stops": True},
            "model_quality_improvement": None, "live_zcode_model_run": False, "provider_tokens": None,
            "limitations": result["limitations"] + ["Host tool schemas/system prompts/history, provider latency and OS containment are unmeasured."]}
    finally:
        fixture.doCleanups()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=EVIDENCE)
    args = parser.parse_args()
    report = run(args.output)
    (args.output / "measurement.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"comparison": report["comparison"], "authority_checks": report["authority_checks"]}, indent=2))
