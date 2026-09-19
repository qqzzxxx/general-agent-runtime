# v1.4 Supervisor configuration UI — Codex CLI capability evidence

Date: 2026-09-18 · Machine: this Windows workstation · Probe: real installed CLI

## Method

The canonical model ids and reasoning-effort vocabulary were confirmed from the
actually installed Codex CLI, not from documentation or memory:

```
codex --version          -> codex-cli 0.154.0
codex debug models       -> raw model catalog as JSON (see
                            codex-models-catalog-extract.json)
~/.codex/config.toml     -> model = "gpt-6-astra",
                            model_reasoning_effort = "xhigh"
                            (desktop: enabled-reasoning-efforts includes
                            low/medium/high/xhigh/ultra/max)
```

## Confirmed facts

| Fact | Value |
| --- | --- |
| CLI version | codex-cli 0.154.0 |
| GPT-5.6 Sol canonical model id | `gpt-5.6-sol` (display "GPT-5.6-Sol", visibility list) |
| GPT-6 Astra canonical model id | `gpt-6-astra` (display "GPT-6-Astra", visibility list) |
| `gpt-5.6-sol` supported reasoning efforts | low, medium, high, **xhigh**, max, ultra |
| `gpt-6-astra` supported reasoning efforts | low, medium, high, **xhigh**, max, ultra |
| `medium` / `high` / `xhigh` legal for both models | yes |

Other listable models in the catalog (`gpt-5.6-terra`, `gpt-5.6-luna`,
`gpt-5.5`, hidden `gpt-reserve`, `codex-auto-review`) are deliberately **not**
exposed by the Runtime UI.

## Conflict check against the target design

None. The target design (UI: GPT-5.6 Sol / GPT-6 Astra; efforts 中/高/极高 →
`medium` / `high` / `xhigh`) maps exactly onto values the installed CLI
accepts. No canonical value had to be substituted, and no model or effort was
silently replaced.

## Mapping implemented

```
UI label           -> payload        -> Runtime vocabulary   -> Codex CLI
GPT-5.6 Sol        -> gpt-5.6-sol    -> gpt-5.6-sol          -> -m gpt-5.6-sol
GPT-6 Astra        -> gpt-6-astra    -> gpt-6-astra          -> -m gpt-6-astra
中                 -> medium         -> MEDIUM (stored)      -> model_reasoning_effort=medium
高                 -> high           -> HIGH (stored)        -> model_reasoning_effort=high
极高               -> xhigh          -> XHIGH (stored)       -> model_reasoning_effort=xhigh
```

The Runtime stores the uppercase vocabulary it has always stored
(`SUPERVISOR_CONFIG_EFFORTS`, extended with `XHIGH` in this change) and
normalizes incoming values case-insensitively, so pre-existing configuration
files keep loading unchanged. The orchestrator already lowercases the effort
when invoking Codex (`supervisor_profile_for_turn`), matching the CLI's
canonical lowercase form.
