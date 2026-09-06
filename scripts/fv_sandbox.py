"""FV-SANDBOX-ISOLATION-V1 — mechanical isolation boundary for Final Verification.

Separates LIVE_READ_ONLY verification from SANDBOX_DESTRUCTIVE verification:

- LIVE_READ_ONLY: may read live Runtime/project artifacts, must never mutate
  authoritative state.
- SANDBOX_DESTRUCTIVE: destructive/adversarial/mutation probes. Must execute
  only inside a sandbox root that is physically outside the live Runtime root,
  behind the mechanical path guard, with controlled process cwd/environment,
  and with a protected live-state integrity manifest compared before/after the
  narrow destructive-probe window.

Boundary layers (all mechanical, none dependent on agent discipline):

1. Structural isolation — the sandbox holds its own copy of the Runtime source
   material, so ``__file__``-derived module-level path globals inside the copy
   anchor to the sandbox. Probes import the sandbox copy; ``verify_probe_identity``
   fail-closes when a probe actually imported code from outside the sandbox.
   This eliminates the "incomplete module-global rebinding" failure class
   instead of asking probes to rebind globals correctly.
2. Path guard — every writable/critical binding is canonicalized (Windows-aware:
   case, drive, separators, reparse points/symlinks/junctions, drive-relative
   and relative traversal) and must resolve inside the sandbox before any
   destructive code begins. Rejections fail closed with
   ``FV_SANDBOX_ESCAPE_DETECTED``.
3. Integrity manifest — protected live authoritative state (active-project
   pointer, Runtime control state, canonical project goal, isolated project
   state, ...) is snapshotted before and compared after the probe window.
   Unexpected mutation fails closed with ``FV_LIVE_STATE_MUTATION_DETECTED``.

Isolation incidents permanently invalidate the verification attempt: this
module records machine-auditable outcome/evidence JSON; the FV receipt contract
(orchestrator.evaluate_final_verification_receipt) mechanically rejects any
receipt carrying ISOLATION_INCIDENT, so a same-attempt incident can never be
packaged as PASS.

This module is presentation-free and does not hardcode any concrete Runtime
installation path: the live root is always an explicit parameter.
"""

from __future__ import annotations

import hashlib
import json
import os
import secrets
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

SCHEMA_VERSION = 1

EXECUTION_MODES = ("LIVE_READ_ONLY", "SANDBOX_DESTRUCTIVE")

FV_GUARD_PASSED = "FV_GUARD_PASSED"
FV_GUARD_REJECTED_UNSAFE_CANDIDATE = "FV_GUARD_REJECTED_UNSAFE_CANDIDATE"
FV_SANDBOX_ESCAPE_DETECTED = "FV_SANDBOX_ESCAPE_DETECTED"
FV_LIVE_STATE_MUTATION_DETECTED = "FV_LIVE_STATE_MUTATION_DETECTED"

OUTCOME_CLEAN = "FV_SANDBOX_CLEAN"
OUTCOME_GUARD_BLOCKED = "FV_SANDBOX_GUARD_BLOCKED"
OUTCOME_INCIDENT = "FV_SANDBOX_ISOLATION_INCIDENT"

LIVE_MANIFEST_UNCHANGED = "UNCHANGED"
LIVE_MANIFEST_MUTATION = "MUTATION_DETECTED"

SANDBOX_PARENT_DIRNAME = "general-agent-runtime-fv"

# Environment variables stripped from destructive-probe subprocesses: they
# redirect import resolution or interpreter startup paths and are the historical
# contamination vectors. Everything else is inherited so probes stay runnable.
STRIPPED_PROBE_ENV_VARS = ("PYTHONPATH", "PYTHONHOME", "PYTHONSTARTUP")


class SandboxEscapeError(RuntimeError):
    """A candidate binding resolves outside the sandbox / onto live authority.

    Raised BEFORE destructive code begins; carries the full auditable binding
    context. Never raised after a mutation was observed — that is an incident
    (see FV_LIVE_STATE_MUTATION_DETECTED), not a guard rejection.
    """

    def __init__(self, binding_name: str, candidate: str, resolved: str,
                 reason: str, sandbox_root: str):
        self.binding_name = binding_name
        self.candidate = candidate
        self.resolved = resolved
        self.reason = reason
        self.sandbox_root = sandbox_root
        super().__init__(
            f"FV_SANDBOX_ESCAPE_DETECTED binding={binding_name!r} "
            f"candidate={candidate!r} resolved={resolved!r} reason={reason}"
        )

    def as_dict(self) -> dict:
        return {
            "binding_name": self.binding_name,
            "candidate": self.candidate,
            "resolved": self.resolved,
            "reason": self.reason,
            "sandbox_root": self.sandbox_root,
            "status": FV_SANDBOX_ESCAPE_DETECTED,
        }


# ---------------------------------------------------------------------------
# Windows-aware canonical path resolution
# ---------------------------------------------------------------------------

def canonical_path(path: str | os.PathLike) -> str:
    """Canonical, comparison-safe form of *path*.

    Windows-aware: anchors drive-relative references to the current
    working directory of their drive (``abspath``), resolves reparse points
    (symlinks/junctions/mounts) along the deepest existing prefix and appends
    non-existent tails (``realpath``), then normalizes case and separators so
    containment checks are text-exact and case-insensitive as the Win32 layer
    is. Environment variables are deliberately NOT expanded: ``realpath``
    semantics match what ordinary file APIs will actually open.
    """
    resolved = os.path.realpath(os.path.abspath(os.fspath(path)))
    return os.path.normcase(os.path.normpath(resolved))


def resolves_within(candidate: str, root: str) -> bool:
    """True iff canonical *candidate* is *root* itself or lies underneath it.

    Boundary-exact: ``C:\\root`` does not contain ``C:\\root-evil``.
    """
    return candidate == root or candidate.startswith(root + os.sep)


def containment_reason(candidate_resolved: str, sandbox_root: str,
                       runtime_root: str) -> str | None:
    """Rejection reason for a canonical candidate, or None when it is safe."""
    if resolves_within(candidate_resolved, runtime_root):
        return "RESOLVES_INTO_LIVE_RUNTIME_AUTHORITY"
    if not resolves_within(candidate_resolved, sandbox_root):
        return "RESOLVES_OUTSIDE_SANDBOX"
    return None


# ---------------------------------------------------------------------------
# Protected live-state integrity manifest
# ---------------------------------------------------------------------------

def _manifest_entry(path: Path) -> dict:
    entry = {"path": str(path), "exists": path.exists()}
    if entry["exists"]:
        if path.is_dir():
            entry.update(kind="dir", size=None, sha256=None,
                         mtime_ns=path.stat().st_mtime_ns)
        else:
            data = path.read_bytes()
            entry.update(kind="file", size=len(data),
                         sha256=hashlib.sha256(data).hexdigest(),
                         mtime_ns=path.stat().st_mtime_ns)
    else:
        entry.update(kind=None, size=None, sha256=None, mtime_ns=None)
    return entry


def default_protected_paths(runtime_root: str | os.PathLike) -> list[str]:
    """High-value live authoritative state for the integrity manifest.

    Read-only resolution from the live root; resilient to missing files. The
    active project's canonical goal and isolated state are included by
    resolving control/ACTIVE_PROJECT.json (the pointer itself is protected too).
    Narrow by design: trusted control-plane writes (claims, ledger, logs,
    archive) live elsewhere and must never appear here, so ordinary protocol
    activity cannot be misread as an FV escape.
    """
    root = Path(runtime_root)
    paths = [
        root / "control" / "ACTIVE_PROJECT.json",
        root / "control" / "project_state.json",
        root / "control" / "orchestrator_runtime.json",
        root / "control" / "budget.json",
        root / "control" / "STOP",
        root / "control" / "HUMAN_REVIEW",
    ]
    try:
        pointer = json.loads((root / "control" / "ACTIVE_PROJECT.json").read_text(
            encoding="utf-8"))
        project_root = root / str(pointer.get("project_root") or "")
        if str(pointer.get("project_root") or "") and project_root.is_dir():
            paths.append(project_root / "PROJECT_GOAL.md")
            paths.append(project_root / "project_state.json")
    except (OSError, ValueError):
        pass
    return paths


def build_live_manifest(paths: list[str | os.PathLike]) -> dict:
    """Snapshot existence/kind/size/SHA-256/mtime for each protected path.

    Entry paths are stored canonically so manifest comparison and the trusted
    allowed-writes policy match exactly regardless of caller casing.
    """
    entries = [_manifest_entry(Path(p)) for p in paths]
    for entry in entries:
        entry["path"] = canonical_path(entry["path"])
    entries.sort(key=lambda e: e["path"])
    body = json.dumps(entries, sort_keys=True, separators=(",", ":"))
    return {
        "schema_version": SCHEMA_VERSION,
        "created_at": _utc_now(),
        "entries": entries,
        "manifest_sha256": hashlib.sha256(body.encode("utf-8")).hexdigest(),
    }


def compare_live_manifests(before: dict, after: dict,
                           allowed_writes: list[str] | tuple = ()) -> dict:
    """Diff two manifests; separate unexpected mutation from trusted writes.

    ``allowed_writes`` lists canonical protected paths that a trusted Runtime
    protocol flow explicitly declared it would write inside this window; those
    differences are reported under ``allowed_trusted_writes`` and never
    escalate to an incident. Everything else is unexpected mutation.
    """
    allowed = {canonical_path(p) for p in allowed_writes}
    before_map = {e["path"]: e for e in before.get("entries", [])}
    after_map = {e["path"]: e for e in after.get("entries", [])}
    unexpected, trusted = [], []
    for key in sorted(set(before_map) | set(after_map)):
        old, new = before_map.get(key), after_map.get(key)
        if old == new:
            continue
        record = {"path": key, "before": old, "after": new}
        if key in allowed:
            trusted.append(record)
        else:
            unexpected.append(record)
    return {
        "status": LIVE_MANIFEST_UNCHANGED if not unexpected else LIVE_MANIFEST_MUTATION,
        "unexpected_mismatches": unexpected,
        "allowed_trusted_writes": trusted,
        "allowed_write_count": len(trusted),
    }


# ---------------------------------------------------------------------------
# Probe import identity (structural isolation proof)
# ---------------------------------------------------------------------------

def probe_identity_report(module_names: list[str]) -> dict:
    """Self-report a probe process must embed in its output.

    Captures the actual on-disk origin of imported modules plus cwd/executable
    so the driver can mechanically prove the probe imported the SANDBOX copies
    and ran inside the sandbox. Modules that are not imported return None.
    """
    import importlib

    modules = {}
    for name in module_names:
        try:
            modules[name] = getattr(importlib.import_module(name), "__file__", None)
        except Exception:
            modules[name] = None
    return {
        "cwd": os.getcwd(),
        "executable": sys.executable,
        "modules": modules,
    }


def verify_probe_identity(report: dict, sandbox_root: str | os.PathLike,
                          required_modules: list[str]) -> None:
    """Fail closed unless every required module resolved to a sandbox copy."""
    sandbox = canonical_path(sandbox_root)
    cwd = canonical_path(report.get("cwd") or "")
    if not resolves_within(cwd, sandbox):
        raise SandboxEscapeError("PROBE_CWD", str(report.get("cwd")), cwd,
                                 "PROBE_CWD_OUTSIDE_SANDBOX", sandbox)
    modules = report.get("modules") or {}
    for name in required_modules:
        origin = modules.get(name)
        resolved = canonical_path(origin) if origin else ""
        if not origin or not resolves_within(resolved, sandbox):
            raise SandboxEscapeError(
                f"PROBE_IMPORT:{name}", str(origin), resolved,
                "PROBE_IMPORTED_NON_SANDBOX_CODE", sandbox)


# ---------------------------------------------------------------------------
# Sandbox
# ---------------------------------------------------------------------------

def _utc_now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S+00:00", time.gmtime())


class FVSandbox:
    """A physically external, disposable sandbox root for destructive FV work."""

    def __init__(self, root: Path, runtime_root: Path, label: str,
                 project_id: str | None, identity_sha256: str, created_at: str):
        self.root = root
        self.canonical_root = canonical_path(root)
        self.runtime_root = Path(runtime_root)
        self.canonical_runtime_root = canonical_path(runtime_root)
        self.label = label
        self.project_id = project_id
        self.identity_sha256 = identity_sha256
        self.created_at = created_at
        self.probe_dir = root / "probe"
        self.runtime_copy_root = root / "runtime"
        self._identity_written = False

    # -- construction -------------------------------------------------------

    @classmethod
    def create(cls, runtime_root: str | os.PathLike, *,
               project_id: str | None = None, label: str = "fv",
               parent: str | os.PathLike | None = None) -> "FVSandbox":
        """Create the sandbox OUTSIDE the live Runtime root; fail closed otherwise.

        ``parent`` defaults to ``<system temp>\\general-agent-runtime-fv`` and may
        be overridden only for tests. Nesting in either direction between the
        sandbox and the live root is mechanically rejected at creation.
        """
        runtime_root = Path(runtime_root).resolve()
        if not runtime_root.is_dir():
            raise RuntimeError(f"runtime root does not exist: {runtime_root}")
        base = Path(parent) if parent else (
            Path(tempfile.gettempdir()) / SANDBOX_PARENT_DIRNAME)
        token = secrets.token_hex(6)
        name = f"{time.strftime('%Y%m%d-%H%M%S')}-{label}-{token}"
        root = base / name

        # Fail closed BEFORE any directory is created: the sandbox and the
        # live Runtime root must be disjoint in both directions.
        live_canonical = canonical_path(runtime_root)
        root_canonical = canonical_path(root)
        if resolves_within(root_canonical, live_canonical) \
                or resolves_within(live_canonical, root_canonical):
            raise RuntimeError(
                "sandbox root and live Runtime root must be disjoint "
                f"(sandbox={root_canonical}, live={live_canonical})")
        base.mkdir(parents=True, exist_ok=True)
        root.mkdir(parents=True)

        sandbox = cls(root, runtime_root, label, project_id, "", _utc_now())
        inside = sandbox.canonical_root
        live = sandbox.canonical_runtime_root
        if resolves_within(inside, live) or resolves_within(live, inside):
            raise RuntimeError(
                "sandbox root and live Runtime root must be disjoint "
                f"(sandbox={inside}, live={live})")

        identity = {
            "schema_version": SCHEMA_VERSION,
            "kind": "FV_SANDBOX_IDENTITY",
            "created_at": sandbox.created_at,
            "runtime_root": live,
            "sandbox_root": inside,
            "project_id": project_id,
            "label": label,
            "pid": os.getpid(),
            "executable": sys.executable,
        }
        body = json.dumps(identity, sort_keys=True, separators=(",", ":"))
        sandbox.identity_sha256 = hashlib.sha256(body.encode("utf-8")).hexdigest()
        identity["identity_sha256"] = sandbox.identity_sha256
        (root / "sandbox_identity.json").write_text(
            json.dumps(identity, indent=2), encoding="utf-8")
        sandbox.probe_dir.mkdir()
        sandbox._identity_written = True
        return sandbox

    # -- fixture population --------------------------------------------------

    def populate_runtime_copy(self, mapping: dict[str, str] | None = None) -> dict:
        """Copy Runtime source material INTO the sandbox; structural isolation.

        Default copies the live root's ``orchestrator.py`` and everything in
        ``scripts/`` preserving relative layout, so ``__file__``-derived module
        globals inside the copies anchor under ``<sandbox>\\runtime``. Explicit
        ``mapping`` (source path -> sandbox-relative destination) serves
        synthetic fixtures. Returns a sha256 manifest of the copies.
        """
        if mapping is None:
            mapping = {}
            if (self.runtime_root / "orchestrator.py").is_file():
                mapping[str(self.runtime_root / "orchestrator.py")] = \
                    "runtime/orchestrator.py"
            live_scripts = self.runtime_root / "scripts"
            if live_scripts.is_dir():
                for item in sorted(live_scripts.glob("*.py")):
                    mapping[str(item)] = f"runtime/scripts/{item.name}"
        manifest = {}
        for source, rel_dest in mapping.items():
            source_path = Path(source)
            dest = self.root / rel_dest
            if not source_path.is_file():
                raise FileNotFoundError(f"fixture source missing: {source_path}")
            dest.parent.mkdir(parents=True, exist_ok=True)
            data = source_path.read_bytes()
            dest.write_bytes(data)
            manifest[rel_dest] = hashlib.sha256(data).hexdigest()
        (self.root / "runtime_copy_manifest.json").write_text(
            json.dumps(manifest, indent=2, sort_keys=True), encoding="utf-8")
        return manifest

    def write_probe_module(self, name: str, code: str) -> Path:
        """Materialize a probe module inside the sandbox; returns its path."""
        if not name.replace("_", "").isalnum() or name.startswith("_"):
            raise ValueError("probe module name must be plain [A-Za-z0-9_]")
        path = self.probe_dir / f"{name}.py"
        path.write_text(code, encoding="utf-8")
        return path

    # -- mechanical path guard ------------------------------------------------

    def validate_binding(self, name: str, candidate: str | os.PathLike) -> str:
        """Canonicalize *candidate* and require it to resolve inside the sandbox.

        Called for every writable/critical binding BEFORE destructive code
        begins. Raises SandboxEscapeError (fail closed) with an explicit reason
        when the candidate resolves into the live Runtime root or anywhere
        outside the sandbox. Performs no filesystem writes.
        """
        raw = os.fspath(candidate) if candidate is not None else ""
        if not raw or not raw.strip():
            raise SandboxEscapeError(name, str(candidate), "",
                                     "EMPTY_BINDING", self.canonical_root)
        resolved = canonical_path(raw)
        reason = containment_reason(resolved, self.canonical_root,
                                    self.canonical_runtime_root)
        if reason:
            raise SandboxEscapeError(name, raw, resolved, reason,
                                     self.canonical_root)
        return resolved

    def validate_bindings(self, bindings: dict) -> dict:
        """Guard a full binding set all-or-nothing; returns canonical bindings."""
        return {name: self.validate_binding(name, value)
                for name, value in bindings.items()}

    # -- process / import isolation -------------------------------------------

    def child_environment(self, pythonpath_entries: list[str] | None = None) -> dict:
        """Sanitized environment for destructive probes.

        Strips interpreter-path contamination vectors (PYTHONPATH / PYTHONHOME /
        PYTHONSTARTUP), then pins PYTHONPATH to the sandbox's own import roots
        (runtime copy, its scripts dir, probe dir) plus caller-approved extra
        entries that must themselves resolve inside the sandbox.
        """
        env = {k: v for k, v in os.environ.items()
               if k not in STRIPPED_PROBE_ENV_VARS}
        entries = [
            str(self.runtime_copy_root),
            str(self.runtime_copy_root / "scripts"),
            str(self.probe_dir),
        ]
        for extra in pythonpath_entries or []:
            entries.append(self.validate_binding("PYTHONPATH_ENTRY", extra))
        # De-duplicate, preserving order; keep only existing dirs.
        seen, final = set(), []
        for entry in entries:
            key = canonical_path(entry)
            if key not in seen and Path(entry).is_dir():
                seen.add(key)
                final.append(entry)
        env["PYTHONPATH"] = os.pathsep.join(final)
        return env

    def run_probe(self, argv: list[str], *, timeout: float = 120.0,
                  cwd: str | os.PathLike | None = None,
                  pythonpath_entries: list[str] | None = None) -> dict:
        """Run one destructive probe subprocess with mechanical cwd/env control.

        The working directory and PYTHONPATH are always sandbox-validated; a
        caller-supplied cwd must itself resolve inside the sandbox or the probe
        is refused before it starts. Output is captured for evidence.
        """
        cwd_resolved = self.validate_binding(
            "PROBE_CWD", cwd if cwd is not None else self.probe_dir)
        if not argv:
            raise ValueError("probe argv must not be empty")
        # Refuse to execute any live Runtime file as probe material. Only the
        # live-authority reason blocks: the interpreter itself and other
        # non-sandbox tooling are expected to live outside the sandbox.
        for element in argv[1:]:
            try:
                candidate = os.fspath(element)
            except TypeError:
                continue
            if os.path.exists(candidate):
                resolved = canonical_path(candidate)
                if containment_reason(resolved, self.canonical_root,
                                      self.canonical_runtime_root) \
                        == "RESOLVES_INTO_LIVE_RUNTIME_AUTHORITY":
                    raise SandboxEscapeError(
                        "PROBE_ARGV", candidate, resolved,
                        "RESOLVES_INTO_LIVE_RUNTIME_AUTHORITY", self.canonical_root)
        started = time.time()
        try:
            proc = subprocess.run(
                [str(a) for a in argv],
                cwd=cwd_resolved,
                env=self.child_environment(pythonpath_entries),
                capture_output=True, text=True, timeout=timeout,
            )
            result = {
                "argv": [str(a) for a in argv],
                "cwd": cwd_resolved,
                "returncode": proc.returncode,
                "stdout": proc.stdout,
                "stderr": proc.stderr,
                "timed_out": False,
            }
        except subprocess.TimeoutExpired as exc:
            result = {
                "argv": [str(a) for a in argv],
                "cwd": cwd_resolved,
                "returncode": None,
                "stdout": (exc.stdout or "") if isinstance(exc.stdout, str) else "",
                "stderr": (exc.stderr or "") if isinstance(exc.stderr, str) else "",
                "timed_out": True,
            }
        result["duration_seconds"] = round(time.time() - started, 3)
        return result

    # -- integrity window ------------------------------------------------------

    def protected_paths(self, extra: list | None = None) -> list[str]:
        paths = default_protected_paths(self.runtime_root)
        for item in extra or []:
            paths.append(item)
        return paths

    def write_outcome(self, outcome: dict) -> Path:
        path = self.root / "sandbox_outcome.json"
        path.write_text(json.dumps(outcome, indent=2), encoding="utf-8")
        return path

    def destructive_window(self, probe_argv: list[str], *,
                           bindings: dict | None = None,
                           protected_extra: list | None = None,
                           allowed_writes: list | tuple = (),
                           cwd: str | os.PathLike | None = None,
                           pythonpath_entries: list[str] | None = None,
                           timeout: float = 120.0,
                           required_probe_modules: list[str] | None = None,
                           probe_stdout_identity: bool = False) -> dict:
        """The narrow destructive-probe window: guard → probe → integrity check.

        1. snapshot the protected live-state manifest;
        2. mechanically validate every binding (fail closed BEFORE the probe
           starts — nothing destructive has executed at that point);
        3. run the probe with controlled cwd/env;
        4. recompute and compare the manifest;
        5. emit one machine-auditable outcome dict and persist it in-sandbox.

        ``FV_GUARD_REJECTED_UNSAFE_CANDIDATE`` means the guard blocked an unsafe
        candidate before execution (the boundary working — for negative-path
        probes this is the expected result). ``FV_LIVE_STATE_MUTATION_DETECTED``
        or any escape past the guard sets ``ISOLATION_INCIDENT`` true: the
        attempt is permanently invalid for PASS purposes and the sandbox is
        retained (never cleaned) for forensics.
        """
        outcome = {
            "schema_version": SCHEMA_VERSION,
            "kind": "FV_SANDBOX_OUTCOME",
            "created_at": _utc_now(),
            "sandbox_root": self.canonical_root,
            "sandbox_identity_sha256": self.identity_sha256,
            "runtime_root": self.canonical_runtime_root,
            "project_id": self.project_id,
            "guard_status": None,
            "guard_rejections": [],
            "probe": None,
            "live_manifest_status": None,
            "live_manifest_mismatches": [],
            "allowed_trusted_writes": [],
            "isolation_incident": False,
            "incident_evidence": [],
        }
        protected = self.protected_paths(protected_extra)
        before = build_live_manifest(protected)

        canonical_bindings = {}
        try:
            canonical_bindings = self.validate_bindings(bindings or {})
            if cwd is not None:
                canonical_bindings["PROBE_CWD_EXPLICIT"] = self.validate_binding(
                    "PROBE_CWD_EXPLICIT", cwd)
            outcome["guard_status"] = FV_GUARD_PASSED
        except SandboxEscapeError as exc:
            outcome["guard_status"] = FV_GUARD_REJECTED_UNSAFE_CANDIDATE
            outcome["guard_rejections"].append(exc.as_dict())
            outcome["probe_executed"] = False
            outcome["status"] = FV_SANDBOX_ESCAPE_DETECTED
            outcome["outcome"] = OUTCOME_GUARD_BLOCKED
            after = build_live_manifest(protected)
            diff = compare_live_manifests(before, after, allowed_writes)
            outcome["live_manifest_status"] = diff["status"]
            outcome["live_manifest_mismatches"] = diff["unexpected_mismatches"]
            if diff["unexpected_mismatches"]:
                outcome["isolation_incident"] = True
                outcome["status"] = FV_LIVE_STATE_MUTATION_DETECTED
                outcome["outcome"] = OUTCOME_INCIDENT
            outcome["bindings_validated"] = {}
            self.write_outcome(outcome)
            return outcome

        outcome["bindings_validated"] = canonical_bindings
        result = self.run_probe(probe_argv, timeout=timeout, cwd=cwd,
                                pythonpath_entries=pythonpath_entries)
        if probe_stdout_identity and required_probe_modules:
            self._verify_reported_identity(result, required_probe_modules, outcome)
        outcome["probe"] = result
        outcome["probe_executed"] = True

        after = build_live_manifest(protected)
        diff = compare_live_manifests(before, after, allowed_writes)
        outcome["live_manifest_status"] = diff["status"]
        outcome["live_manifest_mismatches"] = diff["unexpected_mismatches"]
        outcome["allowed_trusted_writes"] = diff["allowed_trusted_writes"]
        if diff["unexpected_mismatches"]:
            outcome["isolation_incident"] = True
            outcome["status"] = FV_LIVE_STATE_MUTATION_DETECTED
            outcome["outcome"] = OUTCOME_INCIDENT
            incident_dir = self.root / "incident_evidence"
            incident_dir.mkdir(exist_ok=True)
            (incident_dir / "live_manifest_before.json").write_text(
                json.dumps(before, indent=2), encoding="utf-8")
            (incident_dir / "live_manifest_after.json").write_text(
                json.dumps(after, indent=2), encoding="utf-8")
            outcome["incident_evidence"].append(
                str(incident_dir / "live_manifest_before.json"))
            outcome["incident_evidence"].append(
                str(incident_dir / "live_manifest_after.json"))
        else:
            if result.get("timed_out"):
                outcome["status"] = "FV_PROBE_TIMEOUT"
            elif result.get("returncode") == 0:
                outcome["status"] = "FV_PROBE_COMPLETED"
            else:
                outcome["status"] = "FV_PROBE_FAILED"
            outcome["outcome"] = OUTCOME_CLEAN
        outcome["manifest_sha256_before"] = before["manifest_sha256"]
        outcome["manifest_sha256_after"] = after["manifest_sha256"]
        self.write_outcome(outcome)
        return outcome

    def _verify_reported_identity(self, probe_result: dict,
                                  required_modules: list[str],
                                  outcome: dict) -> None:
        """Validate a probe's embedded PROBE_IDENTITY JSON (fail closed)."""
        try:
            report = json.loads(probe_result.get("stdout") or "{}")
            report = report["PROBE_IDENTITY"]
        except Exception:
            outcome["isolation_incident"] = True
            outcome["status"] = FV_SANDBOX_ESCAPE_DETECTED
            outcome["outcome"] = OUTCOME_INCIDENT
            outcome["identity_verification"] = {
                "status": "MISSING_PROBE_IDENTITY_REPORT",
            }
            return
        try:
            verify_probe_identity(report, self.canonical_root, required_modules)
            outcome["identity_verification"] = {
                "status": "SANDBOX_IDENTITY_CONFIRMED",
                "report": report,
            }
        except SandboxEscapeError as exc:
            outcome["isolation_incident"] = True
            outcome["status"] = FV_SANDBOX_ESCAPE_DETECTED
            outcome["outcome"] = OUTCOME_INCIDENT
            outcome["identity_verification"] = {"status": exc.as_dict()}

    # -- lifecycle --------------------------------------------------------------

    def clean(self) -> bool:
        """Remove a sandbox that completed without an isolation incident.

        Bounded cleanup policy: a sandbox is deletable only when its recorded
        outcome (if any) is incident-free; incident sandboxes are retained for
        forensic audit and must never be cleaned by this method.
        """
        outcome_file = self.root / "sandbox_outcome.json"
        if outcome_file.is_file():
            try:
                outcome = json.loads(outcome_file.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                return False
            if outcome.get("isolation_incident") is True:
                return False
        shutil.rmtree(self.root, ignore_errors=False)
        return True


def render_contract_summary() -> dict:
    """Machine-readable summary of the isolation contract for FV drivers."""
    return {
        "schema_version": SCHEMA_VERSION,
        "execution_modes": list(EXECUTION_MODES),
        "guard_passed": FV_GUARD_PASSED,
        "guard_rejected_unsafe_candidate": FV_GUARD_REJECTED_UNSAFE_CANDIDATE,
        "escape_status": FV_SANDBOX_ESCAPE_DETECTED,
        "mutation_status": FV_LIVE_STATE_MUTATION_DETECTED,
        "outcomes": {
            "clean": OUTCOME_CLEAN,
            "guard_blocked": OUTCOME_GUARD_BLOCKED,
            "incident": OUTCOME_INCIDENT,
        },
        "live_manifest": {
            "unchanged": LIVE_MANIFEST_UNCHANGED,
            "mutation": LIVE_MANIFEST_MUTATION,
        },
        "stripped_probe_env_vars": list(STRIPPED_PROBE_ENV_VARS),
        "incident_policy": (
            "any escape past the guard or unexpected protected-state mutation "
            "sets isolation_incident=true and permanently invalidates the "
            "attempt; the FV receipt contract rejects such receipts"
        ),
    }


def _selfcheck() -> int:
    """End-to-end mechanical self-test against throwaway synthetic roots."""
    holder = Path(tempfile.mkdtemp(prefix="fviso-selfcheck-"))
    synthetic_live = holder / "synthetic_live"
    (synthetic_live / "control").mkdir(parents=True)
    (synthetic_live / "control" / "ACTIVE_PROJECT.json").write_text(
        json.dumps({"schema_version": 1, "project_id": "selfcheck",
                    "project_root": "projects/selfcheck"}), encoding="utf-8")
    (synthetic_live / "control" / "project_state.json").write_text(
        "{}", encoding="utf-8")
    (synthetic_live / "orchestrator.py").write_text("# synthetic\n", encoding="utf-8")
    (synthetic_live / "scripts").mkdir()
    (synthetic_live / "scripts" / "helper.py").write_text("# synthetic\n", encoding="utf-8")

    checks = []
    sandbox = FVSandbox.create(synthetic_live, project_id="selfcheck",
                               label="selfcheck", parent=holder / "sandboxes")
    checks.append(("sandbox_outside_live", not resolves_within(
        sandbox.canonical_root, sandbox.canonical_runtime_root)))
    checks.append(("sandbox_outside_live_reverse", not resolves_within(
        sandbox.canonical_runtime_root, sandbox.canonical_root)))
    manifest = sandbox.populate_runtime_copy()
    checks.append(("runtime_copy_populated", bool(manifest)))
    accepted = sandbox.validate_binding("OUT", sandbox.root / "control" / "state.json")
    checks.append(("sandbox_bound_path_accepted", bool(accepted)))
    rejected = 0
    for candidate in (
        str(synthetic_live / "control" / "project_state.json"),
        str(sandbox.root / ".." / ".." / "escape.txt"),
    ):
        try:
            sandbox.validate_binding("CANDIDATE", candidate)
        except SandboxEscapeError:
            rejected += 1
    checks.append(("unsafe_candidates_rejected", rejected == 2))
    probe = sandbox.write_probe_module("selfcheck_probe", "print('ok')\n")
    result = sandbox.run_probe([sys.executable, str(probe)], timeout=60)
    checks.append(("probe_ran_in_sandbox", result["returncode"] == 0))
    outcome = sandbox.destructive_window(
        [sys.executable, str(probe)], bindings={"OUT": sandbox.root / "tmp.bin"})
    checks.append(("window_clean", outcome["outcome"] == OUTCOME_CLEAN))
    checks.append(("manifest_unchanged",
                   outcome["live_manifest_status"] == LIVE_MANIFEST_UNCHANGED))
    checks.append(("no_incident", outcome["isolation_incident"] is False))
    payload = {
        "kind": "FV_SANDBOX_SELFCHECK",
        "created_at": _utc_now(),
        "sandbox_root": sandbox.canonical_root,
        "checks": checks,
        "all_passed": all(ok for _, ok in checks),
    }
    print(json.dumps(payload, indent=2))
    return 0 if payload["all_passed"] else 2


if __name__ == "__main__":
    sys.exit(_selfcheck())
