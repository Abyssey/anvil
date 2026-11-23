"""FastAPI 0.165 application factory, SQLite lifespan, routers, and frontend mount."""

from __future__ import annotations

import logging
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
from backend.app.domains.sessions.router import projects_router
from backend.app.domains.sessions.router import router as sessions_router
from backend.app.domains.sessions.service import SessionService
from backend.app.domains.skills.registry_client import SkillsShClient
from backend.app.domains.skills.router import router as skills_router
from backend.app.domains.skills.store import SkillStore
from backend.app.shared.config import APP_NAME, AppConfig
from backend.app.shared.dependencies import AppContainer
from backend.app.shared.http import RequestLoggingMiddleware, register_exception_handlers
from backend.app.shared.logging import configure_logging

logger = logging.getLogger(__name__)

APP_VERSION = "0.2.0"


def create_app(
    config: AppConfig | None = None,
    model_factory: ModelFactory | None = None,
) -> FastAPI:
    """Construct the FastAPI application with async SQLite persistence."""
    app_config = config or AppConfig.from_repo_root()
    frontend_mounted = app_config.frontend_dist_dir.is_dir()

    @asynccontextmanager
    async def lifespan(fastapi_app: FastAPI) -> AsyncIterator[None]:
        configure_logging()
        app_config.ensure_directories()
        session_repo = SQLiteSessionRepository(app_config.sqlite_path)
        await session_repo.initialize()
        artifact_store = ArtifactStore(app_config.artifacts_root)
        workspace_inspector = WorkspaceInspector()
        resolved_model_factory = model_factory or ModelFactory()
        skill_store = SkillStore(
            bundled_dir=app_config.bundled_skills_dir,
            global_dir=app_config.skills_root,
            registry_client=SkillsShClient(),
        )

        async with AsyncSqliteSaver.from_conn_string(str(app_config.sqlite_path)) as checkpointer:
            harness = DeepAgentHarness(
                config=app_config,
                artifact_store=artifact_store,
                model_factory=resolved_model_factory,
                checkpointer=checkpointer,
                global_skills_dir=app_config.skills_root,
                memories_root=app_config.memories_root,
            )
            session_service = SessionService(
                config=app_config,
                repo=session_repo,
                artifact_store=artifact_store,
                workspace_inspector=workspace_inspector,
                harness=harness,
                memories_root=app_config.memories_root,
                on_delete_session_thread=harness.delete_thread,
            )
            fastapi_app.state.container = AppContainer(
                config=app_config,
                session_repo=session_repo,
                artifact_store=artifact_store,
                workspace_inspector=workspace_inspector,
                model_factory=resolved_model_factory,
                harness=harness,
                session_service=session_service,
                skill_store=skill_store,
            )
            logger.info(
                "app.startup",
                extra={
                    "event": "app.startup",
                    "version": APP_VERSION,
                    "data_dir": str(app_config.data_dir),
                    "sqlite_path": str(app_config.sqlite_path),
                    "frontend_mounted": frontend_mounted,
                },
            )
            try:
                yield
            finally:
                logger.info("app.shutdown", extra={"event": "app.shutdown"})

    fastapi_app = FastAPI(
        title=f"{APP_NAME} Coding Agent",
        version=APP_VERSION,
        lifespan=lifespan,
    )
    fastapi_app.add_middleware(RequestLoggingMiddleware)
    register_exception_handlers(fastapi_app)
    fastapi_app.include_router(projects_router)
    fastapi_app.include_router(sessions_router)
    fastapi_app.include_router(artifacts_router)
    fastapi_app.include_router(workspace_router)
    fastapi_app.include_router(skills_router)

    if frontend_mounted:
        fastapi_app.frontend("/", directory=str(app_config.frontend_dist_dir))

    return fastapi_app


app = create_app()
