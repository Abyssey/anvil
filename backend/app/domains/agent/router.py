"""Thin FastAPI router for workspace inspection and LLM endpoint validation."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Query

from backend.app.domains.agent.workspace import (
    FilePreview,
    TreeNode,
    WorkspaceDiffResponse,
    WorkspaceInfo,
)
from backend.app.shared.dependencies import (
    AppConfigDep,
    LLMCredentialsDep,
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


@router.post("/llm-ping")
async def ping_llm_endpoint(
    creds: LLMCredentialsDep,
    inspector: WorkspaceInspectorDep,
) -> dict[str, object]:
    return await inspector.ping_llm(creds)
