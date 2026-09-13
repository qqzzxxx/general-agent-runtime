"""GET-only verification of the existing, already-completed dogfood project."""
import json
from pathlib import Path
import subprocess
import sys
from datetime import datetime, timezone
from urllib.request import urlopen

OUT = Path(__file__).resolve().parent
ROOT = Path(r"C:\general-agent-runtime-v13-dogfood")
BASE = "http://127.0.0.1:60649/api/runtimes/51042462032cbc1f"
proc = subprocess.run([sys.executable, "-B", str(ROOT / "scripts/supervisor_control.py"),
                       "--root", str(ROOT), "status", "--json"], capture_output=True, text=True)
assert proc.returncode == 0, proc.stderr
status = json.loads(proc.stdout)
assert status["runtime_status"] == status["project_status"] == "COMPLETE"
assert status["terminal_completion"] == {"schema_version": 1, "valid": True,
    "problems": [], "final_verification_status": "PASS"}
assert status["active_task"] is None
assert not status["active_task_claimed"]
assert status["supervisor_turn_inflight"] is None
assert status["pending_interventions"] == 0
assert not status["stop"] and not status["human_review"]
assert status["last_consumed_message_id"] == 700104
assert status["last_authorized_dispatch"]["MESSAGE_ID"] == 700104
responses = {}
for suffix in ("/status", "/cockpit", "/alerts"):
    with urlopen(BASE + suffix, timeout=30) as response:
        responses[suffix] = {"http_status": response.status, "document": json.load(response)}
interp = responses["/cockpit"]["document"]["cockpit"]["interpretation"]
assert interp["state"]["family"] == "COMPLETE", interp
assert interp["state"]["worker"]["who"] is None
assert not interp["state"]["user_action_required"]
assert not interp["protocol"]["available"] and not interp["milestones"]
assert not interp["health"]["current_authorization"]["available"]
assert not interp["health"]["errors_and_warnings"]
assert interp["terminal_completion"] == {"available": True,
    "last_consumed_message_id": 700104, "final_verification_status": "PASS"}
assert interp["next_expected"]["message_id"] is None
assert "STATE_UNAVAILABLE" not in json.dumps(interp)
alerts_doc = responses["/alerts"]["document"]
alerts = alerts_doc["alerts_document"]["alerts"]
assert {item["rule"] for item in alerts} == {"project-complete"}, alerts_doc
assert all(item["severity"] == "informational" for item in alerts)
result = {"at": datetime.now(timezone.utc).isoformat(), "passed": True,
          "base": BASE, "method": "installed CLI status and GET only; no project rerun",
          "installed_status": status, "responses": responses}
(OUT / "live-http.json").write_text(json.dumps(result, indent=2, ensure_ascii=False) + "\n",
                                    encoding="utf-8")
print("PASS: live COMPLETE / FV PASS / MESSAGE 700104 / no active worker, current authorization or risk alert")
