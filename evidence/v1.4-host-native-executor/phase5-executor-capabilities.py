"""Capability descriptions are not grants: sealed policy plus live gates authorize work.

Host discovery uses public installation files, never user sessions or credentials.
There is deliberately no self-reported tool-registration or arbitrary-command API.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
from urllib.parse import urlsplit

ASSURANCES = {"runtime_enforced", "trusted_host", "cooperative_unverified", "unavailable"}
MODES = {"filesystem": {"none", "read", "workspace"}, "shell": {"none"},
         "network": {"none", "https_get"}, "browser": {"none", "render"}, "gui": {"none"}}
OPERATIONS = {"fetch": {"op", "url"},
              "render": {"op", "area", "path", "width", "height", "screenshot"}}
MAX_FETCH_BYTES = 262144
MAX_RENDER_BYTES = 262144


def validate_url(url):
    if (not isinstance(url, str) or not 1 <= len(url) <= 2048
            or not url.isascii() or any(ord(c) <= 32 or ord(c) == 127 for c in url)
            or "\\" in url or "#" in url):
        raise ValueError("invalid HTTPS URL")
    parsed = urlsplit(url)
    if (parsed.scheme != "https" or not parsed.hostname or parsed.username is not None
            or parsed.password is not None or parsed.port not in (None, 443)
            or "%" in parsed.netloc or parsed.hostname.endswith(".")):
        raise ValueError("only credential-free HTTPS on port 443 is supported")
    return parsed


def browser_dependencies():
    # Do not resolve binaries from task data, project files or request-supplied env.
    # PATH/installation and these binaries belong to the trusted Runtime host.
    node = shutil.which("node")
    if os.name == "nt":
        choices = [Path(os.environ.get("ProgramFiles", "C:/Program Files")) /
                   "Google/Chrome/Application/chrome.exe",
                   Path(os.environ.get("ProgramFiles(x86)", "C:/Program Files (x86)")) /
                   "Microsoft/Edge/Application/msedge.exe"]
    else:
        choices = [Path(p) for name in ("chromium", "chromium-browser", "google-chrome")
                   if (p := shutil.which(name))]
    browser = next((str(p) for p in choices if p.is_file()), None)
    return {"node": node, "browser": browser}


def describe(policy, *, check_host=False):
    modes = policy["capabilities"]
    result = {}
    for name in ("shell", "network", "browser", "gui"):
        mode = modes[name]
        result[name] = {"mode": mode, "available": mode != "none", "assurance": "unavailable",
                        "operations": []}
        if mode == "none":
            result[name]["reason"] = "Not granted; direct ZCode tools are not Runtime-bound"
    if modes["network"] == "https_get":
        result["network"].update(assurance="runtime_enforced", operations=["fetch"],
            urls=policy["network_urls"], max_bytes=MAX_FETCH_BYTES, timeout_seconds=15,
            enforcement="Exact URL, public pinned IP, TLS, GET only; no redirects, auth/cookie headers, URL userinfo or proxy",
            limitation="Remote content is untrusted evidence; GET can have server-side effects; issued requests cannot be recalled")
    if modes["browser"] == "render":
        if check_host and not all(browser_dependencies().values()):
            raise ValueError("browser render unavailable: Node and Chrome/Chromium/Edge required")
        result["browser"].update(assurance="trusted_host", operations=["render"],
            provider="Runtime-owned fresh headless Chromium via Node CDP",
            prerequisites="Installed Node >=22 with WebSocket and compatible Chromium; each invocation can fail unavailable",
            enforcement="Runtime scopes input snapshot, viewport, evidence path and authority; browser supplies pixels/DOM",
            limits={"max_html_bytes": MAX_RENDER_BYTES, "width": [320, 1920], "height": [320, 1080],
                    "timeout_seconds": 30},
            limitation="Static self-contained HTML/CSS only: scripts, external resources, frames, navigation and interactions disabled; no host-wide OS/network sandbox or automatic visual judgment")
    return result


def host_inventory():
    """Installation evidence is explicitly weaker than a live automation handshake."""
    base = Path(os.environ.get("LOCALAPPDATA", "")) / "Programs/ZCode/resources/glm"
    packages = base / "packages"
    assets = {}
    for relative in ("zcode.cjs", "packages/browser-use-plugin/package.json",
                     "packages/browser-use-plugin/README.md", "packages/browser-use-plugin/scripts/browser-client.mjs",
                     "packages/zcode-cua-plugin/package.json"):
        path = base / relative
        if path.is_file():
            raw = path.read_bytes()
            assets[relative] = {"bytes": len(raw), "sha256": hashlib.sha256(raw).hexdigest()}
            if relative.endswith("package.json"):
                try:
                    assets[relative]["version"] = json.loads(raw)["version"]
                except (ValueError, KeyError):
                    pass
            if relative == "zcode.cjs":
                assets[relative]["present_markers_not_session_tools"] = [s for s in
                    ("Bash", "Shell", "web_search", "web_fetch", "PreToolUse", "PostToolUse", "mcp__node_repl__js")
                    if s.encode() in raw]
            if relative.endswith("browser-client.mjs"):
                assets[relative]["requires_host_bridge"] = b'zcode.node-repl.browser-control-bridge' in raw
    return {"schema_version": 1, "source": "read-only installation discovery",
            "installed_assets": assets,
            "runtime_browser_dependencies": browser_dependencies(),
            "zcode": {"bundle_present": (base / "zcode.cjs").is_file(),
                "browser_plugin_present": (packages / "browser-use-plugin/package.json").is_file(),
                "gui_plugin_present": (packages / "zcode-cua-plugin/package.json").is_file(),
                "assurance": "cooperative_unverified", "task_work_authorized": False,
                "session_tool_availability": "unknown", "runtime_hooks_attested": False},
            "assurance_levels": sorted(ASSURANCES)}


if __name__ == "__main__":
    argparse.ArgumentParser(description=__doc__).parse_args()
    print(json.dumps(host_inventory(), indent=2))
