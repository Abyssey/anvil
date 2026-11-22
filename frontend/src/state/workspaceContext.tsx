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
  sessions: Session[];
  activeSession: Session | null;
  messages: ChatMessage[];
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
}

export interface WorkspaceActions {
  openSettings: () => void;
  closeSettings: () => void;
  updateSettings: (next: LLMSettings) => void;
  clearSettings: () => void;
  createNewSession: () => Promise<void>;
  selectSession: (sessionId: string) => Promise<void>;
  removeSession: (sessionId: string) => Promise<void>;
  setWorkspacePath: (path: string) => Promise<void>;
  setMode: (mode: RunMode) => Promise<void>;
  sendMessage: (content: string) => Promise<void>;
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
  const [sessions, setSessions] = useState<Session[]>([]);
  const [activeSession, setActiveSession] = useState<Session | null>(null);
  const [messages, setMessages] = useState<ChatMessage[]>([]);
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
  const [rightDrawerTab, setRightDrawerTab] = useState<RightDrawerTab>(null);
  const [activeArtifactTab, setActiveArtifactTab] =
    useState<ArtifactKind>("walkthrough");
  const [streamError, setStreamError] = useState<string | null>(null);

  const abortRef = useRef<AbortController | null>(null);

  const loadSessionDetail = useCallback(async (sessionId: string) => {
    const [detail, artBundle] = await Promise.all([
      apiClient.getSession(sessionId),
      apiClient.listArtifacts(sessionId),
    ]);
    setActiveSession(detail.session);
    setMessages(detail.messages);
    setModeState(detail.session.mode);
    setWorkspacePathState(detail.session.workspace_path);
    setArtifacts(artBundle);
    setWorkspaceError(null);

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
  }, []);

  useEffect(() => {
    let active = true;
    void (async () => {
      try {
        const [defaultWs, list] = await Promise.all([
          apiClient.getDefaultWorkspace(),
          apiClient.listSessions(),
        ]);
        if (!active) return;
        setWorkspaceInfo(defaultWs);
        setWorkspacePathState(defaultWs.path);
        if (list.length > 0) {
          setSessions(list);
          await loadSessionDetail(list[0].id);
        } else {
          const created = await apiClient.createSession(defaultWs.path, "ask");
          if (!active) return;
          setSessions([created]);
          await loadSessionDetail(created.id);
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
  }, [loadSessionDetail]);

  const openSettings = useCallback(() => setSettingsOpen(true), []);
  const closeSettings = useCallback(() => setSettingsOpen(false), []);

  const updateSettings = useCallback((next: LLMSettings) => {
    const saved = saveLLMSettings(next);
    setSettings(saved);
    setStreamError(null);
  }, []);

  const clearSettings = useCallback(() => {
    setSettings(clearLLMSettings());
  }, []);

  const createNewSession = useCallback(async () => {
    const created = await apiClient.createSession(workspacePath || undefined, mode);
    setSessions((prev) => [created, ...prev]);
    setDiffFocus(null);
    setStreamError(null);
    await loadSessionDetail(created.id);
  }, [workspacePath, mode, loadSessionDetail]);

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
        if (remaining.length > 0) {
          await loadSessionDetail(remaining[0].id);
        } else {
          const created = await apiClient.createSession(workspacePath || undefined, mode);
          setSessions([created]);
          await loadSessionDetail(created.id);
        }
      }
    },
    [sessions, activeSession, workspacePath, mode, loadSessionDetail],
  );

  const setWorkspacePath = useCallback(
    async (nextPath: string) => {
      setWorkspaceError(null);
      try {
        const info = await apiClient.validateWorkspace(nextPath);
        setWorkspaceInfo(info);
        setWorkspacePathState(info.path);
        if (activeSession) {
          const updated = await apiClient.updateSession(activeSession.id, {
            workspace_path: info.path,
          });
          setActiveSession(updated);
          setSessions((prev) => prev.map((s) => (s.id === updated.id ? updated : s)));
        }
        const diffData = await apiClient.getWorkspaceDiff(info.path);
        setDiff(diffData);
      } catch (err) {
        setWorkspaceError(err instanceof Error ? err.message : "Invalid workspace path");
      }
    },
    [activeSession],
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
        const [refreshedList, detail, artBundle, diffData] = await Promise.all([
          apiClient.listSessions().catch(() => sessions),
          apiClient.getSession(targetSessionId).catch(() => null),
          apiClient.listArtifacts(targetSessionId).catch(() => null),
          wsPath ? apiClient.getWorkspaceDiff(wsPath).catch(() => null) : Promise.resolve(null),
        ]);
        setSessions(refreshedList);
        if (detail) {
          setActiveSession(detail.session);
          setMessages(detail.messages);
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
        targetSession = await apiClient.createSession(workspacePath || undefined, mode);
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
    [isStreaming, settings, activeSession, workspacePath, mode, runSSEStream],
  );

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
      await refreshDiff();
      return res.saved_path;
    },
    [activeSession, workspacePath, refreshDiff],
  );

  const value: WorkspaceContextValue = {
    state: {
      settings,
      settingsOpen,
      sessions,
      activeSession,
      messages,
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
    },
    actions: {
      openSettings,
      closeSettings,
      updateSettings,
      clearSettings,
      createNewSession,
      selectSession,
      removeSession,
      setWorkspacePath,
      setMode,
      sendMessage,
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
