import { Theme } from "@astryxdesign/core/theme";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { apiClient } from "../../../api/client";
import type { InstalledSkill } from "../../../api/types";
import { defaultThemeSlug, themes } from "../../../astryx-themes";
import {
  WorkspaceProvider,
  type WorkspaceActions,
  type WorkspaceContextValue,
  type WorkspaceState,
} from "../../../state/workspaceContext";
import { SkillsDialog } from "../SkillsDialog";

function makeSkillsMockValue(
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
    title: "Session",
    workspace_path: "/repo",
    mode: "ask" as const,
    status: "idle" as const,
    pending_interrupt: null,
    created_at: "2026-11-22T00:00:00Z",
    updated_at: "2026-11-22T00:00:00Z",
  };
  const defaultSkills: InstalledSkill[] = [
    {
      name: "bundled-review",
      description: "Built-in code review skill",
      scope: "bundled",
      source_label: "Built-in",
      path: "/bundled/bundled-review",
      deletable: false,
    },
    {
      name: "react-patterns",
      description: "React composition guidance",
      scope: "global",
      source_label: "Global",
      path: "/global/react-patterns",
      deletable: true,
      origin_source: "vercel-labs/agent-skills",
      origin_skill_id: "react-patterns",
    },
  ];
  const state: WorkspaceState = {
    settings: {
      baseUrl: "http://localhost:8001/v1",
      model: "qwen3.8-flash",
      apiKey: "sk-test",
      enableThinking: true,
    },
    settingsOpen: false,
    addProjectOpen: false,
    skillsOpen: true,
    memoryOpen: false,
    installedSkills: defaultSkills,
    pinnedSkills: [],
    projects: [baseProject],
    activeProjectId: "proj-1",
    sessions: [baseSession],
    activeSession: baseSession,
    messages: [],
    turnDurationsMs: {},
    hasMoreMessages: false,
    isLoadingOlderMessages: false,
    streamingAssistant: null,
    isStreaming: false,
    mode: "ask",
    workspacePath: "/repo",
    workspaceInfo: { path: "/repo", name: "repo", is_git_repo: true, branch: "main" },
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
    saveArtifactToWorkspace: vi.fn().mockResolvedValue("/repo/docs/ONBOARDING.md"),
    ...actionOverrides,
  };
  return { state, actions, meta: { configured: true } };
}

function renderSkillsDialog(value: WorkspaceContextValue) {
  return render(
    <Theme theme={themes[defaultThemeSlug]}>
      <WorkspaceProvider initialValue={value}>
        <SkillsDialog />
      </WorkspaceProvider>
    </Theme>,
  );
}

describe("SkillsDialog", () => {
  afterEach(() => {
    vi.restoreAllMocks();
  });

  it("renders installed skills with bundled protection, supports preview, pin, and confirmed delete", async () => {
    const detailSpy = vi.spyOn(apiClient, "getInstalledSkillDetail").mockResolvedValue({
      name: "react-patterns",
      description: "React composition guidance",
      scope: "global",
      source_label: "Global",
      path: "/global/react-patterns",
      deletable: true,
      content: "---\nname: react-patterns\n---\n# React Patterns",
      files: ["SKILL.md"],
    });
    const deleteSpy = vi.spyOn(apiClient, "deleteSkill").mockResolvedValue(undefined);

    const val = makeSkillsMockValue();
    renderSkillsDialog(val);

    expect(screen.getByText("bundled-review")).toBeInTheDocument();
    expect(screen.getByText("Protected")).toBeInTheDocument();
    expect(screen.getByText("react-patterns")).toBeInTheDocument();

    // Only one Delete button exists (for global skill, NOT for bundled skill)
    const deleteButtons = screen.getAllByRole("button", { name: "Delete" });
    expect(deleteButtons).toHaveLength(1);

    // Preview global skill opens dedicated full-view reader
    const previewButtons = screen.getAllByRole("button", { name: "Preview" });
    fireEvent.click(previewButtons[1]);
    await screen.findByText(/# React Patterns/);
    expect(detailSpy).toHaveBeenCalledWith("global", "react-patterns", "/repo");
    expect(screen.queryByRole("tablist")).not.toBeInTheDocument();

    // Return to skill list via Back to list
    fireEvent.click(screen.getByRole("button", { name: "Back to list" }));

    // Inline delete confirmation
    fireEvent.click(screen.getByRole("button", { name: "Delete" }));
    const confirmBtn = screen.getByRole("button", { name: "Confirm Delete" });
    fireEvent.click(confirmBtn);

    await waitFor(() => {
      expect(deleteSpy).toHaveBeenCalledWith("global", "react-patterns", "/repo");
      expect(val.actions.refreshInstalledSkills).toHaveBeenCalled();
    });

    // Pin bundled skill closes dialog
    const pinButtons = screen.getAllByRole("button", { name: "Pin" });
    fireEvent.click(pinButtons[0]);
    expect(val.actions.pinSkill).toHaveBeenCalledWith("bundled-review");
    expect(val.actions.closeSkills).toHaveBeenCalled();
  });

  it("browses skills.sh catalog, searches, previews, and installs into chosen scope", async () => {
    const browseSpy = vi.spyOn(apiClient, "browseSkillsCatalog").mockResolvedValue({
      tab: "all-time",
      page: 0,
      total: 2,
      has_more: false,
      skills: [
        {
          id: "vercel-labs/agent-skills/react-patterns",
          source: "vercel-labs/agent-skills",
          skill_id: "react-patterns",
          name: "react-patterns",
          installs: 5400,
          is_official: true,
          weekly_installs: [],
        },
        {
          id: "acme/skills/tdd-guide",
          source: "acme/skills",
          skill_id: "tdd-guide",
          name: "tdd-guide",
          installs: 1200,
          is_official: false,
          weekly_installs: [],
        },
      ],
    });
    const searchSpy = vi.spyOn(apiClient, "searchSkillsCatalog").mockResolvedValue([
      {
        id: "acme/skills/tdd-guide",
        source: "acme/skills",
        skill_id: "tdd-guide",
        name: "tdd-guide",
        installs: 1200,
        is_official: false,
        weekly_installs: [],
      },
    ]);
    const previewRemoteSpy = vi.spyOn(apiClient, "previewRemoteSkill").mockResolvedValue({
      source: "acme/skills",
      skill_id: "tdd-guide",
      name: "tdd-guide",
      description: "Test-driven development",
      content: "# TDD Guide\nWrite failing test first.",
      files: ["SKILL.md"],
    });
    const installSpy = vi.spyOn(apiClient, "installSkill").mockResolvedValue({
      name: "tdd-guide",
      description: "Test-driven development",
      scope: "workspace",
      source_label: "Workspace",
      path: "/repo/.agents/skills/tdd-guide",
      deletable: true,
    });

    const val = makeSkillsMockValue();
    renderSkillsDialog(val);

    fireEvent.click(screen.getByRole("tab", { name: "Discover (skills.sh)" }));

    await screen.findByText("tdd-guide");
    expect(browseSpy).toHaveBeenCalledWith("all-time", 0, "/repo");
    // react-patterns is already installed in global scope -> shows Installed badge
    expect(screen.getByText("Installed")).toBeInTheDocument();

    // Switch scope to This Project (workspace)
    fireEvent.click(screen.getByRole("radio", { name: "This Project" }));

    // Search >= 2 chars
    const searchInput = screen.getByPlaceholderText(/Search skills\.sh/i);
    fireEvent.change(searchInput, { target: { value: "tdd" } });

    await waitFor(() => {
      expect(searchSpy).toHaveBeenCalledWith("tdd", 20, "/repo");
    });

    // Preview remote skill opens dedicated reader and supports direct Install from reader
    fireEvent.click(screen.getByRole("button", { name: "Preview" }));
    await screen.findByText(/Write failing test first\./);
    expect(previewRemoteSpy).toHaveBeenCalledWith("acme/skills", "tdd-guide");

    // Install into workspace scope directly from the dedicated preview header
    fireEvent.click(screen.getByRole("button", { name: "Install" }));

    await waitFor(() => {
      expect(installSpy).toHaveBeenCalledWith({
        source: "acme/skills",
        skill_id: "tdd-guide",
        scope: "workspace",
        workspace_path: "/repo",
      });
      expect(val.actions.refreshInstalledSkills).toHaveBeenCalled();
    });
  });

  it("does not re-fetch installed skills when actions object identity changes while open", async () => {
    const refreshInstalledSkills = vi.fn().mockResolvedValue(undefined);
    const initialVal = makeSkillsMockValue({ skillsOpen: true }, { refreshInstalledSkills });
    const { rerender } = renderSkillsDialog(initialVal);

    await waitFor(() => {
      expect(refreshInstalledSkills).toHaveBeenCalledTimes(1);
    });

    const nextVal = makeSkillsMockValue(
      { skillsOpen: true, installedSkills: [...initialVal.state.installedSkills] },
      { refreshInstalledSkills },
    );
    expect(nextVal.actions).not.toBe(initialVal.actions);

    rerender(
      <Theme theme={themes[defaultThemeSlug]}>
        <WorkspaceProvider initialValue={nextVal}>
          <SkillsDialog />
        </WorkspaceProvider>
      </Theme>,
    );

    expect(refreshInstalledSkills).toHaveBeenCalledTimes(1);
  });
});
