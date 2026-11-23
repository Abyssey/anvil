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
  parent_id?: string | null;
  duration_ms?: number | null;
}

export type VerificationStamp = "passed" | "failed" | "none" | "not_needed";

export interface ChatMessage {
  id: string;
  session_id: string;
  role: "user" | "assistant";
  content: string;
  thinking: string;
  tool_calls: ToolCallRecord[];
  checkpoint_id?: string | null;
  changed_files?: string[];
  verification_stamp?: VerificationStamp | null;
  created_at: string;
}

export interface QuestionOption {
  label: string;
  description?: string | null;
  recommended?: boolean;
}

export interface UserQuestionItem {
  question: string;
  options: QuestionOption[];
}

export interface QuestionAnswer {
  question: string;
  selected_option?: string | null;
  custom_text?: string | null;
  skipped?: boolean;
}

export interface PendingInterrupt {
  kind: "plan_review" | "shell_approval" | "user_question";
  summary: string;
  artifact_kind?: string | null;
  command?: string | null;
  workspace?: string | null;
  reason?: string | null;
  questions?: UserQuestionItem[];
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

export interface ProjectMemory {
  project_id: string;
  content: string;
  updated_at: string | null;
  has_repo_agents_md: boolean;
  max_bytes: number;
}

export type SkillScope = "bundled" | "global" | "workspace";
export type CatalogTab = "all-time" | "trending" | "hot";

export interface InstalledSkill {
  name: string;
  description: string;
  scope: SkillScope;
  source_label: string;
  path: string;
  deletable: boolean;
  license?: string | null;
  compatibility?: string | null;
  origin_source?: string | null;
  origin_skill_id?: string | null;
  origin_url?: string | null;
  installed_at?: string | null;
}

export interface InstalledSkillDetail extends InstalledSkill {
  content: string;
  files: string[];
}

export interface RemoteSkillSummary {
  id: string;
  source: string;
  skill_id: string;
  name: string;
  installs: number;
  is_official: boolean;
  weekly_installs: number[];
  installs_yesterday?: number | null;
  change?: number | null;
}

export interface SkillCatalogPage {
  tab: CatalogTab;
  page: number;
  total: number;
  has_more: boolean;
  skills: RemoteSkillSummary[];
}

export interface SkillPreview {
  source: string;
  skill_id: string;
  name: string;
  description: string;
  content: string;
  license?: string | null;
  compatibility?: string | null;
  files: string[];
}

export interface SkillInstallRequest {
  source: string;
  skill_id?: string | null;
  scope: "global" | "workspace";
  workspace_path?: string | null;
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

export interface RevertSessionResponse {
  session: Session;
  messages: ChatMessage[];
  has_more_messages: boolean;
  reverted_files: string[];
  rewound_user_prompt: string | null;
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
