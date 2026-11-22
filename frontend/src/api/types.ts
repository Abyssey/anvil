export type RunMode = "ask" | "plan" | "auto";
export type SessionStatus = "idle" | "running" | "awaiting_approval" | "error";
export type ArtifactKind = "implementation_plan" | "task" | "walkthrough";

export interface ToolCallRecord {
  id: string;
  name: string;
  args: Record<string, unknown>;
  output: string;
  status: string;
  subagent?: string | null;
}

export interface ChatMessage {
  id: string;
  session_id: string;
  role: "user" | "assistant";
  content: string;
  thinking: string;
  tool_calls: ToolCallRecord[];
  created_at: string;
}

export interface PendingInterrupt {
  kind: "plan_review" | "shell_approval";
  summary: string;
  artifact_kind?: string | null;
  command?: string | null;
  workspace?: string | null;
  reason?: string | null;
}

export interface Project {
  id: string;
  name: string;
  path: string;
  is_git_repo: boolean;
  branch: string;
  created_at: string;
  updated_at: string;
}

export interface DirectoryEntry {
  name: string;
  path: string;
  is_git_repo: boolean;
}

export interface DirectoryBrowseResponse {
  current_path: string;
  parent_path: string | null;
  is_git_repo: boolean;
  branch: string;
  directories: DirectoryEntry[];
}

export interface Session {
  id: string;
  project_id?: string | null;
  title: string;
  workspace_path: string;
  mode: RunMode;
  status: SessionStatus;
  pending_interrupt: PendingInterrupt | null;
  created_at: string;
  updated_at: string;
}

export interface SessionDetail {
  session: Session;
  messages: ChatMessage[];
  has_more_messages?: boolean;
}

export interface MessagePageResponse {
  messages: ChatMessage[];
  has_more: boolean;
}

export interface ArtifactDoc {
  kind: ArtifactKind;
  title: string;
  content: string;
  updated_at: string;
}

export interface DiffHunk {
  path: string;
  name: string;
  old_start: number;
  old_count: number;
  new_start: number;
  new_count: number;
  header: string;
  lines: string[];
}

export interface FileDiff {
  path: string;
  status: "modified" | "added" | "deleted" | "renamed" | "untracked";
  hunks: DiffHunk[];
  preview_content?: string | null;
}

export interface FileHunks {
  path: string;
  hunks: string[];
}

export interface WalkthroughChunk {
  id: string;
  title: string;
  explanation: string;
  file_hunks: FileHunks[];
}

export interface WalkthroughPlan {
  git_head: string;
  chunks: WalkthroughChunk[];
  other_files: string[];
}

export interface ArtifactBundle {
  artifacts: ArtifactDoc[];
  walkthrough_plan: WalkthroughPlan | null;
}

export interface WorkspaceInfo {
  path: string;
  name: string;
  is_git_repo: boolean;
  branch: string;
}

export interface TreeNode {
  name: string;
  path: string;
  is_dir: boolean;
  size: number;
  children: TreeNode[];
}

export interface FilePreview {
  path: string;
  content: string;
  is_binary: boolean;
  truncated: boolean;
  size: number;
}

export interface WorkspaceDiff {
  workspace_path: string;
  branch: string;
  git_head: string;
  files: FileDiff[];
}

export interface StreamEvent {
  event: string;
  data: Record<string, unknown>;
}
