import { Theme } from "@astryxdesign/core/theme";
import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { defaultThemeSlug, themes } from "../../astryx-themes";
import {
  WorkspaceProvider,
  type WorkspaceActions,
  type WorkspaceContextValue,
  type WorkspaceState,
} from "../../state/workspaceContext";
import { SettingsDialog } from "../settings/SettingsDialog";
import { AddProjectDialog } from "../workspace/AddProjectDialog";
import { WorkspaceShell } from "../workspace/WorkspaceShell";

function makeMockValue(
  stateOverrides: Partial<WorkspaceState> = {},
  actionOverrides: Partial<WorkspaceActions> = {},
): WorkspaceContextValue {
  const baseProject = {
    id: "proj-1",
    name: "repo",
    path: "/repo",
    is_git_repo: true,
    branch: "main",
    created_at: "2026-11-22T00:00:00Z",
    updated_at: "2026-11-22T00:00:00Z",
  };
  const baseSession = {
    id: "sess-1",
    project_id: "proj-1",
    title: "Code Onboarding",
    workspace_path: "/repo",
    mode: "ask" as const,
    status: "idle" as const,
    pending_interrupt: null,
    created_at: "2026-11-22T00:00:00Z",
    updated_at: "2026-11-22T00:00:00Z",
  };
  const state: WorkspaceState = {
    settings: {
      baseUrl: "http://localhost:8001/v1",
      model: "qwen3.8-flash",
      apiKey: "sk-test",
      enableThinking: true,
    },
    settingsOpen: false,
    addProjectOpen: false,
    projects: [baseProject],
    activeProjectId: "proj-1",
    sessions: [baseSession],
    activeSession: baseSession,
    messages: [],
    streamingAssistant: null,
    isStreaming: false,
    mode: "ask",
    workspacePath: "/repo",
    workspaceInfo: { path: "/repo", name: "repo", is_git_repo: true, branch: "main" },
    workspaceError: null,
    artifacts: { artifacts: [], walkthrough_plan: null },
    diff: { workspace_path: "/repo", branch: "main", git_head: "abc123", files: [] },
    diffFocus: null,
    rightDrawerTab: null,
    activeArtifactTab: "walkthrough",
    streamError: null,
    treeRevision: 0,
    ...stateOverrides,
  };
  const actions: WorkspaceActions = {
    openSettings: vi.fn(),
    closeSettings: vi.fn(),
    openAddProject: vi.fn(),
    closeAddProject: vi.fn(),
    updateSettings: vi.fn(),
    clearSettings: vi.fn(),
    addProject: vi.fn().mockResolvedValue(undefined),
    selectProject: vi.fn().mockResolvedValue(undefined),
    removeProject: vi.fn().mockResolvedValue(undefined),
    createNewSession: vi.fn().mockResolvedValue(undefined),
    selectSession: vi.fn().mockResolvedValue(undefined),
    removeSession: vi.fn().mockResolvedValue(undefined),
    setWorkspacePath: vi.fn().mockResolvedValue(undefined),
    setMode: vi.fn().mockResolvedValue(undefined),
    sendMessage: vi.fn().mockResolvedValue(undefined),
    stopStream: vi.fn(),
    resumeInterrupt: vi.fn().mockResolvedValue(undefined),
    openRightDrawer: vi.fn(),
    setActiveArtifactTab: vi.fn(),
    jumpToDiffHunk: vi.fn().mockResolvedValue(undefined),
    refreshDiff: vi.fn().mockResolvedValue(undefined),
    saveArtifactToWorkspace: vi.fn().mockResolvedValue("/repo/docs/ONBOARDING.md"),
    ...actionOverrides,
  };
  return {
    state,
    actions,
    meta: { configured: Boolean(state.settings.apiKey) },
  };
}

function renderWorkspace(value: WorkspaceContextValue) {
  return render(
    <Theme theme={themes[defaultThemeSlug]}>
      <WorkspaceProvider initialValue={value}>
        <WorkspaceShell />
        <SettingsDialog />
        <AddProjectDialog />
      </WorkspaceProvider>
    </Theme>,
  );
}

describe("Abyss Workspace UI (Astryx + HITL)", () => {
  it("supports switching modes (Ask / Plan / Auto) and opening Settings", () => {
    const val = makeMockValue();
    renderWorkspace(val);

    fireEvent.click(screen.getByRole("radio", { name: "Plan" }));
    expect(val.actions.setMode).toHaveBeenCalledWith("plan");

    fireEvent.click(screen.getByRole("button", { name: /qwen3\.8-flash/i }));
    expect(val.actions.openSettings).toHaveBeenCalled();
  });

  it("renders Plan approval bar and calls resumeInterrupt(approve) on Proceed", () => {
    const val = makeMockValue({
      mode: "plan",
      rightDrawerTab: "artifacts",
      activeArtifactTab: "implementation_plan",
      activeSession: {
        id: "sess-1",
        title: "Plan Run",
        workspace_path: "/repo",
        mode: "plan",
        status: "awaiting_approval",
        pending_interrupt: {
          kind: "plan_review",
          summary: "Review implementation_plan.md",
          artifact_kind: "implementation_plan",
        },
        created_at: "2026-11-22T00:00:00Z",
        updated_at: "2026-11-22T00:00:00Z",
      },
      artifacts: {
        artifacts: [
          {
            kind: "implementation_plan",
            title: "Implementation Plan",
            content: "# Plan\nEdit main.py",
            updated_at: "2026-11-22T00:00:00Z",
          },
        ],
        walkthrough_plan: null,
      },
    });
    renderWorkspace(val);

    fireEvent.click(screen.getByRole("button", { name: "Proceed" }));
    expect(val.actions.resumeInterrupt).toHaveBeenCalledWith("approve");
  });

  it("renders inline ShellApprovalCard and Walkthrough Chunk jump to Diff", () => {
    const val = makeMockValue({
      rightDrawerTab: "artifacts",
      activeArtifactTab: "walkthrough",
      activeSession: {
        id: "sess-1",
        title: "Shell Run",
        workspace_path: "/repo",
        mode: "plan",
        status: "awaiting_approval",
        pending_interrupt: {
          kind: "shell_approval",
          summary: "Approve pytest",
          command: "pytest -q",
          workspace: "/repo",
          reason: "Plan mode requires approval",
        },
        created_at: "2026-11-22T00:00:00Z",
        updated_at: "2026-11-22T00:00:00Z",
      },
      artifacts: {
        artifacts: [
          {
            kind: "walkthrough",
            title: "Walkthrough",
            content: "## Summary\nUpdated main.py",
            updated_at: "2026-11-22T00:00:00Z",
          },
        ],
        walkthrough_plan: {
          git_head: "abc123",
          chunks: [
            {
              id: "chunk-1",
              title: "Updates in backend",
              explanation: "Modified app.py (1 hunk).",
              file_hunks: [{ path: "backend/app.py", hunks: ["+12"] }],
            },
          ],
          other_files: [],
        },
      },
    });
    renderWorkspace(val);

    fireEvent.click(screen.getByRole("button", { name: "Approve" }));
    expect(val.actions.resumeInterrupt).toHaveBeenCalledWith("approve");

    fireEvent.click(screen.getByRole("button", { name: "backend/app.py (+12)" }));
    expect(val.actions.jumpToDiffHunk).toHaveBeenCalledWith("backend/app.py", "+12");
  });

  it("renders empty-state onboarding prompt and unconfigured LLM banner when messages are empty", () => {
    const val = makeMockValue({
      settings: {
        baseUrl: "",
        model: "qwen3.8-flash",
        apiKey: "",
        enableThinking: true,
      },
      messages: [],
      streamingAssistant: null,
    });
    renderWorkspace(val);

    expect(screen.getByRole("heading", { name: "Abyss Coding Agent" })).toBeInTheDocument();
    expect(screen.getByText("Configure your LLM endpoint")).toBeInTheDocument();

    const onboardingBtn = screen.getByRole("button", {
      name: "이 프로젝트 구조와 핵심 의존성을 파악해서 온보딩 문서로 정리해줘",
    });
    fireEvent.click(onboardingBtn);
    expect(val.actions.sendMessage).toHaveBeenCalledTimes(1);
  });

  it("submits composer input once and keeps Stop clickable during streaming", () => {
    const val = makeMockValue();
    const { unmount } = renderWorkspace(val);

    const textbox = screen.getByRole("textbox", { name: "Message input" });
    textbox.textContent = "Explain architecture";
    fireEvent.input(textbox);
    fireEvent.keyDown(textbox, { key: "Enter", code: "Enter" });

    expect(val.actions.sendMessage).toHaveBeenCalledTimes(1);
    expect(val.actions.sendMessage).toHaveBeenCalledWith("Explain architecture");
    unmount();

    const streamingVal = makeMockValue({ isStreaming: true });
    renderWorkspace(streamingVal);
    const streamingBox = screen.getByRole("textbox", { name: "Message input" });
    expect(streamingBox).toHaveAttribute("aria-disabled", "true");
    const stopBtn = screen.getByRole("button", { name: /stop/i });
    expect(stopBtn).not.toBeDisabled();
    fireEvent.click(stopBtn);
    expect(streamingVal.actions.stopStream).toHaveBeenCalledTimes(1);
  });

  it("shows inline Plan review controls when drawer is closed and supports Cancel (deny)", () => {
    const val = makeMockValue({
      mode: "plan",
      rightDrawerTab: null,
      activeSession: {
        id: "sess-1",
        title: "Plan Run",
        workspace_path: "/repo",
        mode: "plan",
        status: "awaiting_approval",
        pending_interrupt: {
          kind: "plan_review",
          summary: "Review implementation_plan.md",
          artifact_kind: "implementation_plan",
        },
        created_at: "2026-11-22T00:00:00Z",
        updated_at: "2026-11-22T00:00:00Z",
      },
    });
    renderWorkspace(val);

    expect(screen.getByText("Plan ready for review")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Open Plan" }));
    expect(val.actions.openRightDrawer).toHaveBeenCalledWith("artifacts");

    fireEvent.click(screen.getByRole("button", { name: "Cancel" }));
    expect(val.actions.resumeInterrupt).toHaveBeenCalledWith("deny");
  });

  it("omits left sidebar collapse button and renders Add Project button", () => {
    const val = makeMockValue();
    renderWorkspace(val);

    expect(screen.queryByRole("button", { name: /collapse/i })).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: /Add Project/i })).toBeInTheDocument();
  });

  it("filters project files and switches to dedicated file preview with Back to Files", async () => {
    const { apiClient } = await import("../../api/client");
    vi.spyOn(apiClient, "getWorkspaceTree").mockResolvedValue([
      { name: "README.md", path: "README.md", is_dir: false, size: 12, children: [] },
      { name: "main.py", path: "main.py", is_dir: false, size: 40, children: [] },
    ]);
    vi.spyOn(apiClient, "getWorkspaceFile").mockResolvedValue({
      path: "main.py",
      content: "print('hello')\n",
      is_binary: false,
      truncated: false,
      size: 15,
    });

    const val = makeMockValue({ rightDrawerTab: "files" });
    renderWorkspace(val);

    const filterInput = await screen.findByPlaceholderText(/Filter files/i);
    await screen.findByText("README.md");
    fireEvent.change(filterInput, { target: { value: "main" } });
    expect(screen.queryByText("README.md")).not.toBeInTheDocument();
    expect(screen.getByText("main.py")).toBeInTheDocument();

    fireEvent.click(screen.getByText("main.py"));
    const backBtn = await screen.findByRole("button", { name: /Back to Files/i });
    expect(screen.getByText(/print\('hello'\)/)).toBeInTheDocument();

    fireEvent.click(backBtn);
    expect(screen.getByPlaceholderText(/Filter files/i)).toBeInTheDocument();
  });
});
