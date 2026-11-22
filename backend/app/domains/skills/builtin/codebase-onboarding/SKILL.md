---
name: codebase-onboarding
description: Quickly map and explain a codebase's architecture, modules, entrypoints, and dependencies using parallel agentic search without manual context selection.
---

# Codebase Onboarding Skill

Use when asked to explain, map, or onboard someone onto a repository quickly.

## Workflow

1. **Map structure first**: Call `ls("/")` and `glob` (`**/*` or targeted globs like `pyproject.toml`, `package.json`, `**/*.py`, `**/*.ts`) in parallel.
2. **Delegate deep dives when helpful**: Spawn `explorer` subagents to inspect distinct subsystems (e.g. backend vs frontend vs tests) simultaneously.
3. **Read entrypoints and manifests**: Inspect `README.md`, `AGENTS.md`, `pyproject.toml`, `package.json`, and primary app entrypoints before drawing conclusions.
4. **Synthesize concisely**:
   - Purpose and tech stack
   - High-level directory layout and module responsibilities
   - Core execution/data flow and external dependencies
   - How to run tests, lint, and start the app locally
   - Cite exact observed file paths and symbol names only.
   - Write the final onboarding summary and `/artifacts/walkthrough.md` in Korean (한국어) by default unless the user explicitly asks for another language.
