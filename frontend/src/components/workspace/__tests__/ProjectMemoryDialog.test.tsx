import { Theme } from "@astryxdesign/core/theme";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { apiClient } from "../../../api/client";
import { defaultThemeSlug, themes } from "../../../astryx-themes";
import {
  WorkspaceProvider,
  type WorkspaceActions,
  type WorkspaceContextValue,
  type WorkspaceState,
} from "../../../state/workspaceContext";
import { ProjectMemoryDialog } from "../ProjectMemoryDialog";

function makeMemoryMockValue(
  stateOverrides: Partial<WorkspaceState> = {},
  actionOverrides: Partial<WorkspaceActions> = {},
): WorkspaceContextValue {
  const baseProject = {
    id: "proj-alpha",
    name: "alpha-service",
    path: "/work/alpha-service",
    is_git_repo: true,
    branch: "main",
    created_at: "2026-11-22T00:00:00Z",
    updated_at: "2026-11-22T00:00:00Z",
  };
  const baseSession = {
    id: "sess-1",
    project_id: "proj-alpha",
    title: "Session",
    workspace_path: "/work/alpha-service",
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
    skillsOpen: false,
    memoryOpen: true,
    installedSkills: [],
    pinnedSkills: [],
    projects: [baseProject],
    activeProjectId: "proj-alpha",
    sessions: [baseSession],
    activeSession: baseSession,
    messages: [],
    turnDurationsMs: {},
    hasMoreMessages: false,
    isLoadingOlderMessages: false,
    streamingAssistant: null,
    isStreaming: false,
    mode: "ask",
    workspacePath: "/work/alpha-service",
    workspaceInfo: {
      path: "/work/alpha-service",
      name: "alpha-service",
      is_git_repo: true,
      branch: "main",
    },
    workspaceError: null,
    artifacts: { artifacts: [], walkthrough_plan: null },
    diff: null,
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
    openSkills: vi.fn(),
    closeSkills: vi.fn(),
    openMemory: vi.fn(),
    closeMemory: vi.fn(),
    refreshInstalledSkills: vi.fn().mockResolvedValue(undefined),
    pinSkill: vi.fn(),
    unpinSkill: vi.fn(),
    clearPinnedSkills: vi.fn(),
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
    loadOlderMessages: vi.fn().mockResolvedValue(undefined),
    stopStream: vi.fn(),
    resumeInterrupt: vi.fn().mockResolvedValue(undefined),
    openRightDrawer: vi.fn(),
    setActiveArtifactTab: vi.fn(),
    jumpToDiffHunk: vi.fn().mockResolvedValue(undefined),
    refreshDiff: vi.fn().mockResolvedValue(undefined),
    revertFromMessage: vi.fn().mockResolvedValue(undefined),
    revertDiffFiles: vi.fn().mockResolvedValue(undefined),
    saveArtifactToWorkspace: vi.fn().mockResolvedValue("/work/alpha-service/docs/ONBOARDING.md"),
    ...actionOverrides,
  };
  return { state, actions, meta: { configured: true } };
}

function renderMemoryDialog(value: WorkspaceContextValue) {
  return render(
    <Theme theme={themes[defaultThemeSlug]}>
      <WorkspaceProvider initialValue={value}>
        <ProjectMemoryDialog />
      </WorkspaceProvider>
    </Theme>,
  );
}

describe("ProjectMemoryDialog", () => {
  afterEach(() => {
    vi.restoreAllMocks();
  });

  it("loads project memory, shows isolation and repo AGENTS.md badges, edits, saves, and clears", async () => {
    const getSpy = vi.spyOn(apiClient, "getProjectMemory").mockResolvedValue({
      project_id: "proj-alpha",
      content: "# Alpha Notes\nRun pytest -q",
      updated_at: "2026-11-22T00:00:00Z",
      has_repo_agents_md: true,
      max_bytes: 32 * 1024,
    });
    const updateSpy = vi.spyOn(apiClient, "updateProjectMemory").mockResolvedValue({
      project_id: "proj-alpha",
      content: "# Alpha Notes\nAlways use ruff.",
      updated_at: "2026-11-22T01:00:00Z",
      has_repo_agents_md: true,
      max_bytes: 32 * 1024,
    });
    const clearSpy = vi.spyOn(apiClient, "clearProjectMemory").mockResolvedValue(undefined);

    const val = makeMemoryMockValue();
    renderMemoryDialog(val);

    await screen.findByDisplayValue(/Run pytest -q/);
    expect(getSpy).toHaveBeenCalledWith("proj-alpha");
    expect(screen.getByText(/Project Memory · alpha-service/)).toBeInTheDocument();
    expect(screen.getByText("Project-scoped")).toBeInTheDocument();
    expect(screen.getByText("Repo AGENTS.md detected")).toBeInTheDocument();

    const textarea = screen.getByRole("textbox", { name: /Project memory/i });
    fireEvent.change(textarea, { target: { value: "# Alpha Notes\nAlways use ruff." } });

    const saveBtn = screen.getByRole("button", { name: "Save" });
    fireEvent.click(saveBtn);

    await waitFor(() => {
      expect(updateSpy).toHaveBeenCalledWith("proj-alpha", "# Alpha Notes\nAlways use ruff.");
    });
    await screen.findByText("Project memory saved.");

    // Clear with inline confirmation
    fireEvent.click(screen.getByRole("button", { name: "Clear Memory" }));
    fireEvent.click(screen.getByRole("button", { name: "Confirm Clear" }));

    await waitFor(() => {
      expect(clearSpy).toHaveBeenCalledWith("proj-alpha");
    });
    await screen.findByText("Project memory cleared.");
    expect(textarea).toHaveValue("");
  });

  it("shows No repo AGENTS.md badge and blocks saving when exceeding max_bytes", async () => {
    vi.spyOn(apiClient, "getProjectMemory").mockResolvedValue({
      project_id: "proj-alpha",
      content: "",
      updated_at: null,
      has_repo_agents_md: false,
      max_bytes: 16,
    });

    const val = makeMemoryMockValue();
    renderMemoryDialog(val);

    await screen.findByText("No repo AGENTS.md");
    const textarea = screen.getByRole("textbox", { name: /Project memory/i });
    fireEvent.change(textarea, { target: { value: "This content is way over sixteen bytes." } });

    expect(screen.getByText(/Memory exceeds the/i)).toBeInTheDocument();
    const updateSpy = vi.spyOn(apiClient, "updateProjectMemory");
    const saveBtn = screen.getByRole("button", { name: "Save" });
    expect(saveBtn).toHaveAttribute("aria-disabled", "true");
    fireEvent.click(saveBtn);
    expect(updateSpy).not.toHaveBeenCalled();
  });
});
