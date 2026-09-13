"""Record completed regression results and exact tested product hashes."""
import hashlib
import json
from pathlib import Path
import re

OUT = Path(__file__).resolve().parent
ROOT = OUT.parent.parent
files = ["scripts/supervisor_control.py", "scripts/web_console_state.py",
         "scripts/web_console_alerts.py", "web_console/index.html",
         "scripts/test_terminal_complete_status.py", "scripts/test_web_console_state.py",
         "scripts/test_web_console_alerts.py", "scripts/test_web_console_frontend.py"]
results = {}
for name in ("focused-final", "full-suite", "full-suite-final"):
    log = (OUT / (name + ".txt")).read_text(encoding="utf-8-sig")
    match = re.search(r"Ran (\d+) tests in ([\d.]+)s", log)
    assert match and re.search(r"^OK(?: \(skipped=\d+\))?\s*$", log, re.M), name
    skipped = re.search(r"^OK \(skipped=(\d+)\)", log, re.M)
    skip_count = int(skipped[1]) if skipped else 0
    count = int(match[1])
    results[name] = {"tests_run": count, "passed": count - skip_count,
        "skipped": skip_count, "failures": 0, "errors": 0, "seconds": float(match[2]),
        "log": (OUT / (name + ".txt")).relative_to(ROOT).as_posix(),
        "skip_details": re.findall(r"^(test_.*) \.\.\. skipped (.*)$", log, re.M)}
for name in ("full-suite", "full-suite-final"):
    assert (OUT / (name + "-exit.txt")).read_text(encoding="utf-8-sig").strip() == "0"
data = {"full_suite_command": "python -B -m unittest discover -s scripts -p 'test_*.py' -v",
    "focused_modules": ["test_terminal_complete_status", "test_web_console_state",
        "test_web_console_alerts", "test_web_console_cockpit", "test_web_console_frontend",
        "test_web_console_shell_frontend", "test_supervisor_control",
        "test_supervisor_status_p9", "test_final_verification_gate"],
    "results": results,
    "final_source_sha256": {p: hashlib.sha256((ROOT / p).read_bytes()).hexdigest() for p in files}}
(OUT / "test-results.json").write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
print(json.dumps(results, indent=2))
