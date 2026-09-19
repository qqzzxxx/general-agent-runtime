"""P9 Registry-driven Runtime creation from the supported stable release.

This module owns the template catalog, the copied/excluded
Runtime-template contract, and every pure destination/label validation the
create operation performs. The copy itself is executed only by the server,
under its create lock, into a temporary sibling directory that is renamed
into place only after a complete, verified copy — and registered only after
the Runtime's own control-plane probe validates the result.

Safety contract (tested):

- The template source is always the Console installation itself
  (`console_installation`); the browser can never name a source path.
- Only an explicit allowlist is copied: the release product files, the
  product directories (`docs`, `profiles`, `scripts`, `web_console`), the
  static control-plane seed documents, and `handoff/PROTOCOL.md`.
  Everything else — projects, workspaces, ledgers, claims, dispatch
  archives, registry data, logs, STOP/attention flags, caches — is
  excluded by construction.
- `__pycache__` and `*.pyc` are never copied; reparse points (symlinks and
  Windows junctions) in the source tree or in the destination parent chain
  refuse the operation (`CREATE_SOURCE_UNSAFE` /
  `CREATE_DESTINATION_UNSAFE`).
- The destination must be absolute, normalized, previously non-existing,
  outside the console root, the data directory, and every registered
  Runtime root (in both containment directions).
- Rollback removes only a tree whose recorded marker file still matches
  the copied bytes; any mismatch is reported, never deleted.
"""
from __future__ import annotations

import hashlib
import os
import re
import unicodedata
from pathlib import Path

SCHEMA_VERSION = 1

TEMPLATE_ID = "gar-local-stable"
TEMPLATE_IDS = (TEMPLATE_ID,)

DESTINATION_MAX_CHARS = 2048
LABEL_MAX_CHARS = 120
MAX_PARENT_CHAIN_DEPTH = 64

RELEASE_FILES = (
    ".gitignore",
    "AI_BOOTSTRAP.md",
    "LICENSE",
    "README.md",
    "V1.3_PRODUCT_SPEC.md",
    "orchestrator.py",
    "PAUSE_AGENT_SYSTEM.ps1",
    "PREPARE_ZCODE_AUTOMATION.ps1",
    "REQUEST_SUPERVISOR_INTERVENTION.ps1",
    "RESUME_AGENT_SYSTEM.ps1",
    "RESUME_HUMAN_REVIEW.ps1",
    "SHOW_AGENT_STATUS.ps1",
    "SHOW_AGENT_TIMELINE.ps1",
    "SHOW_EXECUTOR_FEEDBACK.ps1",
    "SHOW_SUPERVISOR_INTERVENTIONS.ps1",
    "SHOW_SUPERVISOR_TASKS.ps1",
    "START_AGENT_SYSTEM.ps1",
    "START_PROJECT.ps1",
    "START_WEB_CONSOLE.ps1",
    "STOP_AGENT_SYSTEM.ps1",
    "STOP_WEB_CONSOLE.ps1",
)
RELEASE_DIRECTORIES = ("docs", "profiles", "scripts", "web_console")
# Static control-plane seed documents only. Runtime state files
# (ACTIVE_PROJECT.json, STOP, HUMAN_REVIEW, orchestrator_runtime.json,
# supervisor_* stores/locks) are deliberately absent from this allowlist.
CONTROL_SEED_FILES = (
    "budget.json",
    "CODEX_SUPERVISOR_RUNTIME.md",
    "SUPERVISOR_PROTOCOL_REFERENCE.md",
    "EXECUTOR_TASK_TEMPLATE.md",
    "FINAL_VERIFICATION_POLICY.md",
    "HUMAN_DECISION_TEMPLATE.json",
    "project_state.example.json",
    "SUPERVISOR_POLICY.md",
    "ZCODE_SCHEDULED_AUTOMATION_PROMPT.md",
)
HANDOFF_SEED_FILES = ("PROTOCOL.md",)
# Names never copied anywhere inside the tree.
COPY_EXCLUDED_NAMES = frozenset({"__pycache__"})
COPY_EXCLUDED_SUFFIXES = (".pyc",)
# Verified present (not merely absent) before a copy is considered complete.
REQUIRED_RELEASE_PATHS = (
    "orchestrator.py",
    "scripts/supervisor_control.py",
    "scripts/provider_usage.py",
    "scripts/supervisor_context.py",
    "scripts/supervisor_inspect.py",
    "scripts/supervisor_intelligence.py",
    "scripts/executor_claim.py",
    "scripts/executor_fence.py",
    "scripts/executor_completion.py",
    "scripts/preflight.py",
    "scripts/start_project.py",
    "web_console/index.html",
    "START_WEB_CONSOLE.ps1",
    "START_AGENT_SYSTEM.ps1",
    "control/ZCODE_SCHEDULED_AUTOMATION_PROMPT.md",
)
# Reserved directory prefix used for atomic create-temp work.
RESERVED_TEMP_PREFIX = ".create-tmp-"

MARKER_RELATIVE = "scripts/supervisor_control.py"

WINDOWS_DEVICE_NAMES = frozenset({
    "CON", "PRN", "AUX", "NUL",
    "COM1", "COM2", "COM3", "COM4", "COM5", "COM6", "COM7", "COM8", "COM9",
    "LPT1", "LPT2", "LPT3", "LPT4", "LPT5", "LPT6", "LPT7", "LPT8", "LPT9",
})
# FILE_ATTRIBUTE_REPARSE_POINT on Windows (lstat / st_file_attributes).
REPARSE_POINT_FLAG = 0x400


class CreateError(ValueError):
    """A Runtime-create request, source, or destination that fails closed."""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


def release_template_catalog() -> list[dict]:
    """The server-controlled allowlist of supported release templates."""
    return [{
        "template_id": TEMPLATE_ID,
        "title": "General Agent Runtime — local stable release",
        "description":
            "Create a new isolated Runtime Root from this installation's "
            "supported release skeleton: product, control plane, profiles, "
            "web console, and documentation only — never projects, "
            "ledgers, claims, flags, logs, or registry data.",
        "source": {"kind": "console_installation",
                   "note": "the Console installation this server runs "
                           "from; the browser cannot choose a source path"},
        "compatibility": {"status_schema_versions": [1]},
    }]


def validate_template_id(value) -> str:
    if value not in TEMPLATE_IDS:
        raise CreateError("CREATE_UNKNOWN_TEMPLATE",
                          f"unknown Runtime template {value!r}")
    return value


def validate_label(value) -> str:
    if not isinstance(value, str) or not value.strip():
        raise CreateError("CREATE_LABEL_INVALID",
                          "the Runtime label must be a non-empty string")
    if value != value.strip():
        raise CreateError("CREATE_LABEL_INVALID",
                          "the Runtime label must not start or end with "
                          "whitespace")
    label = value.strip()
    if len(label) > LABEL_MAX_CHARS:
        raise CreateError("CREATE_LABEL_INVALID",
                          "the Runtime label is too long")
    if any(unicodedata.category(char) == "Cc" for char in label):
        raise CreateError("CREATE_LABEL_INVALID",
                          "the Runtime label must not contain control "
                          "characters")
    return label


def normalize_destination(value) -> Path:
    """Normalize and validate a destination directory path. The
    destination must be absolute, bounded, free of dot segments, UNC
    targets, device names, control characters, and reserved names."""
    if not isinstance(value, str) or not value.strip():
        raise CreateError("CREATE_INVALID_DESTINATION",
                          "the destination must be a non-empty path string")
    if len(value) > DESTINATION_MAX_CHARS:
        raise CreateError("CREATE_INVALID_DESTINATION",
                          "the destination path is too long")
    if any(unicodedata.category(char) == "Cc" for char in value):
        raise CreateError("CREATE_INVALID_DESTINATION",
                          "the destination path must not contain control "
                          "characters")
    if value.startswith("\\\\") or value.startswith("//"):
        raise CreateError("CREATE_INVALID_DESTINATION",
                          "UNC destinations are not supported")
    candidate = Path(value)
    if not candidate.is_absolute():
        raise CreateError("CREATE_INVALID_DESTINATION",
                          "the destination must be an absolute path")
    parts = [part for part in candidate.parts[1:]] \
        if candidate.drive else list(candidate.parts)
    for part in parts:
        if part in (".", "..", ""):
            raise CreateError("CREATE_INVALID_DESTINATION",
                              "the destination path must be normalized "
                              "(no dot segments)")
        stem = part.split(".")[0].upper()
        if stem in WINDOWS_DEVICE_NAMES:
            raise CreateError("CREATE_INVALID_DESTINATION",
                              f"the destination path uses a reserved "
                              f"device name: {part!r}")
        if part.endswith(".") or part.endswith(" "):
            raise CreateError("CREATE_INVALID_DESTINATION",
                              "destination segments must not end with a "
                              "dot or a space")
        if part.startswith(RESERVED_TEMP_PREFIX):
            raise CreateError("CREATE_INVALID_DESTINATION",
                              "the destination uses a reserved name")
    if candidate == candidate.parent:
        raise CreateError("CREATE_INVALID_DESTINATION",
                          "the destination must not be a drive root")
    return Path(os.path.normpath(str(candidate)))


def _normcase_resolved(path: Path) -> str:
    return os.path.normcase(str(Path(path).resolve()))


def check_destination_conflicts(destination: Path, *, console_root: Path,
                                data_dir: Path,
                                registered_roots) -> None:
    """Refuse destinations that equal, sit inside, or contain the console
    installation, the Console data directory, or any registered Runtime
    root."""
    dest = _normcase_resolved(destination)
    protected = [(_normcase_resolved(console_root), "console installation"),
                 (_normcase_resolved(data_dir), "Console data directory")]
    for registered in registered_roots or []:
        if isinstance(registered, str) and registered:
            protected.append((_normcase_resolved(Path(registered)),
                              "registered Runtime root"))
    for root, label in protected:
        if dest == root:
            raise CreateError("CREATE_DESTINATION_CONFLICTS",
                              f"the destination is the {label}")
        if dest.startswith(root + os.sep):
            raise CreateError("CREATE_DESTINATION_CONFLICTS",
                              f"the destination is inside the {label}")
        if root.startswith(dest + os.sep):
            raise CreateError("CREATE_DESTINATION_CONFLICTS",
                              f"the destination would contain the {label}")


def is_reparse(path: Path) -> bool:
    try:
        info = os.lstat(path)
    except OSError:
        return False
    if hasattr(info, "st_file_attributes"):
        if info.st_file_attributes & REPARSE_POINT_FLAG:
            return True
    import stat as stat_module
    return stat_module.S_ISLNK(info.st_mode)


def check_destination_parent(destination: Path) -> None:
    """The destination parent must exist, be a real directory, and sit on
    a reparse-free existing ancestor chain."""
    parent = Path(destination).parent
    chain = []
    current = parent
    for _ in range(MAX_PARENT_CHAIN_DEPTH):
        chain.append(current)
        if current == current.parent:
            break
        current = current.parent
    deepest_existing = None
    for candidate in reversed(chain):
        if candidate.exists():
            deepest_existing = candidate
    if deepest_existing is None:
        raise CreateError("CREATE_DESTINATION_PARENT_MISSING",
                          "the destination parent does not exist")
    if not parent.exists():
        raise CreateError("CREATE_DESTINATION_PARENT_MISSING",
                          f"the destination parent does not exist: "
                          f"{parent}")
    for candidate in chain:
        if not candidate.exists():
            break
        if is_reparse(candidate):
            raise CreateError("CREATE_DESTINATION_UNSAFE",
                              "the destination parent chain crosses a "
                              "symlink or junction")
    if not parent.is_dir():
        raise CreateError("CREATE_DESTINATION_PARENT_INVALID",
                          "the destination parent is not a directory")


def plan_copy(source_root: Path) -> dict:
    """Verify the release skeleton is complete before anything is written.

    Validates the full named allowlist (files, directories, control and
    handoff seeds), not just REQUIRED_RELEASE_PATHS: copy_entry skips absent
    entries silently, so a source missing any seed would otherwise produce a
    created Runtime that the fresh-install readiness proof can never verify.
    """
    source_root = Path(source_root)
    expected_files = set(RELEASE_FILES) | set(REQUIRED_RELEASE_PATHS)
    expected_files.update("control/" + name for name in CONTROL_SEED_FILES)
    expected_files.update("handoff/" + name for name in HANDOFF_SEED_FILES)
    missing = sorted(rel for rel in expected_files
                     if not (source_root / rel).is_file())
    missing.extend(sorted(name for name in RELEASE_DIRECTORIES
                          if not (source_root / name).is_dir()))
    if missing:
        raise CreateError("CREATE_SOURCE_INCOMPLETE",
                          "the template source is not a complete "
                          f"supported release skeleton; missing: "
                          f"{', '.join(missing[:8])}")
    files = len(RELEASE_FILES) + len(CONTROL_SEED_FILES) \
        + len(HANDOFF_SEED_FILES)
    return {"files": files, "missing": []}


def _copy_file(source: Path, target: Path) -> int:
    target.parent.mkdir(parents=True, exist_ok=True)
    data = source.read_bytes()
    with target.open("wb") as handle:
        handle.write(data)
    return len(data)


def copy_skeleton(source_root: Path, destination_temp: Path) -> dict:
    """Copy the release allowlist from the source into the temporary
    destination. Refuses reparse points anywhere it looks and never
    descends into excluded names."""
    source_root = Path(source_root)
    destination_temp = Path(destination_temp)
    if destination_temp.exists():
        raise CreateError("CREATE_COPY_FAILED",
                          "the temporary destination already exists")
    total_files = 0
    total_bytes = 0

    def copy_entry(relative: str) -> None:
        nonlocal total_files, total_bytes
        source = source_root / relative
        if is_reparse(source):
            raise CreateError("CREATE_SOURCE_UNSAFE",
                              f"the template source contains a symlink or "
                              f"junction at {relative!r}")
        if source.is_file():
            total_bytes += _copy_file(source, destination_temp / relative)
            total_files += 1
        elif source.is_dir():
            for base, dirs, files in os.walk(source):
                base_path = Path(base)
                for entry in dirs:
                    if is_reparse(base_path / entry):
                        relative = (base_path / entry) \
                            .relative_to(source_root).as_posix()
                        raise CreateError(
                            "CREATE_SOURCE_UNSAFE",
                            "the template source contains a symlink or "
                            f"junction at {relative!r}")
                dirs[:] = sorted(d for d in dirs
                                 if d not in COPY_EXCLUDED_NAMES)
                for name in sorted(files):
                    if name in COPY_EXCLUDED_NAMES \
                            or name.endswith(COPY_EXCLUDED_SUFFIXES):
                        continue
                    source_file = base_path / name
                    if is_reparse(source_file):
                        relative = source_file \
                            .relative_to(source_root).as_posix()
                        raise CreateError(
                            "CREATE_SOURCE_UNSAFE",
                            "the template source contains a symlink or "
                            f"junction at {relative!r}")
                    rel = source_file.relative_to(source_root).as_posix()
                    total_bytes += _copy_file(source_file,
                                              destination_temp / rel)
                    total_files += 1

    try:
        for name in RELEASE_FILES:
            copy_entry(name)
        for name in RELEASE_DIRECTORIES:
            copy_entry(name)
        for name in CONTROL_SEED_FILES:
            copy_entry(str(Path("control") / name))
        for name in HANDOFF_SEED_FILES:
            copy_entry(str(Path("handoff") / name))
    except Exception:
        import shutil
        shutil.rmtree(destination_temp, ignore_errors=True)
        raise
    return {"files": total_files, "bytes": total_bytes}


def verify_copy(destination_temp: Path) -> dict:
    """The copied tree must contain every required release path; the
    marker file's SHA-256 becomes the rollback proof."""
    destination_temp = Path(destination_temp)
    for rel in REQUIRED_RELEASE_PATHS:
        if not (destination_temp / rel).is_file():
            raise CreateError("CREATE_VALIDATION_FAILED",
                              f"the copied skeleton is incomplete; "
                              f"missing {rel!r}")
    marker = destination_temp / MARKER_RELATIVE
    digest = hashlib.sha256()
    with marker.open("rb") as handle:
        for chunk in iter(lambda: handle.read(65536), b""):
            digest.update(chunk)
    return {"files": len(REQUIRED_RELEASE_PATHS),
            "marker_rel": MARKER_RELATIVE,
            "marker_sha256": digest.hexdigest()}


def rollback_remove(path: Path, marker_rel: str,
                    marker_sha256: str) -> dict:
    """Remove a tree this operation created — but only while its marker
    file still matches the copied bytes. A mismatch is reported and the
    tree is left alone."""
    path = Path(path)
    marker = path / marker_rel
    try:
        digest = hashlib.sha256()
        with marker.open("rb") as handle:
            for chunk in iter(lambda: handle.read(65536), b""):
                digest.update(chunk)
        matches = digest.hexdigest() == marker_sha256
    except OSError:
        matches = False
    if not matches:
        return {"removed": False,
                "skipped_reason": "the created tree no longer matches the "
                                  "recorded copy marker; refusing to "
                                  "delete it automatically"}
    import shutil
    shutil.rmtree(path, ignore_errors=True)
    if path.exists():
        return {"removed": False,
                "skipped_reason": "the created tree could not be removed"}
    return {"removed": True, "skipped_reason": None}
