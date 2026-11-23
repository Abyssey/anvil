---
name: verify
description: Evidence-driven verification workflow for code changes. Use before finishing any coding turn in Plan or Auto mode to run fast local checks, apply diff-triggered reliability checks, respect the Three-Strike rule, and record concrete verification evidence.
---

# Evidence-Driven Verification (`verify`)

Claims without runnable evidence are assertions, not verification. Before concluding any turn that modifies workspace files, follow this progressive verification loop:

## 1. Run Cheap, Deterministic Checks First
1. Discover the repository's native verification commands from `AGENTS.md`, `pyproject.toml`, `package.json`, `Cargo.toml`, `go.mod`, or `Makefile` rather than guessing.
2. Order checks from fastest/narrowest to broadest so failures pinpoint the exact defect:
   - **Stage 1 (Static & Boundary)**: linter, typechecker, and architecture boundary tests (e.g., `ruff check`, `tsc --noEmit`, `test_architecture_boundaries.py`).
   - **Stage 2 (Targeted Unit & Property Tests)**: unit and invariant/property tests covering modified modules and round-trip/idempotency rules.
   - **Stage 3 (Integration / Build / E2E Smoke)**: end-to-end user-flow tests or production build checks (`vite build`, API integration suite) when behavior crosses module or UI boundaries.

## 2. Diff-Triggered Reliability Checklist
Inspect only the items below whose trigger matches your actual diff:
- **Persistence / Entity Schema Changed** (`entities.py`, SQLite tables, API types): Does loading existing rows/transcripts without the new field succeed via safe defaults?
- **Filesystem / Path Handling Changed**: Are symlinks, `..` segments, and virtual prefixes (`/artifacts/`, `/memories/`, `/skills/`) strictly confined to their root?
- **Logging / Error Formatting / Shell Output Changed**: Could API keys, `Bearer` tokens, or credentials leak into logs, tracebacks, checkpoints, or tool outputs?
- **Streaming / LangGraph Tool Wrappers Changed**: Are `(GraphBubbleUp, NodeCancelledError, asyncio.CancelledError)` re-raised cleanly without being swallowed?
- **Frontend UI Changed**: Are component boundaries, keyboard focus, IME `Enter` safety, and theme tokens preserved without raw HTML/inline-style regressions?

## 3. Keep Verifiers Honest & Obey the Three-Strike Rule
- **Never weaken the check to pass the code**: Do not delete assertions, add arbitrary `sleep`s to mask race conditions, skip tests, or rewrite `AGENTS.md` / `REVIEW.md` / boundary tests to make a broken change appear green.
- **Three-Strike Rule**: You have up to **3 rounds** to diagnose and fix a failing check from its real error output. If verification still fails on strike 3, stop modifying code and present the exact command, failure output, and root-cause analysis to the user (or call `ask_user` if requirements conflict).

## 4. Stamp Evidence in `/artifacts/walkthrough.md`
Record a concise `## Verification` section in `/artifacts/walkthrough.md` listing:
- Exact commands executed and pass/fail counts (e.g., `Verification: passed — pytest -q (82 passed), ruff check (0 errors)`).
- Or, if verification could not be executed in the environment, explicitly write `Verification: none (<concrete reason>)`.
