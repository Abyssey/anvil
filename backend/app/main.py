"""FastAPI 0.165 application factory, SQLite lifespan, routers, and frontend mount."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver

from backend.app.domains.agent.harness import DeepAgentHarness
from backend.app.domains.agent.model_adapter import ModelFactory
from backend.app.domains.agent.router import router as workspace_router
from backend.app.domains.agent.workspace import WorkspaceInspector
from backend.app.domains.artifacts.router import router as artifacts_router
from backend.app.domains.artifacts.store import ArtifactStore
from backend.app.domains.sessions.repository import SQLiteSessionRepository
from backend.app.domains.sessions.router import router as sessions_router
from backend.app.domains.sessions.service import SessionService
from backend.app.shared.config import AppConfig
from backend.app.shared.dependencies import AppContainer
from backend.app.shared.errors import register_exception_handlers


def create_app(
    config: AppConfig | None = None,
    model_factory: ModelFactory | None = None,
) -> FastAPI:
    """Construct the Abyss v2 FastAPI application with async SQLite persistence."""
    app_config = config or AppConfig.from_repo_root()
    app_config.ensure_directories()

    @asynccontextmanager
    async def lifespan(fastapi_app: FastAPI) -> AsyncIterator[None]:
        app_config.ensure_directories()
        session_repo = SQLiteSessionRepository(app_config.sqlite_path)
        await session_repo.initialize()
        artifact_store = ArtifactStore(app_config.artifacts_root)
        workspace_inspector = WorkspaceInspector()

        async with AsyncSqliteSaver.from_conn_string(str(app_config.sqlite_path)) as checkpointer:
            harness = DeepAgentHarness(
                config=app_config,
                artifact_store=artifact_store,
                model_factory=model_factory,
                checkpointer=checkpointer,
            )
            session_service = SessionService(
                config=app_config,
                repo=session_repo,
                artifact_store=artifact_store,
                workspace_inspector=workspace_inspector,
                harness=harness,
            )
            fastapi_app.state.container = AppContainer(
                config=app_config,
                session_repo=session_repo,
                artifact_store=artifact_store,
                workspace_inspector=workspace_inspector,
                harness=harness,
                session_service=session_service,
            )
            yield

    fastapi_app = FastAPI(
        title="Abyss v2 Coding Agent",
        version="0.2.0",
        lifespan=lifespan,
    )
    register_exception_handlers(fastapi_app)
    fastapi_app.include_router(sessions_router)
    fastapi_app.include_router(artifacts_router)
    fastapi_app.include_router(workspace_router)

    if app_config.frontend_dist_dir.is_dir():
        fastapi_app.frontend("/", directory=str(app_config.frontend_dist_dir))

    return fastapi_app


app = create_app()
