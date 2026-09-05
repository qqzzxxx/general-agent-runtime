# Projects

Everything under `projects/` is **user-owned runtime data and is gitignored** — project
goals, code, evidence, reports, and logs never belong in a public repository.

## Creating a project

Do not create project directories by hand. Use the mechanical bootstrap, which
validates the id, builds the directory in a staging area, verifies it, renames it
atomically, and activates it last:

```powershell
.\START_PROJECT.ps1 -ProjectId my-project-001 -ProjectType GENERAL -Goal "Your goal text"
```

Profiles available as `-ProjectType`: `GENERAL`, `SOFTWARE_ENGINEERING`,
`ACADEMIC_RESEARCH`, `BUSINESS_RESEARCH` (see `profiles/<TYPE>/`).

## Layout of an activated project

```
projects/<id>/
  PROJECT_GOAL.md         goal + completion criteria (bound into project state)
  project_state.json      lifecycle state owned by the Orchestrator
  RESEARCH_STATE.md       compressed long-term project memory
  workspace/              executor scratch space
  evidence/               immutable run evidence produced by stages
  reports/                deliverable reports (final report discovered here)
```

## Example (illustrative only)

A `GENERAL` profile project for a market scan might define a goal such as: *"Compare
publicly available note-taking tools on offline capability and pricing; produce a
decision table with sources."* The Supervisor would then dispatch stages like
`inventory`, `deep-dive`, `verification`, and `final-report`, with every stage's
evidence landing in this directory. No example data ships with the runtime.
