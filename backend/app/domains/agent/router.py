"""Thin FastAPI router for workspace inspection and LLM endpoint validation."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Query, status
from pydantic import BaseModel, Field

from backend.app.domains.agent.workspace import (
    DirectoryBrowseResponse,
    FilePreview,
    TreeNode,
    WorkspaceDiffResponse,
    WorkspaceInfo,
)
from backend.app.shared.dependencies import (
    AppConfigDep,
    LLMCredentialsDep,
    ModelFactoryDep,
    WorkspaceInspectorDep,
)

router = APIRouter(prefix="/api/workspace", tags=["workspace"])


@router.get("/default")
async def default_workspace(
    config: AppConfigDep,
    inspector: WorkspaceInspectorDep,
) -> WorkspaceInfo:
    return await inspector.validate_workspace(str(config.default_workspace_path))


@router.get("/validate")
async def validate_workspace(
    path: Annotated[str, Query(min_length=1)],
    inspector: WorkspaceInspectorDep,
) -> WorkspaceInfo:
    return await inspector.validate_workspace(path)


@router.get("/browse")
async def browse_workspace_directories(
    inspector: WorkspaceInspectorDep,
    path: Annotated[str | None, Query()] = None,
) -> DirectoryBrowseResponse:
    return await inspector.browse_directories(path)


class CreateDirectoryRequest(BaseModel):
    parent_path: str = Field(min_length=1)
    name: str = Field(min_length=1)


@router.post("/mkdir", status_code=status.HTTP_201_CREATED)
async def create_workspace_directory(
    body: CreateDirectoryRequest,
    inspector: WorkspaceInspectorDep,
) -> WorkspaceInfo:
    return await inspector.create_directory(body.parent_path, body.name)


@router.get("/tree")
async def list_workspace_tree(
    path: Annotated[str, Query(min_length=1)],
    inspector: WorkspaceInspectorDep,
    rel_path: Annotated[str, Query()] = "",
    max_depth: Annotated[int, Query(ge=1, le=6)] = 3,
) -> list[TreeNode]:
    return await inspector.list_tree(path, rel_path=rel_path, max_depth=max_depth)


@router.get("/file")
async def read_workspace_file(
    path: Annotated[str, Query(min_length=1)],
    rel_path: Annotated[str, Query(min_length=1)],
    inspector: WorkspaceInspectorDep,
) -> FilePreview:
    return await inspector.read_file(path, rel_path)


@router.get("/diff")
async def get_workspace_diff(
    path: Annotated[str, Query(min_length=1)],
    inspector: WorkspaceInspectorDep,
) -> WorkspaceDiffResponse:
    return await inspector.get_diff(path)


class RevertWorkspaceDiffRequest(BaseModel):
    path: str = Field(min_length=1)
    files: list[str] | None = None


@router.post("/diff/revert")
async def revert_workspace_diff(
    body: RevertWorkspaceDiffRequest,
    inspector: WorkspaceInspectorDep,
) -> WorkspaceDiffResponse:
    return await inspector.revert_diff(body.path, files=body.files)


@router.post("/llm-ping")
async def ping_llm_endpoint(
    creds: LLMCredentialsDep,
    model_factory: ModelFactoryDep,
) -> dict[str, object]:
    return await model_factory.ping_llm(creds)
