import {
  createContext,
  use,
  useCallback,
  useEffect,
  useRef,
  useState,
  type ReactNode,
} from "react";
import { ApiError, apiClient } from "../api/client";
import type {
  ArtifactBundle,
  ArtifactDoc,
  ArtifactKind,
  ChatMessage,
  InstalledSkill,
  PendingInterrupt,
  Project,
  QuestionAnswer,
  QueuedPrompt,
  RunMode,
  Session,
  StreamEvent,
  ToolCallRecord,
  UserQuestionItem,
  WorkspaceDiff,
  WorkspaceInfo,
} from "../api/types";
import {
  clearLLMSettings,
  isLLMConfigured,
  loadLLMSettings,
  saveLLMSettings,
  type LLMSettings,
} from "./settings";

export type RightDrawerTab = "artifacts" | "files" | "diff" | null;

export interface StreamingAssistantDraft {
  content: string;
  thinking: string;
  toolCalls: ToolCallRecord[];
  startedAtMs: number;
  textSplitAfterIndices?: number[];
  hasTrailingAssistantText?: boolean;
}

export interface WorkspaceState {
  settings: LLMSettings;
  settingsOpen: boolean;
  addProjectOpen: boolean;
  skillsOpen: boolean;
  memoryOpen: boolean;
  installedSkills: InstalledSkill[];
  pinnedSkills: string[];
  projects: Project[];
  activeProjectId: string | null;
  sessions: Session[];
  activeSession: Session | null;
  messages: ChatMessage[];
  turnDurationsMs: Record<string, number>;
  hasMoreMessages: boolean;
  isLoadingOlderMessages: boolean;
  streamingAssistant: StreamingAssistantDraft | null;
  isStreaming: boolean;
  mode: RunMode;
  workspacePath: string;
  workspaceInfo: WorkspaceInfo | null;
  workspaceError: string | null;
  artifacts: ArtifactBundle;
  diff: WorkspaceDiff | null;
  diffFocus: { path: string; hunkName?: string } | null;
  rightDrawerTab: RightDrawerTab;
  activeArtifactTab: ArtifactKind;
  streamError: string | null;
  treeRevision: number;
  rewoundDraft?: { text: string; seq: number } | null;
  queuedMessages?: QueuedPrompt[];
  isQueuePaused?: boolean;
}

export type ResumeDecision = "approve" | "revise" | "deny" | "answer" | "skip";

export interface WorkspaceActions {
  openSettings: () => void;
  closeSettings: () => void;
  openAddProject: () => void;
  closeAddProject: () => void;
  openSkills: () => void;
  closeSkills: () => void;
  openMemory: () => void;
  closeMemory: () => void;
  refreshInstalledSkills: () => Promise<void>;
  pinSkill: (name: string) => void;
  unpinSkill: (name: string) => void;
  clearPinnedSkills: () => void;
  updateSettings: (next: LLMSettings) => void;
  clearSettings: () => void;
  addProject: (path: string) => Promise<void>;
  selectProject: (projectId: string) => Promise<void>;
  removeProject: (projectId: string) => Promise<void>;
  createNewSession: (projectId?: string) => Promise<void>;
  selectSession: (sessionId: string) => Promise<void>;
  removeSession: (sessionId: string) => Promise<void>;
  setWorkspacePath: (path: string) => Promise<void>;
  setMode: (mode: RunMode) => Promise<void>;
  sendMessage: (content: string) => Promise<void>;
  enqueueMessage?: (content: string) => Promise<void>;
  steerMessage?: (content: string, explicitPinnedSkills?: string[]) => Promise<void>;
  steerQueuedMessage?: (queueId: string) => Promise<void>;
  sendQueuedMessageNow?: (queueId: string) => Promise<void>;
  editQueuedMessage?: (queueId: string) => void;
  removeQueuedMessage?: (queueId: string) => void;
  loadOlderMessages: () => Promise<void>;
  stopStream: () => void;
  resumeInterrupt: (
    decision: ResumeDecision,
    feedback?: string,
    answers?: QuestionAnswer[],
  ) => Promise<void>;
  openRightDrawer: (tab: RightDrawerTab) => void;
  setActiveArtifactTab: (kind: ArtifactKind) => void;
  jumpToDiffHunk: (path: string, hunkName?: string) => Promise<void>;
  refreshDiff: () => Promise<void>;
  revertFromMessage: (messageId: string) => Promise<void>;
  revertDiffFiles: (files?: string[]) => Promise<void>;
  saveArtifactToWorkspace: (kind: ArtifactKind, relativePath: string) => Promise<string>;
}

export interface WorkspaceMeta {
  configured: boolean;
}

export interface WorkspaceContextValue {
  state: WorkspaceState;
  actions: WorkspaceActions;
  meta: WorkspaceMeta;
}

const WorkspaceContext = createContext<WorkspaceContextValue | null>(null);

const EMPTY_ARTIFACTS: ArtifactBundle = { artifacts: [], walkthrough_plan: null };

export function useWorkspace(): WorkspaceContextValue {
  const ctx = use(WorkspaceContext);
  if (!ctx) {
    throw new Error("useWorkspace must be used inside WorkspaceProvider");
  }
  return ctx;
}

function upsertArtifactDoc(bundle: ArtifactBundle, doc: ArtifactDoc): ArtifactBundle {
  const remaining = bundle.artifacts.filter((a) => a.kind !== doc.kind);
  return { ...bundle, artifacts: [...remaining, doc] };
}

function sessionsMatchingProject(
  list: Session[],
  project: Pick<Project, "id" | "path">,
): Session[] {
  return list.filter(
    (s) => s.project_id === project.id || (!s.project_id && s.workspace_path === project.path),
  );
}

function formatOptimisticQaSummary(
  questions: readonly UserQuestionItem[],
  decision: ResumeDecision,
  answers: readonly QuestionAnswer[],
): string {
  if (decision === "skip" && answers.length === 0) {
    if (questions.length === 1) {
      return `Q. ${questions[0].question}\nA. (건너뜀 — 추천안으로 진행)`;
    }
    return [
      "질문 건너뜀 (추천안으로 진행)",
      ...questions.map((q, i) => `${i + 1}. ${q.question}`),
    ].join("\n");
  }

  const pairs = questions.map((item, idx) => {
    const ans =
      answers[idx] ??
      answers.find((candidate) => candidate.question.trim() === item.question.trim());
    const text =
      !ans || ans.skipped
        ? "(건너뜀 — 추천안으로 진행)"
        : (ans.custom_text?.trim() || ans.selected_option?.trim() || "(건너뜀 — 추천안으로 진행)");
    return { q: item.question, a: text };
  });

  if (pairs.length === 1) {
    return `Q. ${pairs[0].q}\nA. ${pairs[0].a}`;
  }
  return pairs.map((p, idx) => `${idx + 1}. Q. ${p.q}\n   A. ${p.a}`).join("\n");
}

function projectForSession(projects: Project[], session: Session): Project | undefined {
  if (session.project_id) {
    return projects.find((p) => p.id === session.project_id);
  }
  return projects.find((p) => p.path === session.workspace_path);
}

function parseLeadingSkillMentions(
  input: string,
  installed: InstalledSkill[],
): { cleanedContent: string; mentionedSkills: string[] } {
  const installedNames = new Set(installed.map((s) => s.name));
  const mentionedSkills: string[] = [];
  let remaining = input.trim();
  while (remaining.startsWith("/")) {
    const match = /^\/([a-z0-9][a-z0-9-]{0,63})(?:\s+(.*))?$/s.exec(remaining);
    if (!match) break;
    const candidate = match[1];
    if (!installedNames.has(candidate)) break;
    if (!mentionedSkills.includes(candidate)) {
      mentionedSkills.push(candidate);
    }
    remaining = (match[2] ?? "").trim();
  }
  return {
    cleanedContent: remaining || input.trim(),
    mentionedSkills,
  };
}

export function WorkspaceProvider({
  children,
  initialValue,
}: {
  children: ReactNode;
  initialValue?: WorkspaceContextValue;
}) {
  if (initialValue) {
    return <WorkspaceContext value={initialValue}>{children}</WorkspaceContext>;
  }

  return <LiveWorkspaceProvider>{children}</LiveWorkspaceProvider>;
}

function LiveWorkspaceProvider({ children }: { children: ReactNode }) {
  const [settings, setSettings] = useState<LLMSettings>(() => loadLLMSettings());
  const [settingsOpen, setSettingsOpen] = useState(false);
  const [addProjectOpen, setAddProjectOpen] = useState(false);
  const [skillsOpen, setSkillsOpen] = useState(false);
  const [memoryOpen, setMemoryOpen] = useState(false);
  const [installedSkills, setInstalledSkills] = useState<InstalledSkill[]>([]);
  const [pinnedSkills, setPinnedSkills] = useState<string[]>([]);
  const [projects, setProjects] = useState<Project[]>([]);
  const [activeProjectId, setActiveProjectId] = useState<string | null>(null);
  const [sessions, setSessions] = useState<Session[]>([]);
  const [activeSession, setActiveSession] = useState<Session | null>(null);
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [turnDurationsMs, setTurnDurationsMs] = useState<Record<string, number>>({});
  const [hasMoreMessages, setHasMoreMessages] = useState(false);
  const [isLoadingOlderMessages, setIsLoadingOlderMessages] = useState(false);
  const [streamingAssistant, setStreamingAssistant] =
    useState<StreamingAssistantDraft | null>(null);
  const [isStreaming, setIsStreaming] = useState(false);
  const [mode, setModeState] = useState<RunMode>("ask");
  const [workspacePath, setWorkspacePathState] = useState("");
  const [workspaceInfo, setWorkspaceInfo] = useState<WorkspaceInfo | null>(null);
  const [workspaceError, setWorkspaceError] = useState<string | null>(null);
  const [artifacts, setArtifacts] = useState<ArtifactBundle>(EMPTY_ARTIFACTS);
  const [diff, setDiff] = useState<WorkspaceDiff | null>(null);
  const [diffFocus, setDiffFocus] = useState<{ path: string; hunkName?: string } | null>(null);
  const [rightDrawerTab, setRightDrawerTab] = useState<RightDrawerTab>("files");
  const [activeArtifactTab, setActiveArtifactTab] =
    useState<ArtifactKind>("walkthrough");
  const [streamError, setStreamError] = useState<string | null>(null);
  const [treeRevision, setTreeRevision] = useState(0);
  const [rewoundDraft, setRewoundDraft] = useState<{ text: string; seq: number } | null>(null);
  const [queuedBySession, setQueuedBySession] = useState<Record<string, QueuedPrompt[]>>({});
  const [queuePausedBySession, setQueuePausedBySession] = useState<Record<string, boolean>>({});

  const abortRef = useRef<AbortController | null>(null);
  const manualStopRef = useRef(false);
  const loadingOlderRef = useRef(false);
  const loadSeqRef = useRef(0);
  const skillsSeqRef = useRef(0);
  const activeSessionRef = useRef<Session | null>(null);
  const isStreamingRef = useRef(false);
  const messagesRef = useRef<ChatMessage[]>([]);
  const hasMoreMessagesRef = useRef(false);
  const workspacePathRef = useRef(workspacePath);
  const installedSkillsRef = useRef<InstalledSkill[]>([]);
  const pinnedSkillsRef = useRef<string[]>([]);
  const queuedBySessionRef = useRef<Record<string, QueuedPrompt[]>>({});
  const queuePausedRef = useRef<Record<string, boolean>>({});
  const dispatchQueuedTurnRef = useRef<
    ((session: Session, rawContent: string, explicitPins?: string[]) => Promise<void>) | null
  >(null);

  activeSessionRef.current = activeSession;
  isStreamingRef.current = isStreaming;
  messagesRef.current = messages;
  hasMoreMessagesRef.current = hasMoreMessages;
  workspacePathRef.current = workspacePath;
  installedSkillsRef.current = installedSkills;
  pinnedSkillsRef.current = pinnedSkills;
  queuedBySessionRef.current = queuedBySession;
  queuePausedRef.current = queuePausedBySession;

  const updateSessionQueue = useCallback(
    (sessionId: string, updater: (prev: QueuedPrompt[]) => QueuedPrompt[]) => {
      const current = queuedBySessionRef.current[sessionId] ?? [];
      const next = updater(current);
      queuedBySessionRef.current = { ...queuedBySessionRef.current, [sessionId]: next };
      setQueuedBySession((prev) => ({ ...prev, [sessionId]: next }));
    },
    [],
  );

  const setSessionQueuePaused = useCallback((sessionId: string, paused: boolean) => {
    queuePausedRef.current = { ...queuePausedRef.current, [sessionId]: paused };
    setQueuePausedBySession((prev) => ({ ...prev, [sessionId]: paused }));
  }, []);

  const resetActiveSessionView = useCallback(() => {
    abortRef.current?.abort();
    abortRef.current = null;
    loadSeqRef.current += 1;
    loadingOlderRef.current = false;
    activeSessionRef.current = null;
    setActiveSession(null);
    setMessages([]);
    setTurnDurationsMs({});
    setStreamingAssistant(null);
    setIsStreaming(false);
    setArtifacts(EMPTY_ARTIFACTS);
    setDiff(null);
    setDiffFocus(null);
    setStreamError(null);
    setHasMoreMessages(false);
    setIsLoadingOlderMessages(false);
    setPinnedSkills([]);
    setRewoundDraft(null);
  }, []);

  const refreshInstalledSkills = useCallback(async () => {
    const seq = ++skillsSeqRef.current;
    const currentPath = workspacePathRef.current;
    try {
      const list = await apiClient.listInstalledSkills(currentPath || undefined);
      if (seq !== skillsSeqRef.current) return;
      setInstalledSkills(Array.isArray(list) ? list : []);
    } catch {
      if (seq !== skillsSeqRef.current) return;
      setInstalledSkills([]);
    }
  }, []);

  useEffect(() => {
    void refreshInstalledSkills();
  }, [workspacePath, refreshInstalledSkills]);

  const loadSessionDetail = useCallback(
    async (sessionId: string, knownProjects?: Project[]) => {
      const loadSeq = ++loadSeqRef.current;
      loadingOlderRef.current = false;
      setIsLoadingOlderMessages(false);
      const [detail, artBundle] = await Promise.all([
        apiClient.getSession(sessionId, 20),
        apiClient.listArtifacts(sessionId),
      ]);
      if (loadSeq !== loadSeqRef.current) return;
      activeSessionRef.current = detail.session;
      setActiveSession(detail.session);
      setMessages(detail.messages);
      setHasMoreMessages(Boolean(detail.has_more_messages));
      setModeState(detail.session.mode);
      setWorkspacePathState(detail.session.workspace_path);
      setArtifacts(artBundle);
      setWorkspaceError(null);

      const pool = knownProjects ?? projects;
      const matched = projectForSession(pool, detail.session);
      if (matched) {
        setActiveProjectId(matched.id);
      }

      try {
        const [info, diffData] = await Promise.all([
          apiClient.validateWorkspace(detail.session.workspace_path),
          apiClient.getWorkspaceDiff(detail.session.workspace_path),
        ]);
        if (loadSeq !== loadSeqRef.current) return;
        setWorkspaceInfo(info);
        setDiff(diffData);
      } catch (err) {
        if (loadSeq !== loadSeqRef.current) return;
        setWorkspaceInfo(null);
        setWorkspaceError(err instanceof Error ? err.message : "Invalid workspace");
      }
    },
    [projects],
  );

  useEffect(() => {
    let active = true;
    void (async () => {
      try {
        const [projList, sessionList] = await Promise.all([
          apiClient.listProjects(),
          apiClient.listSessions(),
        ]);
        if (!active) return;
        setProjects(projList);
        if (sessionList.length > 0) {
          setSessions(sessionList);
          const first = sessionList[0];
          const matched = projectForSession(projList, first) ?? projList[0] ?? null;
          if (matched) {
            setActiveProjectId(matched.id);
          }
          await loadSessionDetail(first.id, projList);
        } else if (projList.length > 0) {
          const defaultProject = projList[0];
          setActiveProjectId(defaultProject.id);
          const created = await apiClient.createSession(
            defaultProject.path,
            "ask",
            defaultProject.id,
          );
          if (!active) return;
          setSessions([created]);
          await loadSessionDetail(created.id, projList);
        } else {
          const defaultWs = await apiClient.getDefaultWorkspace();
          if (!active) return;
          const createdProj = await apiClient.createProject(defaultWs.path);
          const createdSess = await apiClient.createSession(
            createdProj.path,
            "ask",
            createdProj.id,
          );
          if (!active) return;
          setProjects([createdProj]);
          setActiveProjectId(createdProj.id);
          setSessions([createdSess]);
          await loadSessionDetail(createdSess.id, [createdProj]);
        }
      } catch (err) {
        if (active) {
          setStreamError(err instanceof Error ? err.message : "Failed to initialize workspace");
        }
      }
    })();
    return () => {
      active = false;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const openSettings = useCallback(() => setSettingsOpen(true), []);
  const closeSettings = useCallback(() => setSettingsOpen(false), []);
  const openAddProject = useCallback(() => setAddProjectOpen(true), []);
  const closeAddProject = useCallback(() => setAddProjectOpen(false), []);
  const openSkills = useCallback(() => setSkillsOpen(true), []);
  const closeSkills = useCallback(() => setSkillsOpen(false), []);
  const openMemory = useCallback(() => setMemoryOpen(true), []);
  const closeMemory = useCallback(() => setMemoryOpen(false), []);

  const pinSkill = useCallback((name: string) => {
    const trimmed = name.trim();
    if (!trimmed) return;
    setPinnedSkills((prev) => (prev.includes(trimmed) ? prev : [...prev, trimmed]));
  }, []);

  const unpinSkill = useCallback((name: string) => {
    setPinnedSkills((prev) => prev.filter((item) => item !== name));
  }, []);

  const clearPinnedSkills = useCallback(() => {
    setPinnedSkills([]);
  }, []);

  const updateSettings = useCallback((next: LLMSettings) => {
    const saved = saveLLMSettings(next);
    setSettings(saved);
    setStreamError(null);
  }, []);

  const clearSettings = useCallback(() => {
    setSettings(clearLLMSettings());
  }, []);

  const createNewSession = useCallback(
    async (projectId?: string) => {
      resetActiveSessionView();
      const targetProj =
        (projectId ? projects.find((p) => p.id === projectId) : undefined) ??
        projects.find((p) => p.id === activeProjectId) ??
        projects[0];
      const created = await apiClient.createSession(
        targetProj?.path ?? (workspacePath || undefined),
        mode,
        targetProj?.id,
      );
      if (targetProj) {
        setActiveProjectId(targetProj.id);
        setWorkspacePathState(targetProj.path);
      }
      setSessions((prev) => [created, ...prev]);
      await loadSessionDetail(created.id);
    },
    [resetActiveSessionView, projects, activeProjectId, workspacePath, mode, loadSessionDetail],
  );

  const selectProject = useCallback(
    async (projectId: string) => {
      const targetProj = projects.find((p) => p.id === projectId);
      if (!targetProj) return;
      if (
        targetProj.id === activeProjectId &&
        activeSessionRef.current &&
        (activeSessionRef.current.project_id === targetProj.id ||
          (!activeSessionRef.current.project_id &&
            activeSessionRef.current.workspace_path === targetProj.path))
      ) {
        return;
      }
      resetActiveSessionView();
      setActiveProjectId(targetProj.id);
      setWorkspacePathState(targetProj.path);
      setRightDrawerTab("files");
      setTreeRevision((n) => n + 1);

      const projSessions = sessionsMatchingProject(sessions, targetProj);
      if (projSessions.length > 0) {
        await loadSessionDetail(projSessions[0].id);
      } else {
        const created = await apiClient.createSession(targetProj.path, mode, targetProj.id);
        setSessions((prev) => [created, ...prev]);
        await loadSessionDetail(created.id);
      }
    },
    [projects, activeProjectId, resetActiveSessionView, sessions, mode, loadSessionDetail],
  );

  const addProject = useCallback(
    async (path: string) => {
      setWorkspaceError(null);
      resetActiveSessionView();
      const createdProj = await apiClient.createProject(path);
      const nextProjects = [
        createdProj,
        ...projects.filter((p) => p.id !== createdProj.id),
      ];
      setProjects(nextProjects);
      setActiveProjectId(createdProj.id);
      setWorkspacePathState(createdProj.path);
      setRightDrawerTab("files");
      setTreeRevision((n) => n + 1);

      const existingSessions = sessionsMatchingProject(sessions, createdProj);
      if (existingSessions.length > 0) {
        await loadSessionDetail(existingSessions[0].id, nextProjects);
      } else {
        const createdSess = await apiClient.createSession(
          createdProj.path,
          mode,
          createdProj.id,
        );
        setSessions((prev) => [createdSess, ...prev]);
        await loadSessionDetail(createdSess.id, nextProjects);
      }
    },
    [resetActiveSessionView, projects, sessions, mode, loadSessionDetail],
  );

  const removeProject = useCallback(
    async (projectId: string) => {
      const targetProj = projects.find((p) => p.id === projectId);
      const isRemovingActive =
        activeProjectId === projectId ||
        activeSession?.project_id === projectId ||
        (Boolean(targetProj) &&
          !activeSession?.project_id &&
          activeSession?.workspace_path === targetProj?.path);
      if (isRemovingActive) {
        resetActiveSessionView();
      }
      await apiClient.deleteProject(projectId);
      const remainingProjects = projects.filter((p) => p.id !== projectId);
      const remainingSessions = sessions.filter(
        (s) =>
          s.project_id !== projectId &&
          !(targetProj && !s.project_id && s.workspace_path === targetProj.path),
      );
      setProjects(remainingProjects);
      setSessions(remainingSessions);

      if (isRemovingActive) {
        if (remainingProjects.length > 0) {
          const nextProj = remainingProjects[0];
          setActiveProjectId(nextProj.id);
          setWorkspacePathState(nextProj.path);
          const nextProjSessions = sessionsMatchingProject(remainingSessions, nextProj);
          if (nextProjSessions.length > 0) {
            await loadSessionDetail(nextProjSessions[0].id, remainingProjects);
          } else {
            const created = await apiClient.createSession(nextProj.path, mode, nextProj.id);
            setSessions([created, ...remainingSessions]);
            await loadSessionDetail(created.id, remainingProjects);
          }
        } else {
          const refreshed = await apiClient.listProjects();
          setProjects(refreshed);
          const fallbackProj = refreshed[0];
          if (fallbackProj) {
            setActiveProjectId(fallbackProj.id);
            setWorkspacePathState(fallbackProj.path);
            const created = await apiClient.createSession(
              fallbackProj.path,
              mode,
              fallbackProj.id,
            );
            setSessions([created]);
            await loadSessionDetail(created.id, refreshed);
          }
        }
      }
    },
    [
      projects,
      sessions,
      activeProjectId,
      activeSession,
      resetActiveSessionView,
      mode,
      loadSessionDetail,
    ],
  );

  const selectSession = useCallback(
    async (sessionId: string) => {
      if (activeSessionRef.current?.id === sessionId) {
        setStreamError(null);
        return;
      }
      resetActiveSessionView();
      await loadSessionDetail(sessionId);
    },
    [resetActiveSessionView, loadSessionDetail],
  );

  const removeSession = useCallback(
    async (sessionId: string) => {
      const isRemovingActive = activeSession?.id === sessionId;
      if (isRemovingActive) {
        resetActiveSessionView();
      }
      await apiClient.deleteSession(sessionId);
      const remaining = sessions.filter((s) => s.id !== sessionId);
      setSessions(remaining);
      if (isRemovingActive) {
        const currentProj = projects.find((p) => p.id === activeProjectId) ?? projects[0];
        const sameProjectSessions = currentProj
          ? sessionsMatchingProject(remaining, currentProj)
          : [];
        if (sameProjectSessions.length > 0) {
          await loadSessionDetail(sameProjectSessions[0].id);
        } else if (remaining.length > 0) {
          await loadSessionDetail(remaining[0].id);
        } else {
          const created = await apiClient.createSession(
            currentProj?.path ?? (workspacePath || undefined),
            mode,
            currentProj?.id,
          );
          setSessions([created]);
          await loadSessionDetail(created.id);
        }
      }
    },
    [
      sessions,
      activeSession,
      resetActiveSessionView,
      activeProjectId,
      projects,
      workspacePath,
      mode,
      loadSessionDetail,
    ],
  );

  const setWorkspacePath = useCallback(
    async (nextPath: string) => {
      setWorkspaceError(null);
      try {
        await addProject(nextPath);
      } catch (err) {
        setWorkspaceError(err instanceof Error ? err.message : "Invalid workspace path");
      }
    },
    [addProject],
  );

  const setMode = useCallback(
    async (nextMode: RunMode) => {
      setModeState(nextMode);
      if (activeSession) {
        const updated = await apiClient.updateSession(activeSession.id, { mode: nextMode });
        if (activeSessionRef.current?.id === updated.id) {
          setActiveSession(updated);
        }
        setSessions((prev) => prev.map((s) => (s.id === updated.id ? updated : s)));
      }
    },
    [activeSession],
  );

  const runSSEStream = useCallback(
    async (url: string, body: Record<string, unknown>, targetSessionId: string, wsPath: string) => {
      const controller = new AbortController();
      abortRef.current = controller;
      manualStopRef.current = false;
      const startedAtMs = Date.now();
      let segmentStartedAtMs = startedAtMs;
      let sawInterrupt = false;
      let sawError = false;
      let wasAborted = false;
      const toolStartedAt = new Map<string, number>();
      const existingMessageIds = new Set(
        messagesRef.current
          .filter((m) => m.session_id === targetSessionId && !m.id.startsWith("local-"))
          .map((m) => m.id),
      );
      isStreamingRef.current = true;
      setIsStreaming(true);
      setStreamError(null);
      setStreamingAssistant({
        content: "",
        thinking: "",
        toolCalls: [],
        startedAtMs,
        textSplitAfterIndices: [],
        hasTrailingAssistantText: false,
      });

      try {
        await apiClient.streamSSE(url, body, settings, controller.signal, (ev: StreamEvent) => {
          if (activeSessionRef.current?.id !== targetSessionId) return;
          if (ev.event === "thinking") {
            const delta = String(ev.data.text ?? "");
            setStreamingAssistant((prev) =>
              prev ? { ...prev, thinking: prev.thinking + delta } : prev,
            );
          } else if (ev.event === "token") {
            const delta = String(ev.data.text ?? "");
            setStreamingAssistant((prev) => {
              if (!prev) return prev;
              const hasMeaningfulToken = delta.trim().length > 0;
              const prevSplits = prev.textSplitAfterIndices ?? [];
              const shouldRecordSplit =
                hasMeaningfulToken &&
                prev.toolCalls.length > 0 &&
                prevSplits[prevSplits.length - 1] !== prev.toolCalls.length;
              return {
                ...prev,
                content: prev.content + delta,
                textSplitAfterIndices: shouldRecordSplit
                  ? [...prevSplits, prev.toolCalls.length]
                  : prevSplits,
                hasTrailingAssistantText:
                  Boolean(prev.hasTrailingAssistantText) || hasMeaningfulToken,
              };
            });
          } else if (ev.event === "tool_start") {
            const tcId = String(ev.data.id ?? "");
            if (tcId) {
              toolStartedAt.set(tcId, Date.now());
            }
            const rec: ToolCallRecord = {
              id: tcId,
              name: String(ev.data.name ?? ""),
              args:
                typeof ev.data.args === "object" && ev.data.args !== null
                  ? (ev.data.args as Record<string, unknown>)
                  : {},
              output: "",
              status: "running",
              subagent: typeof ev.data.subagent === "string" ? ev.data.subagent : null,
              parent_id: typeof ev.data.parent_id === "string" ? ev.data.parent_id : null,
              duration_ms: null,
            };
            setStreamingAssistant((prev) =>
              prev
                ? {
                    ...prev,
                    toolCalls: [...prev.toolCalls, rec],
                    hasTrailingAssistantText: false,
                  }
                : prev,
            );
          } else if (ev.event === "tool_end") {
            const id = String(ev.data.id ?? "");
            const output = String(ev.data.output ?? "");
            const status = String(ev.data.status ?? "success");
            const serverDuration =
              typeof ev.data.duration_ms === "number" ? ev.data.duration_ms : null;
            const startedT = toolStartedAt.get(id);
            const resolvedDuration =
              serverDuration ?? (startedT !== undefined ? Math.max(0, Date.now() - startedT) : null);
            setStreamingAssistant((prev) =>
              prev
                ? {
                    ...prev,
                    toolCalls: prev.toolCalls.map((tc) =>
                      tc.id === id
                        ? { ...tc, output, status, duration_ms: resolvedDuration }
                        : tc,
                    ),
                  }
                : prev,
            );
          } else if (ev.event === "steer_applied") {
            const segElapsed = Math.max(0, Date.now() - segmentStartedAtMs);
            segmentStartedAtMs = Date.now();
            toolStartedAt.clear();
            const rawFlushed = ev.data.flushed_assistant;
            const flushed =
              typeof rawFlushed === "object" && rawFlushed !== null
                ? (rawFlushed as unknown as ChatMessage)
                : null;
            const rawUsers = Array.isArray(ev.data.user_messages)
              ? (ev.data.user_messages as unknown as ChatMessage[])
              : [];
            if (flushed?.id) {
              existingMessageIds.add(flushed.id);
              setTurnDurationsMs((prev) => ({
                ...prev,
                [flushed.id]: segElapsed,
              }));
            }
            for (const u of rawUsers) {
              if (u?.id) {
                existingMessageIds.add(u.id);
              }
            }
            if (flushed || rawUsers.length > 0) {
              setMessages((prev) => [
                ...prev,
                ...(flushed ? [flushed] : []),
                ...rawUsers,
              ]);
            }
            setStreamingAssistant({
              content: "",
              thinking: "",
              toolCalls: [],
              startedAtMs: segmentStartedAtMs,
              textSplitAfterIndices: [],
              hasTrailingAssistantText: false,
            });
          } else if (ev.event === "artifact_updated") {
            const doc = ev.data as unknown as ArtifactDoc;
            setArtifacts((prev) => upsertArtifactDoc(prev, doc));
            setActiveArtifactTab(doc.kind);
            setRightDrawerTab("artifacts");
          } else if (ev.event === "interrupt") {
            sawInterrupt = true;
            const pending = ev.data as unknown as PendingInterrupt;
            setActiveSession((prev) => {
              if (!prev || prev.id !== targetSessionId) return prev;
              const next: Session = {
                ...prev,
                status: "awaiting_approval",
                pending_interrupt: pending,
              };
              activeSessionRef.current = next;
              return next;
            });
            if (pending.kind === "plan_review") {
              setActiveArtifactTab("implementation_plan");
              setRightDrawerTab("artifacts");
            }
          } else if (ev.event === "diff_changed") {
            setTreeRevision((n) => n + 1);
          } else if (ev.event === "error") {
            sawError = true;
            setSessionQueuePaused(targetSessionId, true);
            setStreamError(String(ev.data.message ?? "Agent execution error"));
          }
        });
      } catch (err) {
        if (activeSessionRef.current?.id !== targetSessionId) return;
        if (err instanceof ApiError && err.code === "LLM_SETTINGS_REQUIRED") {
          sawError = true;
          setSessionQueuePaused(targetSessionId, true);
          setSettingsOpen(true);
          setStreamError(err.message);
        } else if (err instanceof DOMException && err.name === "AbortError") {
          wasAborted = true;
          if (manualStopRef.current) {
            setSessionQueuePaused(targetSessionId, true);
          }
        } else {
          sawError = true;
          setSessionQueuePaused(targetSessionId, true);
          setStreamError(err instanceof Error ? err.message : "Streaming error");
        }
      } finally {
        const elapsedMs = Math.max(0, Date.now() - segmentStartedAtMs);
        if (abortRef.current === controller) {
          abortRef.current = null;
        }
        if (activeSessionRef.current?.id !== targetSessionId) {
          void apiClient.listSessions().then(setSessions).catch(() => undefined);
          return;
        }
        isStreamingRef.current = false;
        setIsStreaming(false);
        setStreamingAssistant(null);
        setTreeRevision((n) => n + 1);
        const [refreshedList, detail, artBundle, diffData] = await Promise.all([
          apiClient.listSessions().catch(() => null),
          apiClient.getSession(targetSessionId, 20).catch(() => null),
          apiClient.listArtifacts(targetSessionId).catch(() => null),
          wsPath ? apiClient.getWorkspaceDiff(wsPath).catch(() => null) : Promise.resolve(null),
        ]);
        if (refreshedList) {
          setSessions(refreshedList);
        }
        if (activeSessionRef.current?.id !== targetSessionId) {
          return;
        }
        if (detail) {
          activeSessionRef.current = detail.session;
          setActiveSession(detail.session);
          const persistedAssistant = [...detail.messages]
            .reverse()
            .find((m) => m.role === "assistant" && !existingMessageIds.has(m.id));
          if (persistedAssistant) {
            setTurnDurationsMs((prev) => ({
              ...prev,
              [persistedAssistant.id]: elapsedMs,
            }));
          }
          let keptOlderPrefix = false;
          setMessages((prev) => {
            const detailIds = new Set(detail.messages.map((m) => m.id));
            const olderPrefix = prev.filter(
              (m) =>
                m.session_id === targetSessionId &&
                !m.id.startsWith("local-") &&
                !detailIds.has(m.id),
            );
            keptOlderPrefix = olderPrefix.length > 0;
            return [...olderPrefix, ...detail.messages];
          });
          setHasMoreMessages((prevHasMore) =>
            keptOlderPrefix ? prevHasMore : Boolean(detail.has_more_messages),
          );
        }
        if (artBundle) {
          setArtifacts(artBundle);
        }
        if (diffData) {
          setDiff(diffData);
        }
        if (
          !wasAborted &&
          !sawError &&
          !sawInterrupt &&
          detail?.session.status === "idle" &&
          !queuePausedRef.current[targetSessionId]
        ) {
          const pendingQueue = queuedBySessionRef.current[targetSessionId] ?? [];
          if (pendingQueue.length > 0) {
            const nextItem = pendingQueue[0];
            updateSessionQueue(targetSessionId, (prev) => prev.slice(1));
            void dispatchQueuedTurnRef.current?.(
              detail.session,
              nextItem.content,
              nextItem.pinnedSkills,
            );
          }
        }
      }
    },
    [settings, setSessionQueuePaused, updateSessionQueue],
  );

  const dispatchTurnForSession = useCallback(
    async (targetSession: Session, rawContent: string, explicitPinnedSkills?: string[]) => {
      const trimmed = rawContent.trim();
      if (!trimmed || isStreamingRef.current) return;
      if (!isLLMConfigured(settings)) {
        setSettingsOpen(true);
        setStreamError("Configure Endpoint URL, Model ID, and API Key in Settings.");
        return;
      }
      const { cleanedContent, mentionedSkills } = parseLeadingSkillMentions(
        trimmed,
        installedSkillsRef.current,
      );
      const basePins = explicitPinnedSkills ?? pinnedSkillsRef.current;
      const effectivePinnedSkills = Array.from(new Set([...basePins, ...mentionedSkills]));
      if (explicitPinnedSkills === undefined && pinnedSkillsRef.current.length > 0) {
        setPinnedSkills([]);
      }

      const optimisticUser: ChatMessage = {
        id: `local-${Date.now()}`,
        session_id: targetSession.id,
        role: "user",
        content: trimmed,
        thinking: "",
        tool_calls: [],
        created_at: new Date().toISOString(),
      };
      setMessages((prev) => [...prev, optimisticUser]);
      await runSSEStream(
        `/api/sessions/${encodeURIComponent(targetSession.id)}/messages/stream`,
        {
          content: cleanedContent,
          mode,
          pinned_skills: effectivePinnedSkills,
        },
        targetSession.id,
        targetSession.workspace_path,
      );
    },
    [settings, mode, runSSEStream],
  );
  dispatchQueuedTurnRef.current = dispatchTurnForSession;

  const sendMessage = useCallback(
    async (content: string, explicitPinnedSkills?: string[]) => {
      const trimmed = content.trim();
      if (!trimmed || isStreamingRef.current) return;
      if (!isLLMConfigured(settings)) {
        setSettingsOpen(true);
        setStreamError("Configure Endpoint URL, Model ID, and API Key in Settings.");
        return;
      }
      let targetSession = activeSessionRef.current;
      if (!targetSession) {
        const targetProj = projects.find((p) => p.id === activeProjectId) ?? projects[0];
        targetSession = await apiClient.createSession(
          targetProj?.path ?? (workspacePath || undefined),
          mode,
          targetProj?.id,
        );
        activeSessionRef.current = targetSession;
        setActiveSession(targetSession);
        setSessions((prev) => [targetSession as Session, ...prev]);
      }
      await dispatchTurnForSession(targetSession, trimmed, explicitPinnedSkills);
    },
    [settings, projects, activeProjectId, workspacePath, mode, dispatchTurnForSession],
  );

  const enqueueMessage = useCallback(
    async (content: string) => {
      const trimmed = content.trim();
      if (!trimmed) return;
      const currentSession = activeSessionRef.current;
      if (!isStreamingRef.current || !currentSession) {
        await sendMessage(trimmed);
        return;
      }
      const { mentionedSkills } = parseLeadingSkillMentions(
        trimmed,
        installedSkillsRef.current,
      );
      const capturedPins = Array.from(
        new Set([...pinnedSkillsRef.current, ...mentionedSkills]),
      );
      if (pinnedSkillsRef.current.length > 0) {
        setPinnedSkills([]);
      }
      const item: QueuedPrompt = {
        id: `q-${Date.now()}-${Math.random().toString(36).slice(2, 6)}`,
        content: trimmed,
        pinnedSkills: capturedPins,
        createdAt: new Date().toISOString(),
      };
      setSessionQueuePaused(currentSession.id, false);
      updateSessionQueue(currentSession.id, (prev) => [...prev, item]);
    },
    [sendMessage, setSessionQueuePaused, updateSessionQueue],
  );

  const steerMessage = useCallback(
    async (content: string, explicitPinnedSkills?: string[]) => {
      const trimmed = content.trim();
      if (!trimmed) return;
      const currentSession = activeSessionRef.current;
      const { cleanedContent, mentionedSkills } = parseLeadingSkillMentions(
        trimmed,
        installedSkillsRef.current,
      );
      const basePins = explicitPinnedSkills ?? pinnedSkillsRef.current;
      const effectivePins = Array.from(new Set([...basePins, ...mentionedSkills]));
      if (explicitPinnedSkills === undefined && pinnedSkillsRef.current.length > 0) {
        setPinnedSkills([]);
      }
      if (!currentSession || !isStreamingRef.current) {
        await sendMessage(trimmed, effectivePins);
        return;
      }
      try {
        await apiClient.steerSession(currentSession.id, {
          content: cleanedContent,
          pinned_skills: effectivePins,
        });
      } catch (err) {
        if (err instanceof ApiError && err.status === 409) {
          await sendMessage(trimmed, effectivePins);
          return;
        }
        setStreamError(err instanceof Error ? err.message : "Failed to steer active session");
      }
    },
    [sendMessage],
  );

  const steerQueuedMessage = useCallback(
    async (queueId: string) => {
      const currentSession = activeSessionRef.current;
      if (!currentSession) return;
      const list = queuedBySessionRef.current[currentSession.id] ?? [];
      const target = list.find((item) => item.id === queueId);
      if (!target) return;
      updateSessionQueue(currentSession.id, (prev) => prev.filter((item) => item.id !== queueId));
      await steerMessage(target.content, target.pinnedSkills);
    },
    [steerMessage, updateSessionQueue],
  );

  const sendQueuedMessageNow = useCallback(
    async (queueId: string) => {
      const currentSession = activeSessionRef.current;
      if (!currentSession || isStreamingRef.current) return;
      const list = queuedBySessionRef.current[currentSession.id] ?? [];
      const target = list.find((item) => item.id === queueId);
      if (!target) return;
      updateSessionQueue(currentSession.id, (prev) => prev.filter((item) => item.id !== queueId));
      setSessionQueuePaused(currentSession.id, false);
      await dispatchTurnForSession(currentSession, target.content, target.pinnedSkills);
    },
    [dispatchTurnForSession, setSessionQueuePaused, updateSessionQueue],
  );

  const editQueuedMessage = useCallback(
    (queueId: string) => {
      const currentSession = activeSessionRef.current;
      if (!currentSession) return;
      const list = queuedBySessionRef.current[currentSession.id] ?? [];
      const target = list.find((item) => item.id === queueId);
      if (!target) return;
      updateSessionQueue(currentSession.id, (prev) => prev.filter((item) => item.id !== queueId));
      if (target.pinnedSkills.length > 0) {
        setPinnedSkills((prev) => Array.from(new Set([...prev, ...target.pinnedSkills])));
      }
      setRewoundDraft({ text: target.content, seq: Date.now() });
    },
    [updateSessionQueue],
  );

  const removeQueuedMessage = useCallback(
    (queueId: string) => {
      const currentSession = activeSessionRef.current;
      if (!currentSession) return;
      updateSessionQueue(currentSession.id, (prev) => prev.filter((item) => item.id !== queueId));
    },
    [updateSessionQueue],
  );

  const loadOlderMessages = useCallback(async () => {
    const session = activeSessionRef.current;
    const currentMessages = messagesRef.current;
    if (
      !session ||
      loadingOlderRef.current ||
      !hasMoreMessagesRef.current ||
      currentMessages.length === 0
    ) {
      return;
    }
    const oldest = currentMessages.find((m) => !m.id.startsWith("local-"));
    if (!oldest) {
      return;
    }
    loadingOlderRef.current = true;
    setIsLoadingOlderMessages(true);
    try {
      const page = await apiClient.listMessagesPage(session.id, oldest.id, 20);
      if (activeSessionRef.current?.id !== session.id) {
        return;
      }
      setMessages((prev) => {
        const existingIds = new Set(prev.map((m) => m.id));
        const olderUnique = page.messages.filter((m) => !existingIds.has(m.id));
        return olderUnique.length > 0 ? [...olderUnique, ...prev] : prev;
      });
      setHasMoreMessages(page.has_more);
    } catch (err) {
      if (activeSessionRef.current?.id === session.id) {
        setStreamError(err instanceof Error ? err.message : "Failed to load older messages");
      }
    } finally {
      loadingOlderRef.current = false;
      setIsLoadingOlderMessages(false);
    }
  }, []);

  const stopStream = useCallback(() => {
    const sid = activeSessionRef.current?.id;
    if (sid) {
      setSessionQueuePaused(sid, true);
    }
    manualStopRef.current = true;
    abortRef.current?.abort();
  }, [setSessionQueuePaused]);

  const resumeInterrupt = useCallback(
    async (
      decision: ResumeDecision,
      feedback?: string,
      answers?: QuestionAnswer[],
    ) => {
      if (!activeSession || isStreaming) return;
      if (!isLLMConfigured(settings)) {
        setSettingsOpen(true);
        return;
      }
      const pending = activeSession.pending_interrupt;
      if (pending?.kind === "user_question" && (decision === "answer" || decision === "skip")) {
        const qaSummary = formatOptimisticQaSummary(
          pending.questions ?? [],
          decision,
          answers ?? [],
        );
        const optimisticUser: ChatMessage = {
          id: `local-${Date.now()}`,
          session_id: activeSession.id,
          role: "user",
          content: qaSummary,
          thinking: "",
          tool_calls: [],
          created_at: new Date().toISOString(),
        };
        setMessages((prev) => [...prev, optimisticUser]);
      }
      const clearedSession: Session = {
        ...activeSession,
        status: "running",
        pending_interrupt: null,
      };
      activeSessionRef.current = clearedSession;
      setActiveSession((prev) =>
        prev && prev.id === activeSession.id ? clearedSession : prev,
      );
      setSessions((prev) =>
        prev.map((s) => (s.id === activeSession.id ? clearedSession : s)),
      );
      await runSSEStream(
        `/api/sessions/${encodeURIComponent(activeSession.id)}/resume`,
        {
          decision,
          feedback: feedback ?? null,
          ...(answers && answers.length > 0 ? { answers } : {}),
        },
        activeSession.id,
        activeSession.workspace_path,
      );
    },
    [activeSession, isStreaming, settings, runSSEStream],
  );

  const refreshDiff = useCallback(async () => {
    const targetPath = workspacePathRef.current;
    if (!targetPath) return;
    const diffData = await apiClient.getWorkspaceDiff(targetPath);
    if (workspacePathRef.current === targetPath) {
      setDiff(diffData);
    }
  }, []);

  const revertFromMessage = useCallback(
    async (messageId: string) => {
      const session = activeSessionRef.current;
      if (!session || isStreaming) return;
      setStreamError(null);
      try {
        const res = await apiClient.revertSessionTurn(session.id, messageId);
        if (activeSessionRef.current?.id !== session.id) {
          return;
        }
        activeSessionRef.current = res.session;
        setActiveSession(res.session);
        setSessions((prev) => prev.map((s) => (s.id === res.session.id ? res.session : s)));
        setMessages(res.messages);
        setHasMoreMessages(res.has_more_messages);
        if (res.rewound_user_prompt) {
          setRewoundDraft({ text: res.rewound_user_prompt, seq: Date.now() });
        }
        setTreeRevision((n) => n + 1);
        const [artBundle, diffData] = await Promise.all([
          apiClient.listArtifacts(session.id).catch(() => null),
          session.workspace_path
            ? apiClient.getWorkspaceDiff(session.workspace_path).catch(() => null)
            : Promise.resolve(null),
        ]);
        if (activeSessionRef.current?.id !== session.id) {
          return;
        }
        if (artBundle) {
          setArtifacts(artBundle);
        }
        if (diffData) {
          setDiff(diffData);
        }
      } catch (err) {
        setStreamError(err instanceof Error ? err.message : "Failed to revert turn");
        throw err;
      }
    },
    [isStreaming],
  );

  const revertDiffFiles = useCallback(
    async (files?: string[]) => {
      const targetPath = workspacePathRef.current;
      if (!targetPath || isStreaming) return;
      setStreamError(null);
      try {
        const diffData = await apiClient.revertWorkspaceDiff(targetPath, files);
        if (workspacePathRef.current === targetPath) {
          setDiff(diffData);
          setTreeRevision((n) => n + 1);
        }
        const session = activeSessionRef.current;
        if (session) {
          const artBundle = await apiClient.listArtifacts(session.id).catch(() => null);
          if (artBundle && activeSessionRef.current?.id === session.id) {
            setArtifacts(artBundle);
          }
        }
      } catch (err) {
        setStreamError(err instanceof Error ? err.message : "Failed to revert workspace diff");
        throw err;
      }
    },
    [isStreaming],
  );

  const jumpToDiffHunk = useCallback(
    async (path: string, hunkName?: string) => {
      setDiffFocus({ path, hunkName });
      setRightDrawerTab("diff");
      await refreshDiff();
    },
    [refreshDiff],
  );

  const saveArtifactToWorkspace = useCallback(
    async (kind: ArtifactKind, relativePath: string) => {
      if (!activeSession) {
        throw new Error("No active session");
      }
      const res = await apiClient.saveArtifactToWorkspace(
        activeSession.id,
        kind,
        workspacePath,
        relativePath,
      );
      setTreeRevision((n) => n + 1);
      await refreshDiff();
      return res.saved_path;
    },
    [activeSession, workspacePath, refreshDiff],
  );

  const activeQueuedMessages = activeSession ? (queuedBySession[activeSession.id] ?? []) : [];
  const activeQueuePaused = activeSession ? Boolean(queuePausedBySession[activeSession.id]) : false;

  const value: WorkspaceContextValue = {
    state: {
      settings,
      settingsOpen,
      addProjectOpen,
      skillsOpen,
      memoryOpen,
      installedSkills,
      pinnedSkills,
      projects,
      activeProjectId,
      sessions,
      activeSession,
      messages,
      turnDurationsMs,
      hasMoreMessages,
      isLoadingOlderMessages,
      streamingAssistant,
      isStreaming,
      mode,
      workspacePath,
      workspaceInfo,
      workspaceError,
      artifacts,
      diff,
      diffFocus,
      rightDrawerTab,
      activeArtifactTab,
      streamError,
      treeRevision,
      rewoundDraft,
      queuedMessages: activeQueuedMessages,
      isQueuePaused: activeQueuePaused,
    },
    actions: {
      openSettings,
      closeSettings,
      openAddProject,
      closeAddProject,
      openSkills,
      closeSkills,
      openMemory,
      closeMemory,
      refreshInstalledSkills,
      pinSkill,
      unpinSkill,
      clearPinnedSkills,
      updateSettings,
      clearSettings,
      addProject,
      selectProject,
      removeProject,
      createNewSession,
      selectSession,
      removeSession,
      setWorkspacePath,
      setMode,
      sendMessage,
      enqueueMessage,
      steerMessage,
      steerQueuedMessage,
      sendQueuedMessageNow,
      editQueuedMessage,
      removeQueuedMessage,
      loadOlderMessages,
      stopStream,
      resumeInterrupt,
      openRightDrawer: setRightDrawerTab,
      setActiveArtifactTab,
      jumpToDiffHunk,
      refreshDiff,
      revertFromMessage,
      revertDiffFiles,
      saveArtifactToWorkspace,
    },
    meta: {
      configured: isLLMConfigured(settings),
    },
  };

  return <WorkspaceContext value={value}>{children}</WorkspaceContext>;
}
