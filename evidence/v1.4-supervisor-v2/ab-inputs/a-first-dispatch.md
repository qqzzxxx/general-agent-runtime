MESSAGE_ID: 700100
TASK_ID: console-ui-polish
STAGE_ID: implementation-1

```json
{
  "PROTOCOL_VERSION": 2,
  "CLAIM_PROTOCOL_VERSION": 1,
  "MESSAGE_ID": 700100,
  "TASK_ID": "console-ui-polish",
  "STAGE_ID": "implementation-1",
  "ATTEMPT": 1,
  "NONCE": "e681cf7dff8940da9698a24cc7449f023d769426f6a544fb-700100",
  "ISSUED_AT": "2026-09-13T23:10:34+00:00",
  "EXECUTOR_MODEL_FAMILY": "GLM-5.3",
  "TASK_KIND": "IMPLEMENTATION",
  "OBJECTIVE": "Inspect the supplied AI Agent Project Console, then improve index.html and styles.css into a polished, clear, responsive, accessible product interface while preserving every existing piece of information and intended action. Own the visual design, validate the rendered result, and correct material issues found before completion.",
  "INPUTS": [
    "index.html",
    "styles.css",
    "PROJECT_GOAL.md"
  ],
  "OUTPUTS": [
    "index.html",
    "styles.css",
    "evidence/ui-baseline-audit.md",
    "evidence/ui-validation.md",
    "evidence/ui-before.png",
    "evidence/ui-after-desktop.png",
    "evidence/ui-after-mobile.png"
  ],
  "ACCEPTANCE_CRITERIA": [
    "Before editing, inventory the baseline's visible information, controls, navigation, and intended actions in evidence/ui-baseline-audit.md and capture evidence/ui-before.png so preservation can be checked.",
    "Implement a cohesive, mature AI/developer-product visual system with clear hierarchy, typography, spacing, color, surfaces, status treatment, and control states; avoid a generic unstyled engineering-dashboard appearance.",
    "Preserve all supplied information, labels, controls, links, and intended actions. Do not invent product capabilities or remove content merely to simplify the layout.",
    "Keep the patch focused on index.html and styles.css. Preserve existing behavior and avoid JavaScript or dependency changes unless demonstrably necessary for an existing intended action.",
    "Validate the rendered page at representative desktop and mobile sizes, including approximately 1440px and 375px widths. Correct clipping, overlap, illegible text, unintended horizontal scrolling, and broken layout before completion.",
    "Provide evidence/ui-after-desktop.png and evidence/ui-after-mobile.png from the final rendered interface, and visually inspect both rather than treating successful rendering as sufficient.",
    "Check semantic structure, keyboard-visible focus, readable contrast, usable hit targets, and reduced-motion behavior where motion exists; record concrete results and any limits in evidence/ui-validation.md.",
    "Exercise every available control or link to the extent possible in the local artifact and verify that the redesign did not break its existing action. Record the exact validation commands, checks, and results.",
    "Record the exact diff summary, before-versus-after findings, rollback considerations, and any remaining uncertainty. Keep unrelated files and refactors untouched.",
    "Publish all listed outputs through executor_fence.py publish and include their relative paths and SHA-256 values in the completion receipt."
  ],
  "MAX_TIME": 2700,
  "MAX_RETRIES": 1,
  "SCHEDULER_GRACE_SECONDS": 3600,
  "SUPERVISOR_REVIEW_EFFORT": "high",
  "FORBIDDEN_ACTIONS": [
    "Do not edit PROJECT_GOAL.md, project_state.json, RESEARCH_STATE.md, TO_ZCODE.md, Runtime ledgers, claim directories, or Runtime-owned completion artifacts.",
    "Do not write directly to canonical outputs; perform stage work only in the prepared attempt workspace and publish through executor_fence.py.",
    "Do not remove, rename, or silently alter existing information or intended actions.",
    "Do not add frameworks, external dependencies, network-loaded assets, analytics, or unrelated functionality.",
    "Do not bypass authorization, access controls, CAPTCHA, claims, fencing, or completion checks.",
    "Do not select or dispatch a later stage and do not claim the project is finally accepted."
  ],
  "STOP_CONDITIONS": [
    "Stop immediately and silently if the claim helper returns CLAIM_EXISTS or ALREADY_PROCESSED.",
    "Fail closed and stop without touching stage outputs on any other claim-helper or fence failure.",
    "Stop and report BLOCKED if required source inputs are missing or unusable, or if faithful preservation would require an unresolved product decision.",
    "Stop after committing the authoritative completion or after any completion-helper rejection; do not repair Runtime-owned artifacts."
  ],
  "EXECUTOR_PROTOCOL": [
    "Read the task identity, then before any stage work run exactly: python scripts/executor_claim.py acquire --message-id 700100 --task-id \"console-ui-polish\" --stage-id \"implementation-1\" --attempt 1 --nonce \"e681cf7dff8940da9698a24cc7449f023d769426f6a544fb-700100\".",
    "If acquire exits 0 with CLAIM_ACQUIRED, retain the returned claim token and proceed; exit 10 CLAIM_EXISTS or 11 ALREADY_PROCESSED means stop this Scheduled Automation run immediately and silently without research, output changes, staging, completion calls, or root artifacts.",
    "On any other claim-helper failure, fail closed and stop without touching stage outputs; never delete a claim directory, reuse a claimed identity, or transfer its token.",
    "After acquisition, run executor_fence.py prepare with the exact identity and claim token. Treat canonical inputs as read-only and place every mutation and subprocess output in the prepared attempt workspace.",
    "Run executor_fence.py check with the exact identity and token on resume and before every work batch, mutation-capable command, publication, and completion action; a prior successful check is not a reusable write grant.",
    "Publish each canonical workspace, evidence, or report output only with executor_fence.py publish using the exact identity, token, relative path, and candidate SHA-256. Never write canonical outputs or project memory directly.",
    "Follow EXECUTOR-FENCE-V1 in docs/STALE_WORKER_FENCING.md, including supported paths and file-size limits. Never continue a retired or superseded identity.",
    "When work is finished, create a unique directory under projects/console-a-001/completion_staging containing staging.json with COMPLETION_STAGING_SCHEMA_VERSION 1, the exact stable identity, PROJECT_ID console-a-001, STATUS STAGING_READY, a timezone-aware CREATED_AT, and the full RECEIPT payload whose identity matches exactly.",
    "The RECEIPT must truthfully report actual runtime model, status, objective, 3-7 key findings, core metrics, conclusions and limits, problems and uncertainty, work performed, failed methods, recommended next action, evidence pointers, efficiency note, deliverables with hashes, acceptance self-check, and timestamps.",
    "Run python scripts/executor_completion.py commit --staging-dir \"<staging dir>\" --claim-token \"<claim token>\". Exit 0 COMPLETION_COMMITTED means stop immediately and never edit the generated root SUPERVISOR_BRIEF.md, ZCODE_LAST_PROCESSED.txt, or ZCODE_DONE.flag.",
    "If completion exits 10 ALREADY_COMMITTED, 11 COMPLETION_SEALED, 12 COMPLETION_NOT_AUTHORIZED, 13 COMPLETION_CLAIM_MISMATCH, or 14 INVALID_COMPLETION_STAGING, fail closed, stop, and publish nothing further."
  ]
}
```
