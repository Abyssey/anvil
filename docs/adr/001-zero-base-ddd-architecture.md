# ADR 001: Zero-Base DDD Coding Agent (`deepagents` + Open SWE Patterns + Astryx)

- **Status**: Accepted (2026-11-22)
- **Repository**: `/home/abyss/projects/coding_agent` (`main`)

## Context

The previous codebase carried ~18.9k LOC of legacy control-plane routes, multi-tab UI clutter, and outdated patterns. Maintaining or refactoring around legacy route contracts consumed more effort and context than building a clean, focused coding agent from scratch.

We rebuilt on a reset `main` branch targeting:
1. Claude Code's distraction-free 2-pane streaming UX (`Ask` / `Plan` / `Auto` modes).
2. Antigravity's structured artifacts (`implementation_plan.md`, `task.md`, `walkthrough.md`) with human-in-the-loop (HITL) approval gates.
3. Latest `langchain-ai/deepagents` (`0.7.41`) and `langchain-ai/open-swe` backend engineering patterns on self-hosted local execution.
4. Standard OpenAI-compatible LLM integration (default `qwen3.8-flash`) configured strictly in browser UI Settings.

## Decisions

### 1. Strict DDD Bounded Contexts & AST Enforcement
Three bounded contexts plus a minimal `shared` layer follow a strictly unidirectional dependency arrow (`sessions` → `agent` → `artifacts` → `shared`), verified by `tests/test_architecture_boundaries.py`:
- **`sessions`**: Session lifecycle, SQLite transcript persistence (`aiosqlite`), and FastAPI 0.165 native `EventSourceResponse` SSE streaming.
- **`agent`**: `DeepAgentHarness`, `ReasoningChatOpenAI`, `RunContext`/`RunCatalog`, `SkillFiles`, Open SWE middlewares, `DynamicRunBackend`/`DynamicGuardedShellBackend`, `ShellGuard` (pure stdlib), `explorer`/`reviewer` subagents, and `WorkspaceInspector`.
- **`artifacts`**: Isolated `/artifacts/` `CompositeBackend` storage (`ArtifactStore`), pure-stdlib `walkthrough_diff.py` diff hunk parser/planner, and `ArtifactWorkflowMiddleware` (receives shell/context callbacks via DI without importing `domains.agent`).

### 2. Self-Hosted `deepagents` + Open SWE Deep Backend Patterns
- **`ReasoningChatOpenAI`**: Subclasses `ChatOpenAI` because `langchain-openai 1.8.5` strips non-standard `reasoning_content`; sets `max_retries=0` (owned by `ModelRetryMiddleware`) and both `http_client` & `http_async_client(trust_env=False)`.
- **`RunContext` + Bounded LRU Graph Cache**: Keyed by `(creds.cache_fingerprint(), mode)` — never plaintext API keys — while `RunCatalog` injects per-run workspace, artifacts, skills, and `GitEvidenceTracker` via `ContextVar`.
- **Mode-Scoped Safety**: `Ask` uses read-only `FilesystemBackend` + `FilesystemPermission` (no `execute` tool); `Plan`/`Auto` use `DynamicGuardedShellBackend` (`permissions=None` per `deepagents 0.7.41` `SandboxBackendProtocol` rules) with 2-tier `ShellGuard` + LangGraph `interrupt()` HITL gates.
- **Read-Only Subagents**: `explorer` (all modes) and `reviewer` (`plan`/`auto`) run with `ReadOnlyToolGuardMiddleware` so parallel exploration and diff review never collide with HITL prompts.

### 3. Web UI-Only Credential Security Model
- Credentials (`X-LLM-Base-Url`, `X-LLM-Model`, `X-LLM-Api-Key`, `X-LLM-Enable-Thinking`) live in browser `localStorage` and travel per request in headers.
- `LLMCredentials` masks `api_key` in `__repr__`/`__str__` and hashes it into `cache_fingerprint()`. Keys are never written to SQLite checkpoints, server logs, or SSE responses. Missing credentials fail fast with HTTP `428 LLM_SETTINGS_REQUIRED`.

### 4. Astryx-First Frontend Stack
- Built on React 19, TypeScript, Vite, and `@astryxdesign/core@0.7.17` (`neutral` theme) with zero raw `<div>`/`<span>`, zero custom `.css` files, and zero hardcoded `#hex`/`px` literals.
- Guided by `modern-web-guidance` for native `<dialog>` accessibility, XSS-safe Markdown sanitization, and content-first progressive disclosure (right drawer collapsed by default).

## Consequences
- Every architectural boundary, security invariant, HITL transition, and SQLite restart recovery is verified offline via deterministic tests (`ScriptedReasoningChatModel` under `tests/fakes/`).
- The system supports live OpenAI-compatible models (DashScope, vLLM, SGLang, OpenAI) immediately from the Web UI without restarting the server or editing `.env`.
