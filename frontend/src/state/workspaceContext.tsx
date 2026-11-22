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
  PendingInterrupt,
  Project,
  RunMode,
  Session,
  StreamEvent,
  ToolCallRecord,
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
}

export interface WorkspaceState {
  settings: LLMSettings;
  settingsOpen: boolean;
  addProjectOpen: boolean;
  projects: Project[];
  activeProjectId: string | null;
  sessions: Session[];
  activeSession: Session | null;
  messages: ChatMessage[];
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
}

export interface WorkspaceActions {
  openSettings: () => void;
  closeSettings: () => void;
  openAddProject: () => void;
  closeAddProject: () => void;
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
  loadOlderMessages: () => Promise<void>;
  stopStream: () => void;
  resumeInterrupt: (
    decision: "approve" | "revise" | "deny",
    feedback?: string,
  ) => Promise<void>;
  openRightDrawer: (tab: RightDrawerTab) => void;
  setActiveArtifactTab: (kind: ArtifactKind) => void;
  jumpToDiffHunk: (path: string, hunkName?: string) => Promise<void>;
  refreshDiff: () => Promise<void>;
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

function projectForSession(projects: Project[], session: Session): Project | undefined {
  if (session.project_id) {
    const byId = projects.find((p) => p.id === session.project_id);
    if (byId) return byId;
  }
  return projects.find((p) => p.path === session.workspace_path);
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
  const [projects, setProjects] = useState<Project[]>([]);
  const [activeProjectId, setActiveProjectId] = useState<string | null>(null);
  const [sessions, setSessions] = useState<Session[]>([]);
  const [activeSession, setActiveSession] = useState<Session | null>(null);
  const [messages, setMessages] = useState<ChatMessage[]>([]);
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

  const abortRef = useRef<AbortController | null>(null);
  const loadingOlderRef = useRef(false);
  const activeSessionRef = useRef<Session | null>(null);
  const messagesRef = useRef<ChatMessage[]>([]);
  const hasMoreMessagesRef = useRef(false);

  activeSessionRef.current = activeSession;
  messagesRef.current = messages;
  hasMoreMessagesRef.current = hasMoreMessages;

  const loadSessionDetail = useCallback(
    async (sessionId: string, knownProjects?: Project[]) => {
      loadingOlderRef.current = false;
      setIsLoadingOlderMessages(false);
      const [detail, artBundle] = await Promise.all([
        apiClient.getSession(sessionId, 20),
        apiClient.listArtifacts(sessionId),
      ]);
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
        setWorkspaceInfo(info);
        setDiff(diffData);
      } catch (err) {
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
      }
      setSessions((prev) => [created, ...prev]);
      setDiffFocus(null);
      setStreamError(null);
      await loadSessionDetail(created.id);
    },
    [projects, activeProjectId, workspacePath, mode, loadSessionDetail],
  );

  const selectProject = useCallback(
    async (projectId: string) => {
      const targetProj = projects.find((p) => p.id === projectId);
      if (!targetProj) return;
      setActiveProjectId(targetProj.id);
      setWorkspacePathState(targetProj.path);
      setStreamError(null);
      setDiffFocus(null);
      setRightDrawerTab("files");
      setTreeRevision((n) => n + 1);

      const projSessions = sessions.filter(
        (s) => s.project_id === targetProj.id || s.workspace_path === targetProj.path,
      );
      if (projSessions.length > 0) {
        await loadSessionDetail(projSessions[0].id);
      } else {
        const created = await apiClient.createSession(targetProj.path, mode, targetProj.id);
        setSessions((prev) => [created, ...prev]);
        await loadSessionDetail(created.id);
      }
    },
    [projects, sessions, mode, loadSessionDetail],
  );

  const addProject = useCallback(
    async (path: string) => {
      setWorkspaceError(null);
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

      const existingSessions = sessions.filter(
        (s) => s.project_id === createdProj.id || s.workspace_path === createdProj.path,
      );
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
    [projects, sessions, mode, loadSessionDetail],
  );

  const removeProject = useCallback(
    async (projectId: string) => {
      await apiClient.deleteProject(projectId);
      const remainingProjects = projects.filter((p) => p.id !== projectId);
      const remainingSessions = sessions.filter((s) => s.project_id !== projectId);
      setProjects(remainingProjects);
      setSessions(remainingSessions);

      if (activeProjectId === projectId || activeSession?.project_id === projectId) {
        if (remainingProjects.length > 0) {
          const nextProj = remainingProjects[0];
          setActiveProjectId(nextProj.id);
          const nextProjSessions = remainingSessions.filter(
            (s) => s.project_id === nextProj.id || s.workspace_path === nextProj.path,
          );
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
    [projects, sessions, activeProjectId, activeSession, mode, loadSessionDetail],
  );

  const selectSession = useCallback(
    async (sessionId: string) => {
      setStreamError(null);
      await loadSessionDetail(sessionId);
    },
    [loadSessionDetail],
  );

  const removeSession = useCallback(
    async (sessionId: string) => {
      await apiClient.deleteSession(sessionId);
      const remaining = sessions.filter((s) => s.id !== sessionId);
      setSessions(remaining);
      if (activeSession?.id === sessionId) {
        const sameProjectSessions = remaining.filter(
          (s) => s.project_id === activeProjectId || s.workspace_path === workspacePath,
        );
        if (sameProjectSessions.length > 0) {
          await loadSessionDetail(sameProjectSessions[0].id);
        } else if (remaining.length > 0) {
          await loadSessionDetail(remaining[0].id);
        } else {
          const targetProj = projects.find((p) => p.id === activeProjectId) ?? projects[0];
          const created = await apiClient.createSession(
            targetProj?.path ?? (workspacePath || undefined),
            mode,
            targetProj?.id,
          );
          setSessions([created]);
          await loadSessionDetail(created.id);
        }
      }
    },
    [sessions, activeSession, activeProjectId, projects, workspacePath, mode, loadSessionDetail],
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
        setActiveSession(updated);
        setSessions((prev) => prev.map((s) => (s.id === updated.id ? updated : s)));
      }
    },
    [activeSession],
  );

  const runSSEStream = useCallback(
    async (url: string, body: Record<string, unknown>, targetSessionId: string, wsPath: string) => {
      const controller = new AbortController();
      abortRef.current = controller;
      setIsStreaming(true);
      setStreamError(null);
      setStreamingAssistant({ content: "", thinking: "", toolCalls: [] });

      try {
        await apiClient.streamSSE(url, body, settings, controller.signal, (ev: StreamEvent) => {
          if (ev.event === "thinking") {
            const delta = String(ev.data.text ?? "");
            setStreamingAssistant((prev) =>
              prev ? { ...prev, thinking: prev.thinking + delta } : prev,
            );
          } else if (ev.event === "token") {
            const delta = String(ev.data.text ?? "");
            setStreamingAssistant((prev) =>
              prev ? { ...prev, content: prev.content + delta } : prev,
            );
          } else if (ev.event === "tool_start") {
            const rec: ToolCallRecord = {
              id: String(ev.data.id ?? ""),
              name: String(ev.data.name ?? ""),
              args:
                typeof ev.data.args === "object" && ev.data.args !== null
                  ? (ev.data.args as Record<string, unknown>)
                  : {},
              output: "",
              status: "running",
              subagent: typeof ev.data.subagent === "string" ? ev.data.subagent : null,
            };
            setStreamingAssistant((prev) =>
              prev ? { ...prev, toolCalls: [...prev.toolCalls, rec] } : prev,
            );
          } else if (ev.event === "tool_end") {
            const id = String(ev.data.id ?? "");
            const output = String(ev.data.output ?? "");
            const status = String(ev.data.status ?? "success");
            setStreamingAssistant((prev) =>
              prev
                ? {
                    ...prev,
                    toolCalls: prev.toolCalls.map((tc) =>
                      tc.id === id ? { ...tc, output, status } : tc,
                    ),
                  }
                : prev,
            );
          } else if (ev.event === "artifact_updated") {
            const doc = ev.data as unknown as ArtifactDoc;
            setArtifacts((prev) => upsertArtifactDoc(prev, doc));
            setActiveArtifactTab(doc.kind);
            setRightDrawerTab("artifacts");
          } else if (ev.event === "interrupt") {
            const pending = ev.data as unknown as PendingInterrupt;
            setActiveSession((prev) =>
              prev ? { ...prev, status: "awaiting_approval", pending_interrupt: pending } : prev,
            );
            if (pending.kind === "plan_review") {
              setActiveArtifactTab("implementation_plan");
              setRightDrawerTab("artifacts");
            }
          } else if (ev.event === "diff_changed") {
            setTreeRevision((n) => n + 1);
          } else if (ev.event === "error") {
            setStreamError(String(ev.data.message ?? "Agent execution error"));
          }
        });
      } catch (err) {
        if (err instanceof ApiError && err.code === "LLM_SETTINGS_REQUIRED") {
          setSettingsOpen(true);
          setStreamError(err.message);
        } else if (err instanceof DOMException && err.name === "AbortError") {
          // User stopped stream manually
        } else {
          setStreamError(err instanceof Error ? err.message : "Streaming error");
        }
      } finally {
        abortRef.current = null;
        setIsStreaming(false);
        setStreamingAssistant(null);
        setTreeRevision((n) => n + 1);
        const [refreshedList, detail, artBundle, diffData] = await Promise.all([
          apiClient.listSessions().catch(() => sessions),
          apiClient.getSession(targetSessionId, 20).catch(() => null),
          apiClient.listArtifacts(targetSessionId).catch(() => null),
          wsPath ? apiClient.getWorkspaceDiff(wsPath).catch(() => null) : Promise.resolve(null),
        ]);
        setSessions(refreshedList);
        if (detail) {
          setActiveSession(detail.session);
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
      }
    },
    [settings, sessions],
  );

  const sendMessage = useCallback(
    async (content: string) => {
      const trimmed = content.trim();
      if (!trimmed || isStreaming) return;
      if (!isLLMConfigured(settings)) {
        setSettingsOpen(true);
        setStreamError("Configure Endpoint URL, Model ID, and API Key in Settings.");
        return;
      }
      let targetSession = activeSession;
      if (!targetSession) {
        const targetProj = projects.find((p) => p.id === activeProjectId) ?? projects[0];
        targetSession = await apiClient.createSession(
          targetProj?.path ?? (workspacePath || undefined),
          mode,
          targetProj?.id,
        );
        setActiveSession(targetSession);
        setSessions((prev) => [targetSession as Session, ...prev]);
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
        { content: trimmed, mode },
        targetSession.id,
        targetSession.workspace_path,
      );
    },
    [isStreaming, settings, activeSession, projects, activeProjectId, workspacePath, mode, runSSEStream],
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
    abortRef.current?.abort();
  }, []);

  const resumeInterrupt = useCallback(
    async (decision: "approve" | "revise" | "deny", feedback?: string) => {
      if (!activeSession || isStreaming) return;
      if (!isLLMConfigured(settings)) {
        setSettingsOpen(true);
        return;
      }
      await runSSEStream(
        `/api/sessions/${encodeURIComponent(activeSession.id)}/resume`,
        { decision, feedback: feedback ?? null },
        activeSession.id,
        activeSession.workspace_path,
      );
    },
    [activeSession, isStreaming, settings, runSSEStream],
  );

  const refreshDiff = useCallback(async () => {
    if (!workspacePath) return;
    const diffData = await apiClient.getWorkspaceDiff(workspacePath);
    setDiff(diffData);
  }, [workspacePath]);

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

  const value: WorkspaceContextValue = {
    state: {
      settings,
      settingsOpen,
      addProjectOpen,
      projects,
      activeProjectId,
      sessions,
      activeSession,
      messages,
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
    },
    actions: {
      openSettings,
      closeSettings,
      openAddProject,
      closeAddProject,
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
      loadOlderMessages,
      stopStream,
      resumeInterrupt,
      openRightDrawer: setRightDrawerTab,
      setActiveArtifactTab,
      jumpToDiffHunk,
      refreshDiff,
      saveArtifactToWorkspace,
    },
    meta: {
      configured: isLLMConfigured(settings),
    },
  };

  return <WorkspaceContext value={value}>{children}</WorkspaceContext>;
}
