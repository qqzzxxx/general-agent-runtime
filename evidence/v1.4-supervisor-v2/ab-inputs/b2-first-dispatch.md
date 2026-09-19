MESSAGE_ID: 700100
TASK_ID: task-7e96e8add993d57bb172aa11
STAGE_ID: stage-91d02fc35175d70aafa6656e

```json
{
  "ACCEPTANCE_CRITERIA": [
    "All information, controls, links, labels, states, and intended actions present in the supplied console remain available and semantically intact unless a change is strictly presentational.",
    "The interface has a cohesive mature AI/developer-product visual system with clear hierarchy, readable typography, deliberate spacing, consistent components, restrained color, and polished interaction states.",
    "The page is responsive and usable at representative desktop and mobile widths, with no unintended horizontal overflow, clipped essential content, overlapping elements, or inaccessible controls.",
    "A baseline desktop screenshot and final desktop and mobile screenshots are captured from the rendered page and demonstrate a material, coherent improvement rather than isolated cosmetic tweaks.",
    "Interactive elements and any existing behavior are exercised after the changes; verification.md records the checks, viewport sizes, results, compatibility notes, and any genuine limitations.",
    "HTML and CSS are valid enough to render without console-breaking errors, and the implementation avoids unrelated features, external service dependencies, and changes outside the scoped console files and evidence artifacts.",
    "The Executor performs a final visual inspection and resolves obvious contrast, alignment, density, truncation, focus-state, and responsive-layout defects before completion."
  ],
  "ATTEMPT": 1,
  "CLAIM_PROTOCOL_VERSION": 1,
  "EXECUTION": {
    "autonomy": "HIGH",
    "capabilities": {
      "browser": "host",
      "filesystem": "workspace",
      "gui": "host",
      "network": "none",
      "other": "host",
      "shell": "host",
      "vision": "host"
    },
    "read_paths": [
      "index.html",
      "styles.css"
    ]
  },
  "EXECUTOR_MODEL_FAMILY": "GLM-5.3",
  "EXECUTOR_PROTOCOL": [
    "Work only inside the active Runtime Root and the active Project Root supplied by the Supervisor.",
    "Before any stage work, run: python scripts/executor_claim.py acquire --message-id <MESSAGE_ID> --task-id <TASK_ID> --stage-id <STAGE_ID> --attempt <ATTEMPT> --nonce <NONCE>.",
    "Proceed only on CLAIM_ACQUIRED / exit code 0. On CLAIM_EXISTS / exit code 10 or ALREADY_PROCESSED / exit code 11, stop this run immediately and silently: do not touch deliverables, build completion staging, call the completion helper, or create any root completion artifact.",
    "On any other claim-helper failure, fail closed and stop without touching stage outputs; watchdog/Supervisor owns recovery.",
    "Never delete the claim. A retry must use a fresh MESSAGE_ID and NONCE.",
    "Complete the whole stage internally before returning to the Supervisor.",
    "Retain the claim_token returned only to the successful claim owner. It is required by fencing and completion helpers; never put it in deliverables or receipts.",
    "Run python scripts/executor_fence.py prepare with the original five identity arguments and --claim-token <token>. All stage writes and subprocess output belong in its returned attempt_workspace; canonical files are read-only inputs.",
    "Run python scripts/executor_fence.py check with the original identity and token after claim, on every resume, before each work batch or mutation-capable command, and before publication/completion. Only ATTEMPT_AUTHORIZED / exit 0 permits candidate work; otherwise stop. Never switch to a newer inbox identity.",
    "Publish candidates only with python scripts/executor_fence.py publish using the original identity/token, --path <project-relative path> and --sha256 <candidate hash>. Supported outputs are workspace/, evidence/, reports/ files (excluding reports/USER_STATUS.md), at most 64 MiB each. Only PUBLICATION_COMMITTED / exit 0 means canonical publication; otherwise stop.",
    "Never directly write canonical outputs, RESEARCH_STATE.md, or publication records. Suggest memory updates in the receipt. See docs/STALE_WORKER_FENCING.md.",
    "Build a completion staging directory under the active Project Root's completion_staging\\ folder: one staging.json (COMPLETION_STAGING_SCHEMA_VERSION 1) containing the exact MESSAGE_ID, TASK_ID, STAGE_ID, ATTEMPT, NONCE, PROJECT_ID, STATUS=STAGING_READY, a timezone-aware CREATED_AT, and the full receipt payload as RECEIPT (identity fields must match the staging identity exactly).",
    "Then run: python scripts/executor_completion.py commit --staging-dir \"<staging dir>\" --claim-token \"<token>\".",
    "Proceed only on COMPLETION_COMMITTED / exit code 0: the Runtime then generates SUPERVISOR_BRIEF.md, ZCODE_LAST_PROCESSED.txt, and ZCODE_DONE.flag itself. Stop immediately; never create, edit, repair, or republish those root files.",
    "On completion-helper exit codes 10/11/12/13/14 (ALREADY_COMMITTED / COMPLETION_SEALED / COMPLETION_NOT_AUTHORIZED / COMPLETION_CLAIM_MISMATCH / INVALID_COMPLETION_STAGING), fail closed and stop without publishing anything.",
    "Stop after the stage; never create the next TO_ZCODE task."
  ],
  "FORBIDDEN_ACTIONS": [
    "Do not bypass authentication, CAPTCHA, access control, or anti-automation restrictions.",
    "Do not invent missing data or credentials.",
    "Do not choose the next stage.",
    "Do not remove, rename, disable, or invent product actions or information merely to simplify the layout.",
    "Do not edit PROJECT_GOAL.md, project_state.json, RESEARCH_STATE.md, handoff files, Runtime control files, or anything outside the authorized project outputs.",
    "Do not add network-loaded fonts, frameworks, analytics, trackers, or other external runtime dependencies.",
    "Do not expand the task into backend work or unrelated refactoring."
  ],
  "INPUTS": [
    "index.html",
    "styles.css"
  ],
  "ISSUED_AT": "2026-09-14T09:20:08+00:00",
  "LOGICAL_STAGE": "inspect_implement_verify",
  "LOGICAL_TASK": "console_product_polish",
  "MAX_RETRIES": 2,
  "MAX_TIME": 2700,
  "MESSAGE_ID": 700100,
  "NONCE": "df6a6ae6995d44a6384bc0075d4efbb06f69dcbcc5070f0e",
  "OBJECTIVE": "Inspect the supplied AI Agent Project Console, redesign and refine it into a mature, clear, responsive AI/developer product interface, preserve all existing information and intended actions, and verify the final rendered experience before reporting completion.",
  "OUTPUTS": [
    "index.html",
    "styles.css",
    "evidence/console-product-polish/baseline-desktop.png",
    "evidence/console-product-polish/final-desktop.png",
    "evidence/console-product-polish/final-mobile.png",
    "evidence/console-product-polish/verification.md"
  ],
  "PROTOCOL_VERSION": 2,
  "SCHEDULER_GRACE_SECONDS": 3600,
  "STAGE_ID": "stage-91d02fc35175d70aafa6656e",
  "STOP_CONDITIONS": [
    "Stop and report BLOCKED if either supplied source file is missing or cannot be rendered and the problem cannot be resolved without changing Runtime authority files.",
    "Stop and report HUMAN_REVIEW if preserving an existing action conflicts materially with the requested product experience and no safe presentation-only resolution is possible."
  ],
  "SUPERVISOR_REVIEW_EFFORT": "high",
  "TASK_ID": "task-7e96e8add993d57bb172aa11"
}
```
