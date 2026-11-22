# AGENTS.md — Abyss v2 Coding Agent (`deepagents` + DDD + Astryx)

> Active repository root: `/home/abyss/projects/coding_agent` (branch: `main`)

## 1. Environment & Primary Commands (WSL2 Ubuntu)

- **Python Virtualenv**: `.venv` (Python 3.12, `deepagents 0.7.41`, `fastapi 0.165.0`, `langgraph 1.2.24`, `langchain-openai 1.8.5`, `langgraph-checkpoint-sqlite`).
- **WSL Linux Node PATH**: Default non-interactive shells omit `~/.local/bin`, causing `npm`/`npx`/`astryx` to hit Windows shims. Always export:
  ```bash
  export PATH="$HOME/.local/bin:$PATH"
  ```
- **Core Verification Commands**:
  ```bash
  # Backend unit, integration & DDD boundary suite (100% offline deterministic)
  .venv/bin/pytest -q

  # Backend lint
  .venv/bin/ruff check backend tests

  # Frontend typecheck, tests & production build
  export PATH="$HOME/.local/bin:$PATH" && cd frontend && npm run typecheck && npm test -- --run && npm run build

  # Run unified FastAPI server (serves API + built frontend via app.frontend())
  .venv/bin/fastapi run backend/app/main.py --port 8000
  ```

## 2. Non-Negotiable DDD AST Boundaries (`tests/test_architecture_boundaries.py`)

Three Bounded Contexts (`sessions`, `agent`, `artifacts`) plus `shared` — see `GLOSSARY.md` for Ubiquitous Language:

1. `backend/app/domains/agent/shell_guard.py` and `backend/app/domains/artifacts/walkthrough_diff.py`: **Python stdlib ONLY**.
2. `backend/app/domains/sessions/entities.py` and `backend/app/domains/artifacts/entities.py`: **Python stdlib + `pydantic` ONLY**.
3. `backend/app/domains/agent/middleware/*`: ZERO imports from `domains.sessions` or `fastapi`.
4. `backend/app/domains/agent/*` (except `router.py`): ZERO imports from `domains.sessions` or `fastapi`.
5. `backend/app/domains/artifacts/*` (except `router.py`): ZERO imports from `domains.sessions`, `domains.agent`, or `fastapi`.
6. `backend/app/domains/*/router.py`: Thin HTTP adapters ONLY — ZERO direct `sqlite3`, `aiosqlite`, `deepagents`, `langgraph`, or `subprocess` imports.
7. ZERO test doubles or mock LLMs anywhere inside `backend/app/` (keep fakes strictly under `tests/fakes/`).

## 3. Coding & Runtime Conventions (`open-swe` + FastAPI 0.165)

- **Async-only I/O** on the backend; pass **both** `http_client=httpx.Client(trust_env=False)` and `http_async_client=httpx.AsyncClient(trust_env=False)` plus `max_retries=0` on `ReasoningChatOpenAI`.
- **Strong typing**: Avoid `Any`/`any`; use `Protocol`, `Union`, generics, or `object`/`unknown` with narrowing.
- **State-owning methods**: Attach behavior to classes that own state (`ArtifactStore`, `WorkspaceInspector`, `ShellGuard`, `SQLiteSessionRepository`, `DeepAgentHarness`).
- **Never swallow exceptions**: Re-raise `(GraphBubbleUp, NodeCancelledError, asyncio.CancelledError)` inside tool wrappers so LangGraph `interrupt()` and stream cancellation work cleanly; convert recoverable tool errors into structured `ToolMessage`s.
- **Terminal `after_agent` rule**: `ArtifactWorkflowMiddleware.after_agent` MUST return `None` (never `{"jump_to": "end"}`) to avoid `GraphRecursionError`.
- **DeepAgents Sandbox Permission rule**: Pass `permissions=None` to `create_deep_agent` whenever using a `SandboxBackendProtocol` backend (`LocalShellBackend`), enforcing rules via `ShellGuard` + middleware; reserve `FilesystemPermission` for read-only `Ask` mode (`FilesystemBackend`).
- **Web UI-Only LLM Credentials Security**: Read `X-LLM-Base-Url`, `X-LLM-Model`, `X-LLM-Api-Key`, `X-LLM-Enable-Thinking` per request into `LLMCredentials`. **NEVER** log plaintext keys, store them in SQLite/checkpoints/LRU keys (SHA-256 prefix only), or return them in HTTP/SSE responses. Missing credentials fail fast with HTTP `428 LLM_SETTINGS_REQUIRED`.

## 4. Frontend Skill Precedence Stack (Astryx-First)

1. **`astryx` (Highest)**: Start every view from Astryx CLI templates (`shell-side-nav`, `ai-chat`, `settings-dialog`, `documentation-technical`, `file-explorer`). Strict CSS import order: `reset.css` -> `astryx.css` -> `theme.css` inside `<Theme>`. **Zero raw `<div>`/`<span>`, zero custom `.css` imports, zero hardcoded `#hex`/`px`.**
2. **`vercel-composition-patterns`**: Compound components + React context state sharing; zero boolean-prop explosion.
3. **`modern-web-guidance`**: Query `npx -y modern-web-guidance@latest search/retrieve` before coding SSE `fetch` streaming, ARIA dialogs/tabs, scroll anchoring, or sanitized Markdown rendering.
4. **`web-design-guidelines` + `distill` + `critique`**: Post-build visual de-noising and accessibility audits.
5. **`frontend-design` (Scoped)**: Microcopy, conciseness, and visual restraint only — never overrides Astryx tokens/CSS rules.
