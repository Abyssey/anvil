import { Theme } from "@astryxdesign/core/theme";
import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { defaultThemeSlug, themes } from "../../../astryx-themes";
import {
  WorkspaceProvider,
  type WorkspaceActions,
  type WorkspaceContextValue,
  type WorkspaceState,
} from "../../../state/workspaceContext";
import { DiffDrawer } from "../DiffDrawer";

function makeWorkspaceValue(overrides: Partial<WorkspaceState> = {}): WorkspaceContextValue {
  const baseSession = {
    id: "sess-1",
    project_id: "proj-1",
    title: "Session 1",
    workspace_path: "/repo-a",
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
    memoryOpen: false,
    installedSkills: [],
    pinnedSkills: [],
    projects: [],
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
    workspacePath: "/repo-a",
    workspaceInfo: { path: "/repo-a", name: "repo-a", is_git_repo: true, branch: "main" },
    workspaceError: null,
    artifacts: { artifacts: [], walkthrough_plan: null },
    diff: {
      workspace_path: "/repo-a",
      branch: "main",
      git_head: "abc123",
      files: [
        {
          path: "backend/app.py",
          status: "modified",
          hunks: [
            {
              name: "+1",
              path: "backend/app.py",
              header: "@@ -1,1 +1,1 @@",
              old_start: 1,
              old_count: 1,
              new_start: 1,
              new_count: 1,
              lines: ["-a = 1", "+a = 2"],
            },
          ],
        },
        {
          path: "frontend/App.tsx",
          status: "modified",
          hunks: [
            {
              name: "+1",
              path: "frontend/App.tsx",
              header: "@@ -1,1 +1,1 @@",
              old_start: 1,
              old_count: 1,
              new_start: 1,
              new_count: 1,
              lines: ["-x", "+y"],
            },
          ],
        },
      ],
    },
    diffFocus: null,
    rightDrawerTab: "diff",
    activeArtifactTab: "walkthrough",
    streamError: null,
    treeRevision: 0,
    ...overrides,
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
    saveArtifactToWorkspace: vi.fn().mockResolvedValue("/repo-a/docs/ONBOARDING.md"),
  };
  return {
    state,
    actions,
    meta: { configured: true },
  };
}

describe("DiffDrawer + Abyss theme", () => {
  it("uses abyss as the default Astryx theme", () => {
    expect(defaultThemeSlug).toBe("abyss");
    expect(themes.abyss).toBeDefined();
  });

  it("infers syntax language on CodeBlock and resets selectedPath on session/workspace switch", () => {
    const valA = makeWorkspaceValue();
    const { container, rerender } = render(
      <Theme theme={themes[defaultThemeSlug]}>
        <WorkspaceProvider initialValue={valA}>
          <DiffDrawer />
        </WorkspaceProvider>
      </Theme>,
    );

    const codeBlock = container.querySelector(".astryx-code-block");
    expect(codeBlock).toHaveAttribute("data-language", "python");

    fireEvent.click(screen.getByText("frontend/App.tsx"));
    expect(container.querySelector(".astryx-code-block")).toHaveAttribute("data-language", "tsx");

    // Switching workspace & session resets selectedPath so the new workspace's first file is shown
    const valB = makeWorkspaceValue({
      workspacePath: "/repo-b",
      activeSession: {
        ...valA.state.activeSession!,
        id: "sess-2",
        workspace_path: "/repo-b",
      },
      diff: {
        workspace_path: "/repo-b",
        branch: "main",
        git_head: "def456",
        files: [
          {
            path: "cmd/main.go",
            status: "modified",
            hunks: [
              {
                name: "+1",
                path: "cmd/main.go",
                header: "@@ -1,1 +1,1 @@",
                old_start: 1,
                old_count: 1,
                new_start: 1,
                new_count: 1,
                lines: ["-a", "+b"],
              },
            ],
          },
          {
            path: "frontend/App.tsx",
            status: "modified",
            hunks: [
              {
                name: "+1",
                path: "frontend/App.tsx",
                header: "@@ -1,1 +1,1 @@",
                old_start: 1,
                old_count: 1,
                new_start: 1,
                new_count: 1,
                lines: ["-c", "+d"],
              },
            ],
          },
        ],
      },
    });
    rerender(
      <Theme theme={themes[defaultThemeSlug]}>
        <WorkspaceProvider initialValue={valB}>
          <DiffDrawer />
        </WorkspaceProvider>
      </Theme>,
    );

    expect(container.querySelector(".astryx-code-block")).toHaveAttribute("data-language", "go");
  });
});
