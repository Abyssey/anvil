# AGENTS.md — Anvil Coding Agent (`deepagents` + DDD + Astryx)

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

  # Frontend typecheck, tests, production build & Astryx/Impeccable audits
  export PATH="$HOME/.local/bin:$PATH" && cd frontend && npm run typecheck && npm test -- --run && npm run build && npx astryx doctor --built .
  /home/abyss/.gemini/config/skills/impeccable/scripts/impeccable detect --json --changed

  # Run unified FastAPI server (serves API + built frontend via app.frontend())
  .venv/bin/fastapi run backend/app/main.py --port 8000
  ```

## 2. Non-Negotiable DDD AST Boundaries (`tests/test_architecture_boundaries.py`)

Four Bounded Contexts (`sessions`, `agent`, `artifacts`, `skills`) plus `shared` — see `GLOSSARY.md` for Ubiquitous Language:

1. `backend/app/domains/agent/shell_guard.py`, `backend/app/domains/artifacts/walkthrough_diff.py`, and `backend/app/shared/logging.py`: **Python stdlib ONLY**.
2. `backend/app/domains/{sessions,artifacts,skills}/entities.py`: **Python stdlib + `pydantic` ONLY**.
3. `backend/app/shared/{errors,llm_credentials,config,logging}.py`: ZERO `fastapi`/`starlette` imports (keep HTTP adapters in `shared/http.py`).
4. `backend/app/domains/agent/*` (including `middleware/*`, except `router.py`): ZERO imports from `domains.sessions` or `fastapi`/`starlette`.
5. `backend/app/domains/artifacts/*` (except `router.py`): ZERO imports from `domains.sessions`, `domains.agent`, `fastapi`/`starlette`, `langchain*`, `langgraph`, or `deepagents`.
6. `backend/app/domains/skills/*` (except `router.py`): ZERO imports from `domains.{sessions,agent,artifacts}` or `fastapi`/`starlette`; built-in skills live strictly under `backend/app/domains/skills/builtin/<name>/SKILL.md`.
7. `backend/app/domains/*/router.py`: Thin HTTP adapters ONLY — ZERO direct `sqlite3`, `aiosqlite`, `deepagents`, `langgraph`, or `subprocess` imports.
8. ZERO test doubles or mock LLMs anywhere inside `backend/app/` (keep fakes strictly under `tests/fakes/`).

## 3. Coding & Runtime Conventions (FastAPI 0.165 + LangGraph)

- **Async-only I/O** on the backend; pass **both** `http_client=httpx.Client(trust_env=False)` and `http_async_client=httpx.AsyncClient(trust_env=False)` plus `max_retries=0` on `ReasoningChatOpenAI`.
- **Strong typing**: Avoid `Any`/`any`; use `Protocol`, `Union`, generics, or `object`/`unknown` with narrowing.
- **State-owning methods**: Attach behavior to classes that own state (`ArtifactStore`, `SkillStore`, `TurnCheckpointStore`, `WorkspaceInspector`, `ShellGuard`, `SQLiteSessionRepository`, `DeepAgentHarness`).
- **Never swallow exceptions**: Re-raise `(GraphBubbleUp, NodeCancelledError, asyncio.CancelledError)` inside tool wrappers so LangGraph `interrupt()` and stream cancellation work cleanly; convert recoverable tool errors into structured `ToolMessage`s.
- **Terminal `after_agent` rule**: Agent `after_agent` / `aafter_agent` hooks MUST return `None` (never `{"jump_to": "end"}`) to avoid `GraphRecursionError`.
- **DeepAgents Sandbox Permission rule**: Pass `permissions=None` to `create_deep_agent` whenever using a `SandboxBackendProtocol` backend (`LocalShellBackend`), enforcing rules via `ShellGuard` + middleware; reserve `FilesystemPermission` for read-only `Ask` mode (`FilesystemBackend`).
- **Agent Tool-Space & Verification Invariants**:
  - Prefer progressive-disclosure `SKILL.md` (`domains/skills/builtin/`) over adding specialized tools; keep the core tool count lean.
  - Enforce *"Changes can't modify their own checks"*: writes/deletes to `ShellGuard.is_verifier_rule_path` (`AGENTS.md`, `CLAUDE.md`, `REVIEW.md`, `.github/workflows/*`, `.claude/hooks/*`, `test_architecture_boundaries.py`) MUST require explicit HITL approval.
  - Read `ToolMessage.artifact["exit_code"]` (with `[Command failed with exit code N]` fallback) when inspecting `deepagents` `execute` results.
  - Session Revert MUST synchronize all 3 layers: `TurnCheckpointStore` reverse-chronological file restoration, SQLite message truncation (`metadata_json` backwards-compatible), and `DeepAgentHarness.reset_thread` + `prior_messages` re-hydration.
- **Brand-Decoupled Naming**: Keep the product brand name (`APP_NAME = "Anvil"`) isolated strictly to presentation/persona constants (`backend/app/shared/config.py`, `frontend/src/constants.ts`, `frontend/index.html`). **NEVER** prefix internal storage paths (`.data/sessions.db`), provenance files (`.skill-meta.json`), env vars (`LOG_LEVEL`, `LOG_FORMAT`), `ContextVar`s, middleware constants, `localStorage` keys (`llm.settings.v1`, `ui.color_mode`, `layout.*.width`), or Astryx themes (`themes/workbench`) with a product brand name.
- **Web UI-Only LLM Credentials Security**: Read `X-LLM-Base-Url`, `X-LLM-Model`, `X-LLM-Api-Key`, `X-LLM-Enable-Thinking` per request into `LLMCredentials`. **NEVER** log plaintext keys, store them in SQLite/checkpoints/LRU keys (SHA-256 prefix only), or return them in HTTP/SSE responses. Missing credentials fail fast with HTTP `428 LLM_SETTINGS_REQUIRED`. Route all backend logs through `shared/logging.py` (`SecretRedactionFilter`).

## 4. Frontend Skill Precedence Stack (Astryx-First)

1. **`astryx` (Highest)**: Start every view from Astryx CLI templates (`shell-side-nav`, `ai-chat`, `settings-dialog`, `documentation-technical`, `file-explorer`). Strict CSS import order: `reset.css` -> `astryx.css` -> `theme.css` inside `<Theme>`. **Zero raw `<div>`/`<span>`/`<svg>`, zero custom `.css` imports, zero `style={{}}`/`className=`, zero hardcoded `#hex`/`px`.**
   - **Native Primitives First**: Run `npx astryx search "<query>"` and `npx astryx component <Name> --props` before coding UI. Reuse `<Kbd>`, `<IconButton>`, `useHotkeys`, `<AlertDialog>` (noting `onAction` does not auto-close), `<Drawer>`, `<MoreMenu>`, and `<CodeBlock endContent={...}>` instead of custom implementations.
   - **Theme-Driven Density & Component Sizing**: Tune code-block padding/font-size, card surfaces, and input spacing in `frontend/themes/workbench/workbench.theme.ts` (`npx astryx experimental theme compile themes/workbench/workbench.theme.ts`), never per-page overrides. Wrap leading icons or trailing action buttons beside growing inputs/text in `<StackItem size="static">` (and wrap growing inputs in `<StackItem size="fill">` with `<TextInput width="100%">` so inputs fill the row and sibling buttons never crush into `"Ins..."`), prefer `IconButton` for self-evident compact row actions (e.g. download/install), use monochrome `<Kbd>` instead of solid semantic `<Badge variant="info">` for keycaps/indices, and prefer minimal chrome labels without redundant subtitles or hardcoded model names.
2. **`vercel-composition-patterns`**: Compound components + React context state sharing; zero boolean-prop explosion. Keep raw SSE/message arrays immutable and derive grouped UI views (`ExplorationGroup`, timers, revert summaries) via pure selector/`useMemo` helpers with zero extra dependencies.
3. **`modern-web-guidance`**: Query `npx -y modern-web-guidance@latest search/retrieve` before coding SSE `fetch` streaming, modal `<dialog>`/`AlertDialog` confirmations, tabs, scroll anchoring, or sanitized Markdown/SVG rendering (delete temporary `/tmp/mwg-guide-*.md` files before committing).
4. **`impeccable`**: Read `impeccable` `SKILL.md` for layout hierarchy, visual restraint, and accessibility; verify every UI diff with `impeccable detect --json --changed` (`[]`), `npx astryx doctor --built .` (`0 warn, 0 fail`), and the `ui-verify` skill.
