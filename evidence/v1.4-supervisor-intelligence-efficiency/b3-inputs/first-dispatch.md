MESSAGE_ID: 700100
TASK_ID: task-024d9ef3144205feb45eedef
STAGE_ID: stage-2666beda1b1240d0f5b2d0ee

```json
{
  "ACCEPTANCE_CRITERIA": [
    "The rendered console presents an intentional, cohesive visual system and clear operational hierarchy that feels like a mature AI/developer product rather than a raw engineering dashboard.",
    "All supplied facts, records, labels, status details, progress data, activity entries, artifacts, token data, alerts, explanatory fixture context, and intended actions remain meaningfully available; regrouping or progressive disclosure is allowed when it improves usability.",
    "Pause and resume still update system and project status, control enabled states, and activity; reviewing one alert and reviewing all alerts still acknowledge the correct pending items and provide visible feedback without errors.",
    "The interface remains legible and usable at representative desktop and narrow viewport sizes without destructive clipping or inaccessible controls, with sensible keyboard focus, contrast, semantics, and reduced-motion consideration.",
    "Visible copy is professional and free of the current encoding artifact while retaining its meaning; severity, state, progress, pending-human-action, and primary controls can be understood quickly.",
    "Evidence includes rendered screenshots at desktop and narrow widths, interaction-check results, a content-preservation comparison, and a concise self-review describing at least one inspection-driven refinement or explicitly stating why none was needed.",
    "The final files load locally without backend or network dependencies and without browser console errors during the exercised flows."
  ],
  "ATTEMPT": 1,
  "CLAIM_PROTOCOL_VERSION": 1,
  "EXECUTION": {
    "autonomy": "HIGH",
    "capabilities": {
      "browser": "host",
      "filesystem": "workspace",
      "gui": "host",
      "network": "host",
      "other": "host",
      "shell": "host",
      "vision": "host"
    },
    "read_paths": [
      "index.html",
      "styles.css",
      "PROJECT_GOAL.md",
      "evidence",
      "reports"
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
    "Do not add a backend, network requests, external runtime dependencies, or claims that the illustrative fixture data is live.",
    "Do not remove supplied information or disable, replace, or misrepresent the intended actions.",
    "Do not modify PROJECT_GOAL.md, project_state.json, RESEARCH_STATE.md, Runtime control files, or authority records."
  ],
  "INPUTS": [
    "index.html",
    "styles.css",
    "PROJECT_GOAL.md"
  ],
  "ISSUED_AT": "2026-09-14T12:39:06+00:00",
  "LOGICAL_STAGE": "implement and inspect experience",
  "LOGICAL_TASK": "console product redesign",
  "MAX_RETRIES": 2,
  "MAX_TIME": 2700,
  "MESSAGE_ID": 700100,
  "NONCE": "c42f09536501d9f6f3250f8fd7996b6f2535a769fe4aa8c0",
  "OBJECTIVE": "Transform the supplied static AI Agent Project Console into a polished, clear, and usable mature developer-product interface. Take ownership of information architecture, hierarchy, responsive behavior, interaction clarity, and visual quality; preserve all supplied information and the intended pause, resume, review-one, and review-all actions. Render and inspect the result, exercise the interactions, and iterate on material weaknesses before reporting completion.",
  "OUTCOME_CONTEXT": {
    "current_facts": [
      "The current page is a very dense sequence of basic HTML tables with minimal spacing, weak visual prioritization, and plain system-default controls.",
      "All behavior is local inline JavaScript: pause/resume changes labels and disabled states, and review actions acknowledge alerts and append activity.",
      "The page declares itself a static local fixture with illustrative data and no backend or network requests.",
      "The Alerts heading currently contains a visible mojibake encoding artifact."
    ],
    "desired_outcome": "An operator can understand the project's state, priorities, progress, resource use, and required interventions at a glance, then confidently use the local controls in a polished console.",
    "hard_constraints": [
      {
        "constraint": "Preserve the existing information and intended actions.",
        "source": "PROJECT_GOAL.md Objective and Deliverable"
      },
      {
        "constraint": "The deliverable is the improved supplied console based on index.html and styles.css.",
        "source": "PROJECT_GOAL.md Inputs and Deliverable"
      },
      {
        "constraint": "Operate only inside the active Runtime Root and project scope, and do not alter Runtime-owned authority or the Goal Anchor.",
        "source": "Supervisor Runtime Contract and Goal Integrity block"
      }
    ],
    "quality_bar": [
      "Strong hierarchy and scanability across dense operational data.",
      "Cohesive, restrained visual design with production-quality details and states.",
      "Responsive, accessible interaction behavior with clear feedback.",
      "Visual inspection and evidence-backed iteration, not source-only confidence."
    ],
    "revisable_assumptions": [
      "The current section order, table-heavy information architecture, labels, wording, and DOM structure may be changed when meaning and actions remain available.",
      "A single long desktop-oriented layout is not required; responsive regrouping or progressive disclosure may better serve the outcome.",
      "The existing pale gray visual treatment is implementation history, not a product constraint."
    ]
  },
  "OUTPUTS": [
    "index.html",
    "styles.css",
    "evidence/console-redesign/"
  ],
  "PROTOCOL_VERSION": 2,
  "SCHEDULER_GRACE_SECONDS": 3600,
  "STAGE_ID": "stage-2666beda1b1240d0f5b2d0ee",
  "STOP_CONDITIONS": [
    "Stop and report if either supplied implementation input is unreadable or if the page cannot be rendered locally with available tools."
  ],
  "SUPERVISOR_REVIEW_EFFORT": "high",
  "TASK_ID": "task-024d9ef3144205feb45eedef"
}
```
