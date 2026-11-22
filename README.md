# Abyss v2 — DeepAgents Coding Agent

A zero-base, clean-architecture Coding Agent built with **FastAPI 0.165**, **LangChain DeepAgents (`0.7.41`)**, **Open SWE** backend runtime patterns, and **React 19 + Astryx (`0.7.17`)**.

## Highlights

- **Claude Code-Style 2-Pane Workspace**: Focused streaming chat (`Ask` / `Plan` / `Auto` mode toggle), collapsible Thinking trace (`reasoning_content`), inline tool & subagent timelines (`explorer`, `reviewer`), and an on-demand right drawer (`Artifacts` | `Files` | `Diff`).
- **Antigravity Artifacts + HITL Gates**:
  - `Ask` mode: Read-only parallel agentic search (`ls`, `glob`, `grep`, `read_file`, `explorer`) producing an immediate `walkthrough.md` artifact (with 1-click export to workspace).
  - `Plan` mode: Writes `implementation_plan.md` → pauses via LangGraph `interrupt()` (`Proceed` / `Request Changes`) → syncs `write_todos` to `task.md` → gates shell commands with `Approve` / `Deny` → generates verification-backed `walkthrough.md` + clickable **Walkthrough Chunks** jumping straight into the Diff Viewer.
  - `Auto` mode: Executes edits and safe checks autonomously while still gating destructive shell commands (`rm`, `git push`, `git reset --hard`, etc.).
- **Web UI-Only LLM Configuration (`qwen3.8-flash` & OpenAI-Compatible Endpoints)**: Enter Endpoint URL, Model ID, API Key, and Enable Thinking directly in Settings (browser `localStorage` → `X-LLM-*` headers; never stored or logged on the server).
- **SQLite Persistence**: Sessions, transcripts, artifacts, and interrupted HITL states persist across server restarts in `.abyss-data/abyss.db`.

## Quick Start (WSL2 Ubuntu)

> Always export `~/.local/bin` before running Node/npm/Astryx commands in WSL2:
> ```bash
> export PATH="$HOME/.local/bin:$PATH"
> ```

### 1. Build the Frontend & Start the Unified Server

```bash
export PATH="$HOME/.local/bin:$PATH"
cd frontend && npm install && npm run build && cd ..
.venv/bin/fastapi run backend/app/main.py --port 8000
```

Open `http://localhost:8000` in your browser. FastAPI 0.165 serves both `/api/*` and the built Astryx SPA via `app.frontend()`.

### 2. Configure Your OpenAI-Compatible Endpoint (Settings)

Click **Configure LLM** in the bottom-left sidebar and enter:
- **Endpoint URL**: e.g. `https://dashscope-intl.aliyuncs.com/compatible-mode/v1` or `http://localhost:8001/v1`
- **Model ID**: `qwen3.8-flash` (or any OpenAI-compatible model ID)
- **API Key**: Your API key (kept only in your browser)
- **Enable Thinking**: `ON`

Click **Test Connection** → **Save**.

## Demo Scenario: Code Onboarding in Seconds (`Ask` Mode)

1. Point the sidebar **Workspace directory** input to any local repository and click **Apply**.
2. Keep the composer toggle on **Ask**.
3. Enter 1–2 sentences (or click the empty-state prompt):
   > `이 프로젝트 구조와 핵심 의존성을 파악해서 온보딩 문서로 정리해줘`
4. Without manually selecting context files, the agent inspects the tree, dependencies, and `AGENTS.md` files via parallel agentic search + `explorer` subagents, streams its reasoning/tool trace, and renders a structured `walkthrough.md` onboarding document (optionally exportable to `docs/ONBOARDING.md` inside the workspace).

## Verification Commands

```bash
# Backend unit, integration & DDD AST boundary suite (100% offline)
.venv/bin/pytest -q

# Backend lint
.venv/bin/ruff check backend tests

# Frontend typecheck, unit/UI tests, production build & Astryx doctor
export PATH="$HOME/.local/bin:$PATH" && cd frontend && npm run typecheck && npm test -- --run && npm run build && npx astryx doctor --built dist
```
