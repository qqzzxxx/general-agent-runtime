# Release Build Workflow (repeatable)

Purpose: build a clean, publishable Runtime release from this repository
(main) with no dependence on developer-residue files, machine state, or
memory. Every step is a command; the result is verifiable.

## Inputs

- This repository at a recorded commit (the release HEAD). Record
  `git rev-parse HEAD` in the release notes.
- A clean working tree (`git status` reports nothing to commit).

## Steps

1. **Clean export.** `git clone <this repository> <staging-dir>` and record
   the cloned commit hash. A clone contains exactly the tracked tree — this
   is the release content. Do not copy working-tree extras by hand.

2. **Skeleton integrity gate.** In the staging clone run:

   ```
   python -m unittest discover -s scripts -p "test_release_skeleton_integrity.py"
   ```

   This proves every Runtime-create skeleton allowlist entry (including the
   control seeds) exists and is tracked, so Runtime-create and fresh-install
   readiness work from this tree (F-001).

3. **Full regression gate.** In the staging clone run:

   ```
   python -m unittest discover -s scripts -p "test_*.py"
   ```

   The release ships only if this is green. Record the counts.

4. **Smoke gate.** In the staging clone run
   `python -m unittest discover -s scripts -p "test_clean_release_startup.py"`.
   This starts a project from a non-Git clean copy through the public
   launcher and proves no developer-residue dependency.

5. **Package.** The release artifact is the staging clone contents minus the
   `.git` directory. Hygiene checklist — the package must NOT contain:
   user projects (`projects/<id>/` beyond the README), live Runtime state
   (`control/ACTIVE_PROJECT.json`, `control/HUMAN_REVIEW`, `control/STOP`,
   `control/orchestrator_runtime.json`, `TO_ZCODE.md`, `SUPERVISOR_BRIEF.md`,
   `ZCODE_DONE.flag`, `ZCODE_LAST_PROCESSED.txt`), logs, caches
   (`__pycache__`, `*.pyc`), `web_console_data/`, or any hardening/dogfood
   artifacts. Verify with a fresh-clone diff and `git status` cleanliness.

6. **Acceptance.** Repeat steps 1–5 a second time from the same commit; the
   two staged trees must be structurally identical (timestamps aside).

## Notes

- `control/HUMAN_DECISION_TEMPLATE.json` is a shipped seed (F-001): it is
  force-included in the tracked tree via a `.gitignore` negation and is
  pinned by the skeleton-integrity test.
- Runtime-created operational files (active-project pointer, runtime state,
  supervisor control store, inbox, ledgers) are build outputs of *running*
  the Runtime, never release content; the tests above keep them that way.
