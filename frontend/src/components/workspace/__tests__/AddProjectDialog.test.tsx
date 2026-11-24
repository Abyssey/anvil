import { Theme } from "@astryxdesign/core/theme";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { ApiError, apiClient } from "../../../api/client";
import { defaultThemeSlug, themes } from "../../../astryx-themes";
import {
  WorkspaceProvider,
  type WorkspaceActions,
  type WorkspaceContextValue,
  type WorkspaceState,
} from "../../../state/workspaceContext";
import { AddProjectDialog } from "../AddProjectDialog";

function makeAddProjectMockValue(
  stateOverrides: Partial<WorkspaceState> = {},
  actionOverrides: Partial<WorkspaceActions> = {},
): WorkspaceContextValue {
  const state: WorkspaceState = {
    settings: {
      baseUrl: "http://localhost:8001/v1",
      model: "qwen3.8-flash",
      apiKey: "sk-test",
      enableThinking: true,
    },
    settingsOpen: false,
    addProjectOpen: true,
    skillsOpen: false,
    memoryOpen: false,
    installedSkills: [],
    pinnedSkills: [],
    projects: [],
    activeProjectId: null,
    sessions: [],
    activeSession: null,
    messages: [],
    turnDurationsMs: {},
    hasMoreMessages: false,
    isLoadingOlderMessages: false,
    streamingAssistant: null,
    isStreaming: false,
    mode: "ask",
    workspacePath: "/home/dev/projects/existing",
    workspaceInfo: null,
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
    saveArtifactToWorkspace: vi.fn().mockResolvedValue("/home/dev/projects/docs/ONBOARDING.md"),
    ...actionOverrides,
  };
  return { state, actions, meta: { configured: true } };
}

function renderAddProjectDialog(value: WorkspaceContextValue) {
  return render(
    <Theme theme={themes[defaultThemeSlug]}>
      <WorkspaceProvider initialValue={value}>
        <AddProjectDialog />
      </WorkspaceProvider>
    </Theme>,
  );
}

describe("AddProjectDialog new folder creation", () => {
  afterEach(() => {
    vi.restoreAllMocks();
  });

  it("creates a new subfolder from the browse bar, navigates into it, and adds it as a project", async () => {
    const browseSpy = vi
      .spyOn(apiClient, "browseDirectories")
      .mockResolvedValueOnce({
        current_path: "/home/dev/projects",
        parent_path: "/home/dev",
        is_git_repo: false,
        branch: "",
        directories: [{ name: "existing", path: "/home/dev/projects/existing", is_git_repo: true }],
      })
      .mockResolvedValueOnce({
        current_path: "/home/dev/projects/fresh-service",
        parent_path: "/home/dev/projects",
        is_git_repo: false,
        branch: "",
        directories: [],
      });

    const createDirSpy = vi.spyOn(apiClient, "createDirectory").mockResolvedValue({
      path: "/home/dev/projects/fresh-service",
      name: "fresh-service",
      is_git_repo: false,
      branch: "",
    });

    const addProject = vi.fn().mockResolvedValue(undefined);
    const closeAddProject = vi.fn();
    const ctx = makeAddProjectMockValue({}, { addProject, closeAddProject });

    renderAddProjectDialog(ctx);

    await waitFor(() => {
      expect(screen.getByText("/home/dev/projects")).toBeInTheDocument();
    });

    fireEvent.click(screen.getByRole("button", { name: "New Folder" }));

    const folderInput = screen.getByLabelText("New folder name");
    fireEvent.change(folderInput, { target: { value: "fresh-service" } });
    fireEvent.click(screen.getByRole("button", { name: "Create" }));

    await waitFor(() => {
      expect(createDirSpy).toHaveBeenCalledWith("/home/dev/projects", "fresh-service");
      expect(browseSpy).toHaveBeenCalledWith("/home/dev/projects/fresh-service");
      expect(screen.getByDisplayValue("/home/dev/projects/fresh-service")).toBeInTheDocument();
    });

    fireEvent.click(screen.getByRole("button", { name: "Add Project" }));

    await waitFor(() => {
      expect(addProject).toHaveBeenCalledWith("/home/dev/projects/fresh-service");
      expect(closeAddProject).toHaveBeenCalled();
    });
  });

  it("offers one-click folder creation when a typed child folder path does not exist yet", async () => {
    vi.spyOn(apiClient, "browseDirectories")
      .mockResolvedValueOnce({
        current_path: "/home/dev/projects",
        parent_path: "/home/dev",
        is_git_repo: false,
        branch: "",
        directories: [],
      })
      .mockRejectedValueOnce(
        new ApiError(400, "INVALID_WORKSPACE", "Workspace directory does not exist"),
      )
      .mockResolvedValueOnce({
        current_path: "/home/dev/projects/typed-new-app",
        parent_path: "/home/dev/projects",
        is_git_repo: false,
        branch: "",
        directories: [],
      });

    const createDirSpy = vi.spyOn(apiClient, "createDirectory").mockResolvedValue({
      path: "/home/dev/projects/typed-new-app",
      name: "typed-new-app",
      is_git_repo: false,
      branch: "",
    });

    renderAddProjectDialog(makeAddProjectMockValue());

    await waitFor(() => {
      expect(screen.getByDisplayValue("/home/dev/projects")).toBeInTheDocument();
    });

    const pathInput = screen.getByLabelText("Folder path");
    fireEvent.change(pathInput, { target: { value: "/home/dev/projects/typed-new-app" } });
    fireEvent.click(screen.getByRole("button", { name: "Open" }));

    await waitFor(() => {
      expect(screen.getByText('Folder "typed-new-app" does not exist yet.')).toBeInTheDocument();
    });

    fireEvent.click(screen.getByRole("button", { name: "Create Folder" }));

    await waitFor(() => {
      expect(createDirSpy).toHaveBeenCalledWith("/home/dev/projects", "typed-new-app");
      expect(screen.getByDisplayValue("/home/dev/projects/typed-new-app")).toBeInTheDocument();
    });
  });
});
