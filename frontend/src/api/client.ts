import type {
  ArtifactBundle,
  ArtifactKind,
  CatalogTab,
  DirectoryBrowseResponse,
  FilePreview,
  InstalledSkill,
  InstalledSkillDetail,
  MessagePageResponse,
  Project,
  ProjectMemory,
  RemoteSkillSummary,
  RunMode,
  Session,
  SessionDetail,
  SkillCatalogPage,
  SkillInstallRequest,
  SkillPreview,
  SkillScope,
  StreamEvent,
  TreeNode,
  WorkspaceDiff,
  WorkspaceInfo,
} from "./types";
import { normalizeBaseUrl, type LLMSettings } from "../state/settings";

export class ApiError extends Error {
  readonly status: number;
  readonly code: string;

  constructor(status: number, code: string, message: string) {
    super(message);
    this.status = status;
    this.code = code;
  }
}

export function llmHeaders(settings: LLMSettings): Record<string, string> {
  return {
    "X-LLM-Base-Url": normalizeBaseUrl(settings.baseUrl),
    "X-LLM-Model": settings.model.trim(),
    "X-LLM-Api-Key": settings.apiKey.trim(),
    "X-LLM-Enable-Thinking": settings.enableThinking ? "true" : "false",
  };
}

async function parseErrorResponse(res: Response): Promise<ApiError> {
  try {
    const body = (await res.json()) as {
      error?: { code?: string; message?: string };
      detail?: string;
    };
    const code = body.error?.code ?? `HTTP_${res.status}`;
    const msg = body.error?.message ?? body.detail ?? `Request failed (${res.status})`;
    return new ApiError(res.status, code, msg);
  } catch {
    return new ApiError(res.status, `HTTP_${res.status}`, `Request failed (${res.status})`);
  }
}

async function fetchJson<T>(url: string, init?: RequestInit): Promise<T> {
  const res = await fetch(url, init);
  if (!res.ok) {
    throw await parseErrorResponse(res);
  }
  if (res.status === 204) {
    return undefined as T;
  }
  return (await res.json()) as T;
}

export const apiClient = {
  getDefaultWorkspace(): Promise<WorkspaceInfo> {
    return fetchJson("/api/workspace/default");
  },

  validateWorkspace(path: string): Promise<WorkspaceInfo> {
    return fetchJson(`/api/workspace/validate?path=${encodeURIComponent(path)}`);
  },

  browseDirectories(path?: string): Promise<DirectoryBrowseResponse> {
    const qs = path ? `?path=${encodeURIComponent(path)}` : "";
    return fetchJson(`/api/workspace/browse${qs}`);
  },

  listProjects(): Promise<Project[]> {
    return fetchJson("/api/projects");
  },

  createProject(path: string): Promise<Project> {
    return fetchJson("/api/projects", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ path }),
    });
  },

  deleteProject(projectId: string): Promise<void> {
    return fetchJson(`/api/projects/${encodeURIComponent(projectId)}`, {
      method: "DELETE",
    });
  },

  getProjectMemory(projectId: string): Promise<ProjectMemory> {
    return fetchJson(`/api/projects/${encodeURIComponent(projectId)}/memory`);
  },

  updateProjectMemory(projectId: string, content: string): Promise<ProjectMemory> {
    return fetchJson(`/api/projects/${encodeURIComponent(projectId)}/memory`, {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ content }),
    });
  },

  clearProjectMemory(projectId: string): Promise<void> {
    return fetchJson(`/api/projects/${encodeURIComponent(projectId)}/memory`, {
      method: "DELETE",
    });
  },

  listInstalledSkills(workspacePath?: string): Promise<InstalledSkill[]> {
    const qs = workspacePath ? `?workspace_path=${encodeURIComponent(workspacePath)}` : "";
    return fetchJson(`/api/skills/installed${qs}`);
  },

  getInstalledSkillDetail(
    scope: SkillScope,
    name: string,
    workspacePath?: string,
  ): Promise<InstalledSkillDetail> {
    const qs = workspacePath ? `?workspace_path=${encodeURIComponent(workspacePath)}` : "";
    return fetchJson(
      `/api/skills/installed/${encodeURIComponent(scope)}/${encodeURIComponent(name)}${qs}`,
    );
  },

  browseSkillsCatalog(
    tab: CatalogTab = "all-time",
    page = 0,
    workspacePath?: string,
  ): Promise<SkillCatalogPage> {
    const qs = new URLSearchParams({ tab, page: String(page) });
    if (workspacePath) {
      qs.set("workspace_path", workspacePath);
    }
    return fetchJson(`/api/skills/catalog?${qs.toString()}`);
  },

  searchSkillsCatalog(
    q: string,
    limit = 20,
    workspacePath?: string,
  ): Promise<RemoteSkillSummary[]> {
    const qs = new URLSearchParams({ q, limit: String(limit) });
    if (workspacePath) {
      qs.set("workspace_path", workspacePath);
    }
    return fetchJson(`/api/skills/search?${qs.toString()}`);
  },

  previewRemoteSkill(source: string, skillId?: string): Promise<SkillPreview> {
    const qs = new URLSearchParams({ source });
    if (skillId) {
      qs.set("skill_id", skillId);
    }
    return fetchJson(`/api/skills/preview?${qs.toString()}`);
  },

  installSkill(req: SkillInstallRequest): Promise<InstalledSkill> {
    return fetchJson("/api/skills/install", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(req),
    });
  },

  deleteSkill(scope: SkillScope, name: string, workspacePath?: string): Promise<void> {
    const qs = workspacePath ? `?workspace_path=${encodeURIComponent(workspacePath)}` : "";
    return fetchJson(
      `/api/skills/${encodeURIComponent(scope)}/${encodeURIComponent(name)}${qs}`,
      { method: "DELETE" },
    );
  },

  getWorkspaceTree(path: string, relPath = "", maxDepth = 3): Promise<TreeNode[]> {
    const qs = new URLSearchParams({
      path,
      rel_path: relPath,
      max_depth: String(maxDepth),
    });
    return fetchJson(`/api/workspace/tree?${qs.toString()}`);
  },

  getWorkspaceFile(path: string, relPath: string): Promise<FilePreview> {
    const qs = new URLSearchParams({ path, rel_path: relPath });
    return fetchJson(`/api/workspace/file?${qs.toString()}`);
  },

  getWorkspaceDiff(path: string): Promise<WorkspaceDiff> {
    return fetchJson(`/api/workspace/diff?path=${encodeURIComponent(path)}`);
  },

  pingLLM(settings: LLMSettings): Promise<{ ok: boolean; base_url: string; model: string }> {
    return fetchJson("/api/workspace/llm-ping", {
      method: "POST",
      headers: llmHeaders(settings),
    });
  },

  listSessions(): Promise<Session[]> {
    return fetchJson("/api/sessions");
  },

  createSession(
    workspacePath?: string,
    mode: RunMode = "ask",
    projectId?: string,
  ): Promise<Session> {
    return fetchJson("/api/sessions", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        project_id: projectId ?? null,
        workspace_path: workspacePath ?? null,
        mode,
      }),
    });
  },

  getSession(sessionId: string, limit = 20): Promise<SessionDetail> {
    const qs = new URLSearchParams({ limit: String(limit) });
    return fetchJson(`/api/sessions/${encodeURIComponent(sessionId)}?${qs.toString()}`);
  },

  listMessagesPage(
    sessionId: string,
    beforeId?: string,
    limit = 20,
  ): Promise<MessagePageResponse> {
    const qs = new URLSearchParams({ limit: String(limit) });
    if (beforeId) {
      qs.set("before_id", beforeId);
    }
    return fetchJson(
      `/api/sessions/${encodeURIComponent(sessionId)}/messages?${qs.toString()}`,
    );
  },

  updateSession(
    sessionId: string,
    patch: { title?: string; workspace_path?: string; mode?: RunMode },
  ): Promise<Session> {
    return fetchJson(`/api/sessions/${encodeURIComponent(sessionId)}`, {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(patch),
    });
  },

  deleteSession(sessionId: string): Promise<void> {
    return fetchJson(`/api/sessions/${encodeURIComponent(sessionId)}`, {
      method: "DELETE",
    });
  },

  listArtifacts(sessionId: string): Promise<ArtifactBundle> {
    return fetchJson(`/api/sessions/${encodeURIComponent(sessionId)}/artifacts`);
  },

  saveArtifactToWorkspace(
    sessionId: string,
    kind: ArtifactKind,
    workspacePath: string,
    relativePath: string,
  ): Promise<{ saved_path: string }> {
    return fetchJson(
      `/api/sessions/${encodeURIComponent(sessionId)}/artifacts/${encodeURIComponent(kind)}/save`,
      {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          workspace_path: workspacePath,
          relative_path: relativePath,
        }),
      },
    );
  },

  async streamSSE(
    url: string,
    body: Record<string, unknown>,
    settings: LLMSettings,
    signal: AbortSignal,
    onEvent: (event: StreamEvent) => void,
  ): Promise<void> {
    const res = await fetch(url, {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        Accept: "text/event-stream",
        ...llmHeaders(settings),
      },
      body: JSON.stringify(body),
      signal,
    });
    if (!res.ok) {
      throw await parseErrorResponse(res);
    }
    if (!res.body) {
      return;
    }

    const reader = res.body.getReader();
    const decoder = new TextDecoder();
    let buffer = "";
    let eventName = "message";
    let dataLines: string[] = [];

    const flushEvent = () => {
      if (dataLines.length === 0) {
        eventName = "message";
        return;
      }
      const rawData = dataLines.join("\n");
      dataLines = [];
      let parsed: Record<string, unknown> = {};
      try {
        parsed = JSON.parse(rawData) as Record<string, unknown>;
      } catch {
        parsed = { raw: rawData };
      }
      onEvent({ event: eventName, data: parsed });
      eventName = "message";
    };

    const consumeLines = (lines: string[]) => {
      for (const line of lines) {
        if (line === "") {
          flushEvent();
        } else if (line.startsWith("event:")) {
          eventName = line.slice(6).trim();
        } else if (line.startsWith("data:")) {
          dataLines.push(line.slice(5).trimStart());
        }
      }
    };

    while (true) {
      const { value, done } = await reader.read();
      if (done) {
        buffer += decoder.decode();
        break;
      }
      buffer += decoder.decode(value, { stream: true });
      const lines = buffer.split(/\r?\n/);
      buffer = lines.pop() ?? "";
      consumeLines(lines);
    }
    if (buffer) {
      consumeLines(buffer.split(/\r?\n/));
    }
    flushEvent();
  },
};
