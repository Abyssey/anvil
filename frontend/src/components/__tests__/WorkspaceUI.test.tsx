import { Theme } from "@astryxdesign/core/theme";
import { act, fireEvent, render, screen, within } from "@testing-library/react";
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
    skillsOpen: false,
    memoryOpen: false,
    installedSkills: [],
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

describe("Workspace UI (Astryx + HITL)", () => {
  it("supports switching modes (Ask / Plan / Auto) and opening Settings", () => {
    const val = makeMockValue();
    renderWorkspace(val);

    fireEvent.click(screen.getByRole("radio", { name: "Plan" }));
    expect(val.actions.setMode).toHaveBeenCalledWith("plan");

    fireEvent.click(screen.getByRole("button", { name: /LLM Settings/i }));
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

    expect(screen.getByRole("heading", { name: "Anvil Coding Agent" })).toBeInTheDocument();
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
    expect(screen.getByRole("separator", { name: /Resize projects sidebar/i })).toBeInTheDocument();
  });

  it("never renders a New messages button and shows Loading older messages indicator while paginating", () => {
    const val = makeMockValue({
      hasMoreMessages: true,
      isLoadingOlderMessages: true,
      messages: [
        {
          id: "m1",
          session_id: "sess-1",
          role: "user",
          content: "First visible turn",
          thinking: "",
          tool_calls: [],
          created_at: "2026-11-22T00:00:00Z",
        },
        {
          id: "m2",
          session_id: "sess-1",
          role: "assistant",
          content: "Latest reply",
          thinking: "",
          tool_calls: [],
          created_at: "2026-11-22T00:00:01Z",
        },
      ],
    });
    renderWorkspace(val);

    expect(screen.queryByRole("button", { name: /new messages/i })).not.toBeInTheDocument();
    expect(screen.getByText("Loading older messages...")).toBeInTheDocument();
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
    const codeBlock = document.querySelector(".astryx-code-block");
    expect(codeBlock).toHaveAttribute("data-language", "python");
    expect(codeBlock).toHaveTextContent("print('hello')");

    fireEvent.click(backBtn);
    expect(screen.getByPlaceholderText(/Filter files/i)).toBeInTheDocument();
  });

  it("opens Skills and Memory dialogs from header and renders dismissible pinned skill tokens in Composer", () => {
    const val = makeMockValue({ pinnedSkills: ["react-patterns"] });
    renderWorkspace(val);

    fireEvent.click(screen.getByRole("button", { name: /Skills/i }));
    expect(val.actions.openSkills).toHaveBeenCalledTimes(1);

    fireEvent.click(screen.getByRole("button", { name: "Memory" }));
    expect(val.actions.openMemory).toHaveBeenCalledTimes(1);

    expect(screen.getByText("/react-patterns")).toBeInTheDocument();
    const removeTokenBtn = screen.getByRole("button", { name: /Remove \/react-patterns/i });
    fireEvent.click(removeTokenBtn);
    expect(val.actions.unpinSkill).toHaveBeenCalledWith("react-patterns");
  });

  it("opens AddProjectDialog at parent directory and shows Already added / Open Existing Project for registered paths", async () => {
    const { apiClient } = await import("../../api/client");
    const browseSpy = vi.spyOn(apiClient, "browseDirectories").mockImplementation(async (path) => {
      if (path === "/work/repo-a") {
        return {
          current_path: "/work/repo-a",
          parent_path: "/work",
          is_git_repo: true,
          branch: "main",
          directories: [],
        };
      }
      return {
        current_path: "/work",
        parent_path: "/",
        is_git_repo: false,
        branch: "",
        directories: [
          { name: "repo-a", path: "/work/repo-a", is_git_repo: true },
          { name: "repo-b", path: "/work/repo-b", is_git_repo: true },
        ],
      };
    });

    const val = makeMockValue({
      addProjectOpen: true,
      workspacePath: "/work/repo-a",
      projects: [
        {
          id: "proj-a",
          name: "repo-a",
          path: "/work/repo-a",
          is_git_repo: true,
          branch: "main",
          created_at: "2026-11-22T00:00:00Z",
          updated_at: "2026-11-22T00:00:00Z",
        },
      ],
      activeProjectId: "proj-a",
    });
    renderWorkspace(val);

    await screen.findByText("repo-b");
    expect(browseSpy).toHaveBeenCalledWith("/work");
    const dialog = screen.getByRole("dialog");
    expect(within(dialog).getByRole("button", { name: "Add Project" })).toBeInTheDocument();

    fireEvent.click(within(dialog).getByText("repo-a"));
    await within(dialog).findByText("Already added");
    const openExistingBtn = within(dialog).getByRole("button", { name: "Open Existing Project" });
    fireEvent.click(openExistingBtn);

    expect(val.actions.selectProject).toHaveBeenCalledWith("proj-a");
    browseSpy.mockRestore();
  });

  it("renders per-turn Worked for durations and ticks live Working · timer while streaming", () => {
    vi.useFakeTimers();
    try {
      vi.setSystemTime(new Date("2026-11-22T00:05:10.000Z"));
      const val = makeMockValue({
        isStreaming: true,
        turnDurationsMs: { a1: 74_000 },
        messages: [
          {
            id: "u1",
            session_id: "sess-1",
            role: "user",
            content: "Check backend",
            thinking: "",
            tool_calls: [],
            created_at: "2026-11-22T00:00:00.000Z",
          },
          {
            id: "a1",
            session_id: "sess-1",
            role: "assistant",
            content: "Backend checked.",
            thinking: "",
            tool_calls: [],
            created_at: "2026-11-22T00:00:05.000Z",
          },
          {
            id: "u2",
            session_id: "sess-1",
            role: "user",
            content: "Check frontend",
            thinking: "",
            tool_calls: [],
            created_at: "2026-11-22T00:01:00.000Z",
          },
          {
            id: "a2",
            session_id: "sess-1",
            role: "assistant",
            content: "Frontend checked.",
            thinking: "",
            tool_calls: [],
            created_at: "2026-11-22T00:01:08.000Z",
          },
        ],
        streamingAssistant: {
          content: "",
          thinking: "",
          toolCalls: [
            {
              id: "tc-live-1",
              name: "read_file",
              args: { file_path: "/backend/app.py" },
              output: "",
              status: "running",
              subagent: null,
            },
          ],
          startedAtMs: Date.now() - 3_000,
          textSplitAfterIndices: [],
          hasTrailingAssistantText: false,
        },
      });

      renderWorkspace(val);

      // Recorded duration overrides timestamp diff for a1; timestamp diff used for a2
      expect(screen.getByText("Worked for 1m 14s")).toBeInTheDocument();
      expect(screen.getByText("Worked for 8s")).toBeInTheDocument();

      // Live streaming timer starts at 3s and ticks to 4s after 1s
      expect(screen.getByText("Working · 3s")).toBeInTheDocument();
      expect(screen.getByText("app.py 읽는 중...")).toBeInTheDocument();
      expect(screen.queryByText("read_file")).not.toBeInTheDocument();

      act(() => {
        vi.advanceTimersByTime(1_000);
      });
      expect(screen.getByText("Working · 4s")).toBeInTheDocument();
    } finally {
      vi.useRealTimers();
    }
  });

  it("auto-collapses completed ExplorationGroups and preserves manual toggle state across re-renders", () => {
    const messages = [
      {
        id: "u1",
        session_id: "sess-1",
        role: "user" as const,
        content: "Inspect repo and edit app.py",
        thinking: "",
        tool_calls: [],
        created_at: "2026-11-22T00:00:00.000Z",
      },
      {
        id: "a1",
        session_id: "sess-1",
        role: "assistant" as const,
        content: "Done inspecting and editing.",
        thinking: "",
        tool_calls: [
          {
            id: "tc-1",
            name: "ls",
            args: { path: "/backend" },
            output: "app.py\nmain.py",
            status: "success",
            subagent: null,
          },
          {
            id: "tc-2",
            name: "read_file",
            args: { file_path: "/backend/app.py" },
            output: "print('hi')",
            status: "success",
            subagent: null,
          },
          {
            id: "tc-3",
            name: "edit_file",
            args: { file_path: "/backend/app.py" },
            output: "Updated",
            status: "success",
            subagent: null,
          },
        ],
        created_at: "2026-11-22T00:00:06.000Z",
      },
    ];

    const initialVal = makeMockValue({
      messages,
      isStreaming: true,
      streamingAssistant: {
        content: "Follow-up token 1",
        thinking: "",
        toolCalls: [
          {
            id: "st-1",
            name: "grep",
            args: { pattern: "WorkspaceProvider", path: "/frontend" },
            output: "workspaceContext.tsx:175",
            status: "success",
            subagent: null,
          },
          {
            id: "st-2",
            name: "execute",
            args: { command: "npm test" },
            output: "",
            status: "running",
            subagent: null,
          },
        ],
        startedAtMs: Date.now(),
        textSplitAfterIndices: [],
        hasTrailingAssistantText: false,
      },
    });
    const { rerender } = renderWorkspace(initialVal);

    // Non-read tool renders individually immediately, while read exploration groups start collapsed
    expect(screen.getByText("edit_file")).toBeInTheDocument();
    expect(screen.getByText("execute")).toBeInTheDocument();
    const persistedTrigger = screen.getByRole("button", {
      name: "폴더 1개 탐색 · 파일 1개 읽음",
    });
    const streamingTrigger = screen.getByRole("button", {
      name: "검색 1회",
    });
    expect(persistedTrigger).toHaveAttribute("aria-expanded", "false");
    expect(streamingTrigger).toHaveAttribute("aria-expanded", "false");
    expect(screen.queryByText("read_file")).not.toBeInTheDocument();
    expect(screen.queryByText("grep")).not.toBeInTheDocument();

    // User manually expands both persisted and streaming-draft exploration groups
    fireEvent.click(persistedTrigger);
    fireEvent.click(streamingTrigger);
    expect(persistedTrigger).toHaveAttribute("aria-expanded", "true");
    expect(streamingTrigger).toHaveAttribute("aria-expanded", "true");
    expect(screen.getByText("read_file")).toBeInTheDocument();
    expect(screen.getByText("ls")).toBeInTheDocument();
    expect(screen.getByText("grep")).toBeInTheDocument();

    // Subsequent SSE token + new tool call triggers re-render; manual expanded state MUST persist
    const updatedVal = makeMockValue({
      messages,
      isStreaming: true,
      streamingAssistant: {
        content: "Follow-up token 1 and token 2",
        thinking: "",
        toolCalls: [
          {
            id: "st-1",
            name: "grep",
            args: { pattern: "WorkspaceProvider", path: "/frontend" },
            output: "workspaceContext.tsx:175",
            status: "success",
            subagent: null,
          },
          {
            id: "st-2",
            name: "execute",
            args: { command: "npm test" },
            output: "PASS",
            status: "success",
            subagent: null,
          },
          {
            id: "st-3",
            name: "read_file",
            args: { file_path: "/frontend/src/App.tsx" },
            output: "",
            status: "running",
            subagent: null,
          },
        ],
        startedAtMs: initialVal.state.streamingAssistant?.startedAtMs ?? Date.now(),
        textSplitAfterIndices: [],
        hasTrailingAssistantText: false,
      },
    });
    rerender(
      <Theme theme={themes[defaultThemeSlug]}>
        <WorkspaceProvider initialValue={updatedVal}>
          <WorkspaceShell />
          <SettingsDialog />
          <AddProjectDialog />
        </WorkspaceProvider>
      </Theme>,
    );

    expect(
      screen.getByRole("button", { name: "폴더 1개 탐색 · 파일 1개 읽음" }),
    ).toHaveAttribute("aria-expanded", "true");
    expect(screen.getByRole("button", { name: "검색 1회" })).toHaveAttribute(
      "aria-expanded",
      "true",
    );
    expect(screen.getByText("read_file")).toBeInTheDocument();
    expect(screen.getByText("grep")).toBeInTheDocument();
    expect(screen.getByText("App.tsx 읽는 중...")).toBeInTheDocument();
  });

  it("renders Verified stamp and Revert action on assistant turns, confirms via AlertDialog, and restores rewound prompt to Composer", async () => {
    const revertFromMessage = vi.fn().mockResolvedValue(undefined);
    const initialVal = makeMockValue(
      {
        messages: [
          {
            id: "u-turn-1",
            session_id: "sess-1",
            role: "user",
            content: "Refactor parser and run pytest",
            thinking: "",
            tool_calls: [],
            created_at: "2026-11-23T01:00:00Z",
          },
          {
            id: "a-turn-1",
            session_id: "sess-1",
            role: "assistant",
            content: "Refactored parser and verified tests.",
            thinking: "",
            tool_calls: [],
            checkpoint_id: "cp-111",
            changed_files: ["backend/parser.py", "tests/test_parser.py"],
            verification_stamp: "passed",
            created_at: "2026-11-23T01:00:08Z",
          },
        ],
      },
      { revertFromMessage },
    );

    const { rerender } = renderWorkspace(initialVal);

    expect(screen.getByText("Verified")).toBeInTheDocument();
    const revertBtn = screen.getByRole("button", { name: "Revert (2)" });
    fireEvent.click(revertBtn);

    const alertDialog = screen.getByRole("alertdialog");
    expect(alertDialog).toHaveTextContent("Revert to before this turn?");
    expect(alertDialog).toHaveTextContent("backend/parser.py, tests/test_parser.py");

    fireEvent.click(within(alertDialog).getByRole("button", { name: "Revert Turn" }));
    await act(async () => {
      await Promise.resolve();
    });
    expect(revertFromMessage).toHaveBeenCalledWith("a-turn-1");

    // Simulate post-revert state where messages are empty and rewoundDraft populates Composer
    const rewoundVal = makeMockValue({
      messages: [],
      rewoundDraft: { text: "Refactor parser and run pytest", seq: 1 },
    });
    rerender(
      <Theme theme={themes[defaultThemeSlug]}>
        <WorkspaceProvider initialValue={rewoundVal}>
          <WorkspaceShell />
          <SettingsDialog />
          <AddProjectDialog />
        </WorkspaceProvider>
      </Theme>,
    );

    expect(screen.getByRole("textbox", { name: "Message input" })).toHaveTextContent(
      "Refactor parser and run pytest",
    );
  });
});
