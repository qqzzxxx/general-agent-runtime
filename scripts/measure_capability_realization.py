"""Phase 5 reproducible capability surfaces and scripted, real-browser UI iteration.

Private temporary Runtime only. --output retains evidence; --network-smoke explicitly
adds one public HTTPS GET. No model, live automation or production project is invoked.
"""
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
from pathlib import Path
import time

import audit_intelligence_overhead as audit
import executor_capabilities as cap
import executor_completion as completion
import executor_contract as contract
import executor_entry as entry
import executor_inspect as inspect
import ordinary_dispatch as od
import supervisor_control as sc
import test_executor_contract as fixtures
from measure_executor_contract import FormChecks

ROOT = Path(__file__).resolve().parents[1]
EVIDENCE = ROOT / "evidence/v1.4-capability-realization"

DRAFT = '''<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Join Fieldnotes</title><style>
*{box-sizing:border-box}body{margin:0;background:#f5f3ee;color:#172e29;font:16px/1.5 Arial,sans-serif}
header{display:flex;justify-content:space-between;align-items:center;padding:24px 6%;border-bottom:1px solid #d9dfd5}
.brand{font-size:20px;font-weight:700;letter-spacing:-.7px}.edition{font-size:12px;letter-spacing:1.5px;color:#52675d}
main{display:grid;grid-template-columns:1fr 1fr;gap:70px;max-width:1120px;margin:80px auto;padding:0 36px}
.eyebrow{font-size:12px;letter-spacing:2px;color:#577462;font-weight:bold}h1{font-size:56px;line-height:1.08;letter-spacing:-2px;margin:18px 0 24px}
.intro{max-width:370px;color:#52645c;font-size:18px}.benefit{border-top:1px solid #ccd5ca;margin-top:42px;padding-top:22px;max-width:370px}
.benefit b{font-size:15px}.benefit p{font-size:14px;color:#52645c}.card{width:620px;background:white;border:1px solid #d9dfd5;border-radius:18px;padding:36px;box-shadow:0 12px 32px #233e2910}
.step{font-size:11px;letter-spacing:1.5px;color:#62716a}h2{font-size:27px;letter-spacing:-.8px;margin:16px 0 8px}.sub{color:#637068;font-size:14px;margin:0 0 30px}
label{display:block;font-size:14px;font-weight:600;margin-bottom:8px}input{width:100%;height:32px;border:1px solid #aebdb2;border-radius:8px;padding:0 12px;font:inherit;color:#173d30}
.hint{font-size:12px;color:#62716a;margin:8px 0 24px}button{height:32px;width:100%;border:0;border-radius:8px;background:#264f3c;color:white;font:600 15px Arial,sans-serif}
.note{font-size:12px;color:#68746b;line-height:1.5;margin:18px 0 0}.footer{font-size:12px;color:#65736b;margin-top:28px;display:flex;gap:8px;align-items:center}.dot{height:6px;width:6px;border-radius:50%;background:#6a8c65}
</style></head><body><header><div class="brand">fieldnotes<span style="color:#7e9b6c">.</span></div><div class="edition">A LITTLE SPACE TO THINK</div></header>
<main><section><div class="eyebrow">YOUR NEXT CHAPTER</div><h1>Good ideas<br>need a home.</h1><p class="intro">A quiet workspace for the notes, plans, and small discoveries that move you forward.</p>
<div class="benefit"><b>Start small. Keep what matters.</b><p>Bring your thoughts together in one place.<br>No noisy feeds. No complicated setup.</p></div></section>
<section><div class="card"><div class="step">01 / CREATE YOUR WORKSPACE</div><h2>Make yourself at home.</h2><p class="sub">Your first notebook is one step away.</p>
<form><label for="email">Email address</label><input id="email" name="email" type="email" autocomplete="email" placeholder="you@example.com" required aria-describedby="email-help">
<p class="hint" id="email-help">Use an address you can access.</p><button type="submit">Create account</button></form>
<p class="note">Static design prototype. Account creation is not connected.</p></div><div class="footer"><span class="dot"></span>Designed for a calmer kind of progress.</div></section></main></body></html>'''

REVISED = DRAFT.replace('.card{width:620px;', '.card{width:100%;').replace('height:32px', 'height:48px').replace(
    '</style>', '@media(max-width:700px){header{padding:20px 24px}.edition{display:none}main{grid-template-columns:1fr;gap:30px;margin:36px auto;padding:0 24px}h1{font-size:42px}.intro{font-size:16px}.benefit{display:none}.card{padding:26px 22px}h2{font-size:23px}.footer{margin-top:20px}}\n</style>')


def load_snapshot(name, filename):
    spec = importlib.util.spec_from_file_location(name, EVIDENCE / filename)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def checks(observation):
    controls = observation["controls"]
    inputs = [c for c in controls if c["tag"] == "input"]
    buttons = [c for c in controls if c["tag"] == "button"]
    return {"no_horizontal_overflow": not observation["horizontal_overflow"],
            "visible_associated_labels": bool(inputs) and all(c["visible"] and c["labels"] for c in inputs),
            "visible_descriptive_submit": any(c["visible"] and c["text"] == "Create account" for c in buttons),
            "controls_at_least_44px_high": bool(inputs and buttons) and all(c["rect"]["height"] >= 44 for c in inputs + buttons),
            "prototype_disclosure": "Static design prototype. Account creation is not connected." in observation["text"]}


def task_fields(render):
    return {**fixtures.task_fields(), "OBJECTIVE": "Create a responsive Fieldnotes sign-up design prototype.",
            "ACCEPTANCE_CRITERIA": ["At 375x812 and 1280x900, there is no horizontal page overflow.",
                "The email control has a visible associated label and Create account is visible.",
                "Input and submit controls are at least 44 CSS pixels high.",
                "Clearly identify the form as an unconnected static prototype."],
            "EXECUTION": {"autonomy": "HIGH", "capabilities": {"browser": "render" if render else "none"}}}


def ui_run(render, output=None):
    fixture = fixtures.ExecutorContractTests()
    fixture.setUp()
    calls, rounds = [], []
    try:
        fixture.start(task_fields(render))
        ready = {**fixture.ready, "session": "<SESSION>"}
        def perform(op, **args):
            request = {"op": op, **args}
            start = time.perf_counter()
            reply = fixture.do(op, **args)
            calls.append({"operation": op, "request_characters": len(audit.compact(request)),
                          "response_characters": len(audit.compact(reply)),
                          "wall_seconds": round(time.perf_counter() - start, 4)})
            if reply["status"] not in {"OK", "FINISHED"}:
                raise AssertionError(reply)
            return reply
        for revision, source in enumerate((DRAFT, REVISED) if render else (DRAFT,), 1):
            perform("write", path="workspace/signup.html", text=source)
            read = perform("read", area="work", path="workspace/signup.html")
            row = {"revision": revision, "source_checks": FormChecks.inspect(read["text"]), "renders": []}
            if render:
                for label, width, height in (("mobile", 375, 812), ("desktop", 1280, 900)):
                    observation = perform("render", area="work", path="workspace/signup.html", width=width, height=height,
                                          screenshot=f"evidence/{label}-v{revision}.png")["observation"]
                    row["renders"].append({"viewport_name": label, "checks": checks(observation), "observation": observation})
            row["decision"] = "submit" if render and all(all(r["checks"].values()) for r in row["renders"]) else "revise" if render else "report rendering gap"
            rounds.append(row)
            if row["decision"] == "submit":
                break
        passed = render and rounds[-1]["decision"] == "submit"
        result = fixtures.semantic("COMPLETED" if passed else "PARTIAL")
        result["artifacts"] = [{"path": "workspace/signup.html", "role": "deliverable"}]
        result["artifacts"] += [{"path": r["observation"]["screenshot"], "role": "evidence"} for row in rounds for r in row["renders"]]
        result["findings"] = ["All five fixed rendered checks pass at both viewports." if passed else "Source checks pass; rendered criteria unverified."]
        result["evidence"] = [a["path"] for a in result["artifacts"]]
        result["limitations"] = ["Scripted edits and judgments; no live model comparison.",
            "Static prototype: no submission, backend, external assets or page JavaScript verified.",
            "JSON transport provides geometry/text, not image pixels to the Executor; no Executor visual-perception claim."]
        result["completion"] = {"Acceptance self-check": [{"viewport": r["viewport_name"], "checks": r["checks"]}
                                                          for r in rounds[-1]["renders"]]}
        perform("finish", result=result)
        record = completion.lookup_entries(fixture.root, fixture.identity["MESSAGE_ID"])[0]
        intact = completion.entry_hashes_intact(record)
        if not intact:
            raise AssertionError("publication provenance failed")
        if output is not None:
            target = Path(output) / ("render-iteration" if render else "source-control")
            for artifact in result["artifacts"]:
                destination = target / artifact["path"]
                destination.parent.mkdir(parents=True, exist_ok=True)
                destination.write_bytes((fixture.project / artifact["path"]).read_bytes())
            (target / "semantic-result.json").write_text(json.dumps(result, indent=2), encoding="utf-8")
        return {"ready": ready, "rounds": rounds, "calls": calls, "outcome": record["RECEIPT"]["STATUS"],
                "provenance_intact": intact, "published_source_sha256": hashlib.sha256((fixture.project / "workspace/signup.html").read_bytes()).hexdigest(),
                "request_characters": sum(c["request_characters"] for c in calls),
                "response_characters": sum(c["response_characters"] for c in calls),
                "operation_wall_seconds": round(sum(c["wall_seconds"] for c in calls), 4)}
    finally:
        fixture.doCleanups()


def report(output=None, network_smoke=False):
    before_prompt = audit.read(EVIDENCE / "phase4-executor-prompt.md")
    before_contract = load_snapshot("phase4_capability_contract", "phase4-executor-contract.py")
    before_dispatch = load_snapshot("phase4_capability_dispatch", "phase4-ordinary-dispatch.py")
    recorded = json.loads(audit.read(ROOT / "evidence/v1.4-executor-contract-v2/context-comparison.json"))
    if audit.measure(before_prompt) != recorded["sources"]["v2_prompt"]:
        raise ValueError("Phase 4 prompt differs from captured baseline")
    after_prompt = audit.read(ROOT / "control/ZCODE_SCHEDULED_AUTOMATION_PROMPT.md")
    task = task_fields(False)
    phase4 = {"schema_version": 2, "status": "READY", "action": "EXECUTE", "session": "<SESSION>", "contract": before_contract.project(task)}
    phase5_default = {**phase4, "contract": contract.project(task)}
    phase5_both_task = task_fields(True)
    phase5_both_task["EXECUTION"]["capabilities"]["network"] = "https_get"
    phase5_both_task["EXECUTION"]["network_urls"] = ["https://example.com/"]
    phase5_both = {**phase4, "contract": contract.project(phase5_both_task)}
    surfaces = {}
    for name, prompt, ready in (("phase4_filesystem", before_prompt, phase4),
                                ("phase5_filesystem", after_prompt, phase5_default),
                                ("phase5_render_and_https", after_prompt, phase5_both)):
        bound = prompt.replace("<RUNTIME_ROOT>", audit.ROOT_LABEL)
        surfaces[name] = {"permanent_template": len(prompt), "bound_prompt": len(bound),
            "capabilities": len(audit.compact(ready["contract"]["capabilities"])),
            "contract": len(audit.compact(ready["contract"])), "entry": len(audit.compact(ready)),
            "bound_prompt_plus_entry": len(bound) + len(audit.compact(ready))}
    result = {"schema": "CAPABILITY-REALIZATION-PHASE5-V1", "host_inventory": cap.host_inventory(),
        "measurement": "BOM-stripped LF-normalized Unicode characters, compact JSON; local wall seconds; no token conversion",
        "surfaces": surfaces, "supervisor_dispatch_instruction_characters": {"phase4": len(before_dispatch.SUPERVISOR_CONTRACT), "phase5": len(od.SUPERVISOR_CONTRACT)},
        "sources": {"phase4_prompt": audit.measure(before_prompt), "phase5_prompt": audit.measure(after_prompt)},
        "projection_fixtures": {"phase4": phase4, "phase5_filesystem": phase5_default, "phase5_both": phase5_both},
        "source_control": ui_run(False, output), "render_iteration": ui_run(True, output),
        "live_network_smoke": None, "live_model_comparison": False, "model_quality_improvement": None,
        "provider_tokens": None, "provider_latency": None,
        "limitations": ["Edits and acceptance checks are scripted, not observed model decisions.",
            "Source control cannot certify rendering; no claim Phase 4 or another model could not improve source.",
            "Render/HTTPS cases add real capability and context; character counts do not measure reasoning cost.",
            "Host/system prompts, ZCode MCP schemas, browser plugin instructions, history and provider token accounting unavailable.",
            "Per-operation wall time includes gates, child startup and IO; single local run, not a model latency benchmark.",
            "PNG bytes are artifacts; not present in Executor JSON response or vision input."]}
    if network_smoke:
        fixture = fixtures.ExecutorContractTests()
        fixture.setUp()
        try:
            task = task_fields(False)
            task["EXECUTION"].update(capabilities={"network": "https_get"}, network_urls=["https://example.com/"])
            fixture.start(task)
            start = time.perf_counter()
            reply = fixture.do("fetch", url="https://example.com/")
            observed = reply.get("observation", {})
            result["live_network_smoke"] = {"url": "https://example.com/", "status": reply["status"],
                "wall_seconds": round(time.perf_counter()-start, 4), "body_bytes": len(observed.get("text", "").encode()),
                "sha256": observed.get("sha256"), "reason": reply.get("reason")}
        finally:
            fixture.doCleanups()
    return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--network-smoke", action="store_true")
    args = parser.parse_args()
    data = report(args.output, args.network_smoke)
    if args.output:
        args.output.mkdir(parents=True, exist_ok=True)
        (args.output / "measurement.json").write_text(json.dumps(data, indent=2), encoding="utf-8")
        print(json.dumps({"surfaces": data["surfaces"], "render_outcome": data["render_iteration"]["outcome"],
                          "render_wall_seconds": data["render_iteration"]["operation_wall_seconds"],
                          "live_network_smoke": data["live_network_smoke"]}, indent=2))
    else:
        print(json.dumps(data, indent=2))
