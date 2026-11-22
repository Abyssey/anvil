"""FastAPI Annotated dependency injection container and aliases."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Annotated

from fastapi import Depends, Request

from backend.app.shared.config import AppConfig
from backend.app.shared.http import extract_llm_credentials
from backend.app.shared.llm_credentials import LLMCredentials

if TYPE_CHECKING:
    from backend.app.domains.agent.harness import DeepAgentHarness
    from backend.app.domains.agent.model_adapter import ModelFactory
    from backend.app.domains.agent.workspace import WorkspaceInspector
    from backend.app.domains.artifacts.store import ArtifactStore
    from backend.app.domains.sessions.repository import SessionRepository
    from backend.app.domains.sessions.service import SessionService
    from backend.app.domains.skills.store import SkillStore


@dataclass
class AppContainer:
    """Application-scoped dependency container stored on app.state.container."""

    config: AppConfig
    session_repo: SessionRepository
    artifact_store: ArtifactStore
    workspace_inspector: WorkspaceInspector
    model_factory: ModelFactory
    harness: DeepAgentHarness
    session_service: SessionService
    skill_store: SkillStore


def get_container(request: Request) -> AppContainer:
    return request.app.state.container


def get_config(container: Annotated[AppContainer, Depends(get_container)]) -> AppConfig:
    return container.config


def get_session_service(
    container: Annotated[AppContainer, Depends(get_container)],
) -> SessionService:
    return container.session_service


def get_artifact_store(container: Annotated[AppContainer, Depends(get_container)]) -> ArtifactStore:
    return container.artifact_store


def get_workspace_inspector(
    container: Annotated[AppContainer, Depends(get_container)],
) -> WorkspaceInspector:
    return container.workspace_inspector


def get_model_factory(
    container: Annotated[AppContainer, Depends(get_container)],
) -> ModelFactory:
    return container.model_factory


def get_skill_store(container: Annotated[AppContainer, Depends(get_container)]) -> SkillStore:
    return container.skill_store


AppConfigDep = Annotated[AppConfig, Depends(get_config)]
SessionServiceDep = Annotated["SessionService", Depends(get_session_service)]
ArtifactStoreDep = Annotated["ArtifactStore", Depends(get_artifact_store)]
WorkspaceInspectorDep = Annotated["WorkspaceInspector", Depends(get_workspace_inspector)]
ModelFactoryDep = Annotated["ModelFactory", Depends(get_model_factory)]
SkillStoreDep = Annotated["SkillStore", Depends(get_skill_store)]
LLMCredentialsDep = Annotated[LLMCredentials, Depends(extract_llm_credentials)]
