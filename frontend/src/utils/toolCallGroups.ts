import type { ChatMessage, ToolCallRecord } from "../api/types";

export const MAX_DERIVED_TURN_DURATION_MS = 24 * 3600 * 1000;

export type ExplorationToolCategory = "list" | "read" | "search";
export type ExplorationGroupStatus = "running" | "completed";

export interface ExplorationGroupBlock {
  kind: "exploration_group";
  id: string;
  calls: readonly ToolCallRecord[];
  status: ExplorationGroupStatus;
  activeLabel: string;
  summaryLabel: string;
}

export interface SingleToolBlock {
  kind: "single_tool";
  id: string;
  call: ToolCallRecord;
}

export type SubagentRole = "explorer" | "reviewer" | "general";
export type SubagentStatus = "running" | "completed" | "error";

export interface ReviewFindingCounts {
  important: number;
  nit: number;
  preExisting: number;
}

export interface SubagentRunItem {
  id: string;
  taskCall: ToolCallRecord;
  subagentType: string;
  role: SubagentRole;
  roleLabel: string;
  description: string;
  status: SubagentStatus;
  nestedCalls: readonly ToolCallRecord[];
  activeLabel: string;
  summaryLabel: string;
  output: string;
  durationMs: number | null;
  findingCounts: ReviewFindingCounts | null;
}

export interface SubagentGroupBlock {
  kind: "subagent_group";
  id: string;
  items: readonly SubagentRunItem[];
  isParallel: boolean;
  runningCount: number;
}

export type GroupedToolBlock =
  | ExplorationGroupBlock
  | SubagentGroupBlock
  | SingleToolBlock;

export interface GroupToolCallsOptions {
  isStreaming: boolean;
  hasAssistantTextStarted: boolean;
  groupKeyPrefix?: string;
  textSplitIndices?: readonly number[];
}

const READ_FILE_TOOLS = new Set<string>(["read_file", "view_file"]);
const SEARCH_TOOLS = new Set<string>(["grep", "glob", "find_files", "search", "search_files"]);
const LIST_TOOLS = new Set<string>(["ls", "list_files"]);

const SEARCH_ARG_KEYS = ["pattern", "query", "q", "regex", "path", "file_path"] as const;
const DEFAULT_ARG_KEYS = [
  "file_path",
  "path",
  "command",
  "subagent_type",
  "pattern",
  "query",
  "dir",
  "directory",
] as const;

function normalizeToolName(name: string): string {
  return name.trim().toLowerCase();
}

export function getExplorationToolCategory(name: string): ExplorationToolCategory | null {
  const normalized = normalizeToolName(name);
  if (READ_FILE_TOOLS.has(normalized)) {
    return "read";
  }
  if (SEARCH_TOOLS.has(normalized)) {
    return "search";
  }
  if (LIST_TOOLS.has(normalized)) {
    return "list";
  }
  return null;
}

export function isReadExplorationTool(name: string): boolean {
  return getExplorationToolCategory(name) !== null;
}

function firstNonEmptyString(
  args: Record<string, unknown>,
  keys: readonly string[],
): string | undefined {
  for (const key of keys) {
    const val = args[key];
    if (typeof val === "string" && val.trim().length > 0) {
      return val.trim();
    }
  }
  return undefined;
}

function truncateConcise(value: string, maxLength: number): string {
  if (value.length <= maxLength) {
    return value;
  }
  return `${value.slice(0, Math.max(1, maxLength - 1))}…`;
}

function extractFileBasename(rawPath: string): string {
  const cleaned = rawPath.trim().replace(/[\\/]+$/, "");
  if (!cleaned) {
    return "";
  }
  const parts = cleaned.split(/[\\/]/).filter(Boolean);
  return parts.length > 0 ? parts[parts.length - 1] : cleaned;
}

function formatDirectoryLabel(rawPath: string): string {
  const trimmed = rawPath.trim();
  if (!trimmed) {
    return "";
  }
  if (trimmed === "/" || trimmed.length <= 44) {
    return trimmed;
  }
  const parts = trimmed.replace(/[\\/]+$/, "").split(/[\\/]/).filter(Boolean);
  if (parts.length >= 2) {
    return `…/${parts.slice(-2).join("/")}`;
  }
  return truncateConcise(trimmed, 44);
}

export function formatToolTarget(
  args: Record<string, unknown>,
  toolName?: string,
): string | undefined {
  const keys =
    toolName && getExplorationToolCategory(toolName) === "search"
      ? SEARCH_ARG_KEYS
      : DEFAULT_ARG_KEYS;
  return firstNonEmptyString(args, keys);
}

function isCallInProgress(call: ToolCallRecord): boolean {
  return call.status === "running" || call.status === "pending";
}

export function formatExplorationLiveStatus(calls: readonly ToolCallRecord[]): string {
  if (calls.length === 0) {
    return "코드베이스 탐색 중...";
  }
  let activeCall: ToolCallRecord | undefined;
  for (let i = calls.length - 1; i >= 0; i -= 1) {
    if (isCallInProgress(calls[i])) {
      activeCall = calls[i];
      break;
    }
  }
  const targetCall = activeCall ?? calls[calls.length - 1];
  const category = getExplorationToolCategory(targetCall.name);

  if (category === "read") {
    const rawPath = firstNonEmptyString(targetCall.args, ["file_path", "path", "file", "target"]);
    const fileName = rawPath ? extractFileBasename(rawPath) : "";
    return fileName ? `${truncateConcise(fileName, 44)} 읽는 중...` : "파일 읽는 중...";
  }

  if (category === "search") {
    const pattern = firstNonEmptyString(targetCall.args, ["pattern", "query", "q", "regex"]);
    if (pattern) {
      return `"${truncateConcise(pattern, 40)}" 검색 중...`;
    }
    const searchPath = firstNonEmptyString(targetCall.args, ["path", "dir", "directory"]);
    return searchPath ? `${formatDirectoryLabel(searchPath)} 검색 중...` : "코드 검색 중...";
  }

  if (category === "list") {
    const dirPath = firstNonEmptyString(targetCall.args, ["path", "dir", "directory", "file_path"]);
    const label = dirPath ? formatDirectoryLabel(dirPath) : "";
    return label ? `${label} 탐색 중...` : "폴더 탐색 중...";
  }

  return "코드베이스 탐색 중...";
}

export function formatExplorationSummary(calls: readonly ToolCallRecord[]): string {
  let listCount = 0;
  let readCount = 0;
  let searchCount = 0;

  for (const call of calls) {
    const category = getExplorationToolCategory(call.name);
    if (category === "list") {
      listCount += 1;
    } else if (category === "read") {
      readCount += 1;
    } else if (category === "search") {
      searchCount += 1;
    }
  }

  const parts: string[] = [];
  if (listCount > 0) {
    parts.push(`폴더 ${listCount}개 탐색`);
  }
  if (readCount > 0) {
    parts.push(`파일 ${readCount}개 읽음`);
  }
  if (searchCount > 0) {
    parts.push(`검색 ${searchCount}회`);
  }

  return parts.length > 0 ? parts.join(" · ") : `탐색 ${calls.length}회`;
}

function stableCallToken(call: ToolCallRecord, index: number): string {
  const trimmedId = call.id.trim();
  return trimmedId.length > 0 ? trimmedId : `${normalizeToolName(call.name) || "tool"}-${index}`;
}

export function parseReviewFindingCounts(output: string): ReviewFindingCounts {
  const important = (output.match(/\[\s*important\s*\]/gi) ?? []).length;
  const nit = (output.match(/\[\s*nit\s*\]/gi) ?? []).length;
  const preExisting = (output.match(/\[\s*pre-existing\s*\]/gi) ?? []).length;
  return { important, nit, preExisting };
}

function classifySubagentRole(rawType: string): { role: SubagentRole; roleLabel: string } {
  const lowered = rawType.trim().toLowerCase();
  if (lowered === "reviewer") {
    return { role: "reviewer", roleLabel: "Code Reviewer" };
  }
  if (lowered === "explorer") {
    return { role: "explorer", roleLabel: "Explorer" };
  }
  const clean = rawType.trim();
  return {
    role: "general",
    roleLabel: clean.length > 0 ? clean : "Subagent",
  };
}

function summarizeTaskDescription(rawDesc: string | undefined, role: SubagentRole): string {
  if (!rawDesc || !rawDesc.trim()) {
    if (role === "reviewer") return "독립 코드 리뷰 수행";
    if (role === "explorer") return "코드베이스 탐색 수행";
    return "위임 작업 수행";
  }
  const firstLine =
    rawDesc
      .split(/\r?\n/)
      .map((line) => line.replace(/^#+\s*/, "").trim())
      .find((line) => line.length > 0) ?? rawDesc.trim();
  return truncateConcise(firstLine, 84);
}

function buildSubagentRunItem(
  taskCall: ToolCallRecord,
  taskIndex: number,
  nestedCalls: readonly ToolCallRecord[],
  options: GroupToolCallsOptions,
  prefix: string,
): SubagentRunItem {
  const rawType =
    firstNonEmptyString(taskCall.args, ["subagent_type", "agent", "type"]) ?? "subagent";
  const { role, roleLabel } = classifySubagentRole(rawType);
  const rawDesc = firstNonEmptyString(taskCall.args, ["description", "prompt", "task"]);
  const description = summarizeTaskDescription(rawDesc, role);

  const isError = taskCall.status === "error" || taskCall.status === "cancelled";
  const isRunning =
    options.isStreaming && (isCallInProgress(taskCall) || nestedCalls.some(isCallInProgress));
  const status: SubagentStatus = isError ? "error" : isRunning ? "running" : "completed";

  const durationMs =
    typeof taskCall.duration_ms === "number" &&
    Number.isFinite(taskCall.duration_ms) &&
    taskCall.duration_ms >= 0
      ? taskCall.duration_ms
      : null;

  let activeLabel: string;
  if (nestedCalls.length > 0) {
    activeLabel = `${formatExplorationLiveStatus(nestedCalls)} · 도구 ${nestedCalls.length}회 호출`;
  } else if (role === "reviewer") {
    activeLabel = "독립 컨텍스트에서 변경 코드 검토 중...";
  } else if (role === "explorer") {
    activeLabel = "독립 컨텍스트에서 코드베이스 탐색 중...";
  } else {
    activeLabel = "독립 컨텍스트에서 작업 수행 중...";
  }

  let toolClause = "도구 호출 없음";
  if (nestedCalls.length > 0) {
    const expCalls = nestedCalls.filter((c) => isReadExplorationTool(c.name));
    if (expCalls.length > 0) {
      toolClause = `도구 ${nestedCalls.length}회 사용 (${formatExplorationSummary(expCalls)})`;
    } else {
      toolClause = `도구 ${nestedCalls.length}회 사용`;
    }
  }
  const durationClause = durationMs !== null ? ` · ${formatCompactDuration(durationMs)}` : "";
  const summaryPrefix = status === "error" ? "실패" : "완료";
  const summaryLabel = `${summaryPrefix} · ${toolClause}${durationClause}`;

  const output = taskCall.output ?? "";
  const findingCounts =
    role === "reviewer" && output.trim().length > 0 ? parseReviewFindingCounts(output) : null;

  return {
    id: `${prefix}:subagent-item:${taskIndex}:${stableCallToken(taskCall, taskIndex)}`,
    taskCall,
    subagentType: rawType,
    role,
    roleLabel,
    description,
    status,
    nestedCalls,
    activeLabel,
    summaryLabel,
    output,
    durationMs,
    findingCounts,
  };
}

export function groupToolCalls(
  records: readonly ToolCallRecord[],
  options: GroupToolCallsOptions,
): GroupedToolBlock[] {
  if (records.length === 0) {
    return [];
  }

  const prefix = options.groupKeyPrefix?.trim() || "turn";
  const splitSet = new Set<number>(options.textSplitIndices ?? []);
  const blocks: GroupedToolBlock[] = [];

  const taskIndices: number[] = [];
  const taskIndexById = new Map<string, number>();
  for (let i = 0; i < records.length; i += 1) {
    const call = records[i];
    if (normalizeToolName(call.name) === "task" && !call.parent_id) {
      taskIndices.push(i);
      if (call.id.trim().length > 0) {
        taskIndexById.set(call.id.trim(), i);
      }
    }
  }

  const childrenByTaskIndex = new Map<number, ToolCallRecord[]>();
  const consumedChildIndices = new Set<number>();
  if (taskIndices.length > 0) {
    for (const tIdx of taskIndices) {
      childrenByTaskIndex.set(tIdx, []);
    }
    for (let i = 0; i < records.length; i += 1) {
      if (childrenByTaskIndex.has(i)) {
        continue;
      }
      const call = records[i];
      const parentId = call.parent_id?.trim();
      if (parentId && taskIndexById.has(parentId)) {
        const ownerIdx = taskIndexById.get(parentId)!;
        childrenByTaskIndex.get(ownerIdx)?.push(call);
        consumedChildIndices.add(i);
        continue;
      }
      if (!parentId && call.subagent) {
        const precedingTasks = taskIndices.filter((tIdx) => tIdx < i);
        if (precedingTasks.length > 0) {
          const subLabel = call.subagent.trim().toLowerCase();
          const matchedIdx =
            [...precedingTasks].reverse().find((tIdx) => {
              const tType = firstNonEmptyString(records[tIdx].args, ["subagent_type"]);
              return tType?.toLowerCase() === subLabel;
            }) ?? precedingTasks[precedingTasks.length - 1];
          childrenByTaskIndex.get(matchedIdx)?.push(call);
          consumedChildIndices.add(i);
        }
      }
    }
  }

  let buffer: ToolCallRecord[] = [];
  let bufferStartIndex = -1;
  let subagentBuffer: SubagentRunItem[] = [];
  let subagentStartIndex = -1;

  const flushBuffer = (isTrailing: boolean) => {
    if (buffer.length === 0) {
      return;
    }
    const groupCalls = buffer.slice();
    const startIdx = bufferStartIndex;
    buffer = [];
    bufferStartIndex = -1;

    const hasActiveCall = groupCalls.some(isCallInProgress);
    const status: ExplorationGroupStatus =
      isTrailing && options.isStreaming && !options.hasAssistantTextStarted && hasActiveCall
        ? "running"
        : "completed";

    blocks.push({
      kind: "exploration_group",
      id: `${prefix}:exploration:${startIdx}:${stableCallToken(groupCalls[0], startIdx)}`,
      calls: groupCalls,
      status,
      activeLabel: formatExplorationLiveStatus(groupCalls),
      summaryLabel: formatExplorationSummary(groupCalls),
    });
  };

  const flushSubagents = () => {
    if (subagentBuffer.length === 0) {
      return;
    }
    const items = subagentBuffer.slice();
    const startIdx = subagentStartIndex;
    subagentBuffer = [];
    subagentStartIndex = -1;

    const runningCount = items.filter((it) => it.status === "running").length;
    blocks.push({
      kind: "subagent_group",
      id: `${prefix}:subagent:${startIdx}:${items[0].taskCall.id || startIdx}`,
      items,
      isParallel: items.length > 1,
      runningCount,
    });
  };

  for (let i = 0; i < records.length; i += 1) {
    if (splitSet.has(i)) {
      flushBuffer(false);
      flushSubagents();
    }

    if (consumedChildIndices.has(i)) {
      continue;
    }

    const call = records[i];
    if (childrenByTaskIndex.has(i)) {
      flushBuffer(false);
      if (subagentBuffer.length === 0) {
        subagentStartIndex = i;
      }
      const nested = childrenByTaskIndex.get(i) ?? [];
      subagentBuffer.push(buildSubagentRunItem(call, i, nested, options, prefix));
    } else if (isReadExplorationTool(call.name)) {
      flushSubagents();
      if (buffer.length === 0) {
        bufferStartIndex = i;
      }
      buffer.push(call);
    } else {
      flushBuffer(false);
      flushSubagents();
      blocks.push({
        kind: "single_tool",
        id: `${prefix}:tool:${i}:${stableCallToken(call, i)}`,
        call,
      });
    }
  }

  flushBuffer(true);
  flushSubagents();
  return blocks;
}

function formatCompactDuration(ms: number): string {
  const safeMs = Number.isFinite(ms) ? Math.max(0, ms) : 0;
  if (safeMs < 1000) {
    return "<1s";
  }

  const totalSeconds = Math.floor(safeMs / 1000);
  const hours = Math.floor(totalSeconds / 3600);
  const minutes = Math.floor((totalSeconds % 3600) / 60);
  const seconds = totalSeconds % 60;

  const parts: string[] = [];
  if (hours > 0) {
    parts.push(`${hours}h`);
  }
  if (minutes > 0) {
    parts.push(`${minutes}m`);
  }
  if (seconds > 0) {
    parts.push(`${seconds}s`);
  }
  return parts.join(" ");
}

export function formatWorkedDuration(ms: number): string {
  return `Worked for ${formatCompactDuration(ms)}`;
}

export function formatWorkingDuration(ms: number): string {
  return `Working · ${formatCompactDuration(ms)}`;
}

export function computeAssistantTurnDurationMs(
  messages: readonly ChatMessage[],
  assistantIndex: number,
  recordedDurationsMs?: Readonly<Record<string, number>>,
): number | null {
  if (assistantIndex < 0 || assistantIndex >= messages.length) {
    return null;
  }
  const msg = messages[assistantIndex];
  if (msg.role !== "assistant") {
    return null;
  }

  const recorded = recordedDurationsMs?.[msg.id];
  if (typeof recorded === "number" && Number.isFinite(recorded) && recorded >= 0) {
    return recorded;
  }

  if (assistantIndex === 0) {
    return null;
  }
  const preceding = messages[assistantIndex - 1];
  if (preceding.role !== "user") {
    return null;
  }

  const assistantMs = Date.parse(msg.created_at);
  const userMs = Date.parse(preceding.created_at);
  if (!Number.isFinite(assistantMs) || !Number.isFinite(userMs)) {
    return null;
  }

  const deltaMs = assistantMs - userMs;
  if (deltaMs < 0 || deltaMs >= MAX_DERIVED_TURN_DURATION_MS) {
    return null;
  }
  return deltaMs;
}
