# GLOSSARY.md — Abyss v2 Ubiquitous Language

| Term | Bounded Context | Definition |
| :--- | :--- | :--- |
| **Session** | `sessions` | Durable conversation thread bound to a target local `Workspace` path, `RunMode`, message history, and SQLite checkpoint ID (`thread_id`). |
| **Workspace** | `agent` | Local filesystem directory selected by the user in the UI sidebar as the repository root for agentic search, diff inspection, and code edits. |
| **RunMode** | `sessions` / `agent` | Execution policy toggled in the chat composer: **`ask`** (read-only exploration, shell disabled, instant `walkthrough`), **`plan`** (writes `implementation_plan`, pauses at `plan_review` interrupt, gates shell calls), or **`auto`** (executes immediately while still gating destructive shell commands and recording artifacts). |
| **RunCatalog** | `agent` | Immutable per-run configuration snapshot (`session_id`, `workspace_path`, `mode`, `artifacts_dir`, `ShellGuard`, `GitEvidenceTracker`, extra skills dirs) resolved dynamically via `RunContext` so one cached DeepAgents graph serves multiple runs safely. |
| **RunContext** | `agent` | Async `contextvars.ContextVar` binding the active `RunCatalog` for the duration of a single `stream_run` or `stream_resume` execution. |
| **LLMCredentials** | `shared` | Ephemeral per-request OpenAI-compatible credentials (`base_url`, `model`, `api_key`, `enable_thinking`) supplied solely from browser UI Settings via `X-LLM-*` headers; masked in `__repr__`/`__str__` and never persisted on the server. |
| **ShellGuard** | `agent` | Pure-stdlib 2-tier command evaluator (`ALLOW`, `REQUIRE_APPROVAL`, `BLOCK`) enforcing the hard deny-list, destructive command detection per `RunMode`, and secret output scrubbing. |
| **SkillFiles** | `agent` | Read-only virtual `BackendProtocol` aggregating bundled skills (`backend/bundled_skills/`) and target `Workspace` `.agents/skills/` / `.claude/skills/` per active run without cross-session leakage. |
| **GitEvidence** | `agent` | Pre-turn and post-mutation Git snapshot tracker (`branch`, `head`, `dirty` files) used to emit `diff_changed` events and link runs to verified workspace changes. |
| **Artifact** | `artifacts` | Structured Antigravity markdown document stored in `.abyss-data/artifacts/{session_id}/` (mounted at `/artifacts/` inside the agent): `implementation_plan`, `task`, and `walkthrough`. |
| **WalkthroughChunk** | `artifacts` | Logical change unit (`title`, `explanation`, `file_hunks`) indexing git diff hunks (`+12`, `-40`) across files so code-change walkthroughs jump directly into the Diff Viewer. |
| **Interrupt** | `artifacts` / `sessions` | LangGraph HITL pause point (`plan_review` before workspace mutation in `plan` mode, or `shell_approval` before guarded shell execution) resumed via `POST /api/sessions/{id}/resume`. |
