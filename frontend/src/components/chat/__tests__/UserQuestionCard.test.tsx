import { Theme } from "@astryxdesign/core/theme";
import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { defaultThemeSlug, themes } from "../../../astryx-themes";
import type { PendingInterrupt } from "../../../api/types";
import {
  WorkspaceProvider,
  type WorkspaceActions,
  type WorkspaceContextValue,
  type WorkspaceState,
} from "../../../state/workspaceContext";
import { Composer } from "../Composer";

function makeMockValue(
  pendingInterrupt: PendingInterrupt | null,
  actionOverrides: Partial<WorkspaceActions> = {},
): WorkspaceContextValue {
  const baseSession = {
    id: "sess-q1",
    project_id: "proj-1",
    title: "Plan Clarification",
    workspace_path: "/repo",
    mode: "plan" as const,
    status: pendingInterrupt ? ("awaiting_approval" as const) : ("idle" as const),
    pending_interrupt: pendingInterrupt,
    created_at: "2026-11-23T00:00:00Z",
    updated_at: "2026-11-23T00:00:00Z",
  };
  const state: WorkspaceState = {
    settings: {
      baseUrl: "http://localhost:8001/v1",
      model: "test-model",
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
    mode: "plan",
    workspacePath: "/repo",
    workspaceInfo: { path: "/repo", name: "repo", is_git_repo: true, branch: "main" },
    workspaceError: null,
    artifacts: { artifacts: [], walkthrough_plan: null },
    diff: null,
    diffFocus: null,
    rightDrawerTab: null,
    activeArtifactTab: "implementation_plan",
    streamError: null,
    treeRevision: 0,
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
    saveArtifactToWorkspace: vi.fn().mockResolvedValue("/repo/plan.md"),
    ...actionOverrides,
  };
  return { state, actions, meta: { configured: true } };
}

function renderComposer(value: WorkspaceContextValue) {
  return render(
    <Theme theme={themes[defaultThemeSlug]}>
      <WorkspaceProvider initialValue={value}>
        <Composer />
      </WorkspaceProvider>
    </Theme>,
  );
}

describe("UserQuestionCard docked above Composer", () => {
  it("renders neutral Kbd keycaps, (Recommended) options, inline Other + actions, collapse toggle, and keyboard shortcuts", () => {
    const val = makeMockValue({
      kind: "user_question",
      summary: "어떤 저장소를 사용할까요?",
      questions: [
        {
          question: "어떤 저장소를 사용할까요?",
          options: [
            {
              label: "SQLite 로컬 저장",
              description: "별도 서버 없이 즉시 동작",
              recommended: true,
            },
            { label: "PostgreSQL 외부 DB" },
          ],
        },
      ],
    });
    const { container } = renderComposer(val);

    expect(screen.getByText("어떤 저장소를 사용할까요?")).toBeInTheDocument();
    // Neutral Kbd keycaps (1, 2, 3) rendered instead of colored Badges
    expect(screen.getByRole("img", { name: "1" })).toBeInTheDocument();
    expect(screen.getByRole("img", { name: "2" })).toBeInTheDocument();
    expect(screen.getByRole("img", { name: "3" })).toBeInTheDocument();
    expect(container.querySelector(".astryx-card")?.querySelectorAll("kbd")).toHaveLength(3);

    const recOption = screen.getByRole("button", {
      name: /\(Recommended\) SQLite 로컬 저장/i,
    });
    expect(recOption).toBeInTheDocument();

    // Submit is disabled until user picks an option or enters Other
    const submitBtn = screen.getByRole("button", { name: "Submit" });
    expect(submitBtn).toHaveAttribute("aria-disabled", "true");

    // Keyboard shortcut '1' via useHotkeys selects first option without turning Kbd into an info Badge
    fireEvent.keyDown(window, { key: "1" });
    expect(recOption).toHaveAttribute("aria-current", "true");
    expect(container.querySelector('[data-variant="info"]')).toBeNull();

    // Collapse & expand cleanly
    fireEvent.click(screen.getByRole("button", { name: "Collapse question" }));
    expect(
      screen.queryByRole("list", { name: "어떤 저장소를 사용할까요?" }),
    ).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Expand question" }));
    expect(
      screen.getByRole("list", { name: "어떤 저장소를 사용할까요?" }),
    ).toBeInTheDocument();

    // Pressing Enter while focused on Skip button does NOT hijack Enter into Submit
    const skipBtn = screen.getByRole("button", { name: "Skip" });
    fireEvent.keyDown(skipBtn, { key: "Enter" });
    expect(val.actions.resumeInterrupt).not.toHaveBeenCalled();

    // Pressing bare Enter on non-interactive window submits the selected option
    fireEvent.keyDown(window, { key: "Enter" });
    expect(val.actions.resumeInterrupt).toHaveBeenCalledWith("answer", undefined, [
      {
        question: "어떤 저장소를 사용할까요?",
        selected_option: "SQLite 로컬 저장",
        skipped: false,
      },
    ]);
  });

  it("supports multi-question 1/2 stepping, Back, and IME-safe Enter in Other input", () => {
    const val = makeMockValue({
      kind: "user_question",
      summary: "질문 1",
      questions: [
        {
          question: "질문 1: 테마 스타일은?",
          options: [
            { label: "다크 코드 카드", recommended: true },
            { label: "라이트 적응형" },
          ],
        },
        {
          question: "질문 2: 배포 대상은?",
          options: [
            { label: "로컬 WSL", recommended: true },
            { label: "Docker 컨테이너" },
          ],
        },
      ],
    });
    renderComposer(val);

    expect(screen.getByText("1 / 2")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: /다크 코드 카드/i }));
    fireEvent.click(screen.getByRole("button", { name: "Next" }));

    expect(screen.getByText("2 / 2")).toBeInTheDocument();
    expect(screen.getByText("질문 2: 배포 대상은?")).toBeInTheDocument();

    // Shortcut '3' focuses Other input
    const otherInput = screen.getByPlaceholderText("Other (write your answer)");
    fireEvent.keyDown(window, { key: "3" });
    expect(document.activeElement).toBe(otherInput);

    fireEvent.change(otherInput, { target: { value: "사내 Kubernetes 클러스터" } });

    // IME composing Enter (isComposing: true or keyCode: 229) must NOT prematurely submit
    fireEvent.keyDown(otherInput, { key: "Enter", isComposing: true, keyCode: 229 });
    expect(val.actions.resumeInterrupt).not.toHaveBeenCalled();

    // Committed Enter submits
    fireEvent.keyDown(otherInput, { key: "Enter", isComposing: false, keyCode: 13 });
    expect(val.actions.resumeInterrupt).toHaveBeenCalledWith("answer", undefined, [
      {
        question: "질문 1: 테마 스타일은?",
        selected_option: "다크 코드 카드",
        skipped: false,
      },
      {
        question: "질문 2: 배포 대상은?",
        custom_text: "사내 Kubernetes 클러스터",
        skipped: false,
      },
    ]);
  });

  it("calls resumeInterrupt(skip) when Skip or Dismiss (×) is clicked", () => {
    const val = makeMockValue({
      kind: "user_question",
      summary: "테스트 프레임워크는?",
      questions: [
        {
          question: "테스트 프레임워크는?",
          options: [
            { label: "pytest", recommended: true },
            { label: "unittest" },
          ],
        },
      ],
    });
    renderComposer(val);

    fireEvent.click(screen.getByRole("button", { name: "Dismiss question" }));
    expect(val.actions.resumeInterrupt).toHaveBeenCalledWith("skip", undefined, [
      { question: "테스트 프레임워크는?", skipped: true },
    ]);
  });
});
