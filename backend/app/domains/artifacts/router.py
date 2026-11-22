"""Thin FastAPI router for reading and exporting Antigravity artifacts."""

from __future__ import annotations

from fastapi import APIRouter
from pydantic import BaseModel, Field

from backend.app.domains.artifacts.entities import ArtifactDoc, ArtifactKind, WalkthroughPlan
from backend.app.shared.dependencies import ArtifactStoreDep

router = APIRouter(prefix="/api/sessions/{session_id}/artifacts", tags=["artifacts"])


class ArtifactBundleResponse(BaseModel):
    artifacts: list[ArtifactDoc]
    walkthrough_plan: WalkthroughPlan | None = None


class SaveArtifactRequest(BaseModel):
    workspace_path: str = Field(min_length=1)
    relative_path: str = Field(min_length=1)


class SaveArtifactResponse(BaseModel):
    saved_path: str


@router.get("")
async def list_session_artifacts(
    session_id: str,
    store: ArtifactStoreDep,
) -> ArtifactBundleResponse:
    return ArtifactBundleResponse(
        artifacts=store.list_artifacts(session_id),
        walkthrough_plan=store.get_walkthrough_plan(session_id),
    )


@router.get("/{kind}")
async def get_session_artifact(
    session_id: str,
    kind: ArtifactKind,
    store: ArtifactStoreDep,
) -> ArtifactDoc:
    return store.get_artifact(session_id, kind)


@router.post("/{kind}/save")
async def save_artifact_to_workspace(
    session_id: str,
    kind: ArtifactKind,
    body: SaveArtifactRequest,
    store: ArtifactStoreDep,
) -> SaveArtifactResponse:
    saved = store.save_to_workspace(
        session_id,
        kind,
        workspace_path=body.workspace_path,
        relative_path=body.relative_path,
    )
    return SaveArtifactResponse(saved_path=str(saved))
