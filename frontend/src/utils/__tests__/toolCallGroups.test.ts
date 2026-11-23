import { describe, expect, it } from "vitest";
import type { ChatMessage, ToolCallRecord } from "../../api/types";
import {
  computeAssistantTurnDurationMs,
  formatExplorationLiveStatus,
  formatExplorationSummary,
  formatWorkedDuration,
  formatWorkingDuration,
  groupToolCalls,
  isReadExplorationTool,
} from "../toolCallGroups";

function makeTool(
  id: string,
  name: string,
  args: Record<string, unknown> = {},
  status = "success",
  output = "",
): ToolCallRecord {
  return { id, name, args, status, output, subagent: null };
}

function makeMessage(
  id: string,
  role: "user" | "assistant",
  createdAt: string,
): ChatMessage {
  return {
    id,
    session_id: "sess-1",
    role,
    content: role === "user" ? "Question" : "Answer",
    thinking: "",
    tool_calls: [],
    created_at: createdAt,
  };
}

describe("isReadExplorationTool", () => {
  it("classifies read-only exploration tools as true", () => {
    for (const tool of [
      "ls",
      "glob",
      "grep",
      "read_file",
      "list_files",
      "find_files",
      "search",
      "search_files",
      "view_file",
      " READ_FILE ",
    ]) {
      expect(isReadExplorationTool(tool)).toBe(true);
    }
  });

  it("classifies mutating, shell, and delegation tools as false", () => {
    for (const tool of [
      "edit_file",
      "write_file",
      "delete",
      "execute",
      "bash",
      "task",
      "write_todos",
      "custom_tool",
    ]) {
      expect(isReadExplorationTool(tool)).toBe(false);
    }
  });
});

describe("groupToolCalls", () => {
  it("returns an empty array when there are no tool calls", () => {
    expect(
      groupToolCalls([], {
        isStreaming: false,
        hasAssistantTextStarted: false,
        groupKeyPrefix: "m1",
      }),
    ).toEqual([]);
  });

  it("merges consecutive read exploration calls without mutating raw input", () => {
    const raw: ToolCallRecord[] = [
      makeTool("tc-1", "ls", { path: "/backend" }),
      makeTool("tc-2", "read_file", { file_path: "/backend/app.py" }),
      makeTool("tc-3", "grep", { pattern: "DeepAgentHarness", path: "/backend" }),
    ];
    const snapshot = structuredClone(raw);

    const blocks = groupToolCalls(raw, {
      isStreaming: false,
      hasAssistantTextStarted: false,
      groupKeyPrefix: "m1",
    });

    expect(raw).toEqual(snapshot);
    expect(blocks).toHaveLength(1);
    expect(blocks[0].kind).toBe("exploration_group");
    if (blocks[0].kind === "exploration_group") {
      expect(blocks[0].calls).toHaveLength(3);
      expect(blocks[0].status).toBe("completed");
      expect(blocks[0].summaryLabel).toBe("폴더 1개 탐색 · 파일 1개 읽음 · 검색 1회");
    }
  });

  it("splits groups immediately on edit_file, write_file, execute, and task", () => {
    const raw: ToolCallRecord[] = [
      makeTool("tc-1", "ls", { path: "/" }),
      makeTool("tc-2", "read_file", { file_path: "/a.py" }),
      makeTool("tc-3", "edit_file", { file_path: "/a.py" }),
      makeTool("tc-4", "read_file", { file_path: "/b.py" }),
      makeTool("tc-5", "glob", { pattern: "**/*.ts" }),
      makeTool("tc-6", "write_file", { file_path: "/c.py" }),
      makeTool("tc-7", "execute", { command: "pytest -q" }),
      makeTool("tc-8", "view_file", { path: "/README.md" }),
    ];

    const blocks = groupToolCalls(raw, {
      isStreaming: false,
      hasAssistantTextStarted: false,
      groupKeyPrefix: "m2",
    });

    expect(blocks.map((b) => b.kind)).toEqual([
      "exploration_group",
      "single_tool",
      "exploration_group",
      "single_tool",
      "single_tool",
      "exploration_group",
    ]);
    if (blocks[0].kind === "exploration_group") {
      expect(blocks[0].calls.map((c) => c.id)).toEqual(["tc-1", "tc-2"]);
    }
    if (blocks[1].kind === "single_tool") {
      expect(blocks[1].call.name).toBe("edit_file");
    }
    if (blocks[2].kind === "exploration_group") {
      expect(blocks[2].calls.map((c) => c.id)).toEqual(["tc-4", "tc-5"]);
    }
    if (blocks[3].kind === "single_tool") {
      expect(blocks[3].call.name).toBe("write_file");
    }
    if (blocks[4].kind === "single_tool") {
      expect(blocks[4].call.name).toBe("execute");
    }
    if (blocks[5].kind === "exploration_group") {
      expect(blocks[5].calls.map((c) => c.id)).toEqual(["tc-8"]);
    }
  });

  it("splits consecutive read calls when interleaved assistant text boundary is present", () => {
    const raw: ToolCallRecord[] = [
      makeTool("tc-1", "ls", { path: "/backend" }),
      makeTool("tc-2", "read_file", { file_path: "/backend/main.py" }),
      makeTool("tc-3", "read_file", { file_path: "/frontend/App.tsx" }, "running"),
    ];

    const blocks = groupToolCalls(raw, {
      isStreaming: true,
      hasAssistantTextStarted: false,
      textSplitIndices: [2],
      groupKeyPrefix: "stream",
    });

    expect(blocks).toHaveLength(2);
    expect(blocks[0].kind).toBe("exploration_group");
    expect(blocks[1].kind).toBe("exploration_group");
    if (blocks[0].kind === "exploration_group" && blocks[1].kind === "exploration_group") {
      expect(blocks[0].calls.map((c) => c.id)).toEqual(["tc-1", "tc-2"]);
      expect(blocks[0].status).toBe("completed");
      expect(blocks[1].calls.map((c) => c.id)).toEqual(["tc-3"]);
      expect(blocks[1].status).toBe("running");
      expect(blocks[1].activeLabel).toBe("App.tsx 읽는 중...");
    }
  });

  it("marks trailing exploration group running ONLY when streaming, no assistant text started, and an active call exists", () => {
    const runningCalls: ToolCallRecord[] = [
      makeTool("tc-1", "ls", { path: "/backend" }, "success"),
      makeTool("tc-2", "read_file", { file_path: "/backend/app.py" }, "running"),
    ];

    const active = groupToolCalls(runningCalls, {
      isStreaming: true,
      hasAssistantTextStarted: false,
      groupKeyPrefix: "stream",
    });
    expect(active[0].kind === "exploration_group" && active[0].status).toBe("running");

    const textStarted = groupToolCalls(runningCalls, {
      isStreaming: true,
      hasAssistantTextStarted: true,
      groupKeyPrefix: "stream",
    });
    expect(textStarted[0].kind === "exploration_group" && textStarted[0].status).toBe("completed");

    const notStreaming = groupToolCalls(runningCalls, {
      isStreaming: false,
      hasAssistantTextStarted: false,
      groupKeyPrefix: "m1",
    });
    expect(notStreaming[0].kind === "exploration_group" && notStreaming[0].status).toBe("completed");

    const allFinished = groupToolCalls(
      [
        makeTool("tc-1", "ls", { path: "/backend" }, "success"),
        makeTool("tc-2", "read_file", { file_path: "/backend/app.py" }, "success"),
      ],
      {
        isStreaming: true,
        hasAssistantTextStarted: false,
        groupKeyPrefix: "stream",
      },
    );
    expect(allFinished[0].kind === "exploration_group" && allFinished[0].status).toBe("completed");

    const nonTrailingGroup = groupToolCalls(
      [
        makeTool("tc-1", "read_file", { file_path: "/backend/app.py" }, "running"),
        makeTool("tc-2", "edit_file", { file_path: "/backend/app.py" }, "running"),
      ],
      {
        isStreaming: true,
        hasAssistantTextStarted: false,
        groupKeyPrefix: "stream",
      },
    );
    expect(
      nonTrailingGroup[0].kind === "exploration_group" && nonTrailingGroup[0].status,
    ).toBe("completed");
  });

  it("keeps group IDs stable as additional calls stream in", () => {
    const step1 = groupToolCalls([makeTool("tc-1", "ls", { path: "/" }, "running")], {
      isStreaming: true,
      hasAssistantTextStarted: false,
      groupKeyPrefix: "sess-1:stream",
    });
    const step2 = groupToolCalls(
      [
        makeTool("tc-1", "ls", { path: "/" }, "success"),
        makeTool("tc-2", "read_file", { file_path: "/README.md" }, "running"),
      ],
      {
        isStreaming: true,
        hasAssistantTextStarted: false,
        groupKeyPrefix: "sess-1:stream",
      },
    );
    const step3 = groupToolCalls(
      [
        makeTool("tc-1", "ls", { path: "/" }, "success"),
        makeTool("tc-2", "read_file", { file_path: "/README.md" }, "success"),
        makeTool("tc-3", "edit_file", { file_path: "/README.md" }, "success"),
        makeTool("tc-4", "grep", { pattern: "SessionService" }, "running"),
      ],
      {
        isStreaming: true,
        hasAssistantTextStarted: false,
        groupKeyPrefix: "sess-1:stream",
      },
    );

    expect(step1[0].id).toBe(step2[0].id);
    expect(step2[0].id).toBe(step3[0].id);
    expect(step3[2].id).not.toBe(step3[0].id);
  });
});

describe("formatExplorationLiveStatus & formatExplorationSummary", () => {
  it("formats concise live labels for read, search, and directory exploration calls", () => {
    expect(
      formatExplorationLiveStatus([
        makeTool("tc-1", "ls", { path: "/backend" }, "success"),
        makeTool("tc-2", "read_file", { file_path: "/backend/app/app.py" }, "running"),
      ]),
    ).toBe("app.py 읽는 중...");

    expect(
      formatExplorationLiveStatus([
        makeTool("tc-1", "grep", { pattern: "DeepAgentHarness", path: "/backend" }, "running"),
      ]),
    ).toBe('"DeepAgentHarness" 검색 중...');

    expect(
      formatExplorationLiveStatus([makeTool("tc-1", "ls", { path: "/backend" }, "running")]),
    ).toBe("/backend 탐색 중...");

    expect(formatExplorationLiveStatus([makeTool("tc-1", "read_file", {}, "running")])).toBe(
      "파일 읽는 중...",
    );
  });

  it("formats concise non-zero summaries", () => {
    expect(
      formatExplorationSummary([
        makeTool("1", "read_file", { file_path: "/a.py" }),
        makeTool("2", "read_file", { file_path: "/b.py" }),
        makeTool("3", "read_file", { file_path: "/c.py" }),
        makeTool("4", "view_file", { file_path: "/d.py" }),
        makeTool("5", "grep", { pattern: "foo" }),
        makeTool("6", "glob", { pattern: "*.py" }),
      ]),
    ).toBe("파일 4개 읽음 · 검색 2회");

    expect(
      formatExplorationSummary([
        makeTool("1", "ls", { path: "/backend" }),
        makeTool("2", "read_file", { file_path: "/a.py" }),
        makeTool("3", "read_file", { file_path: "/b.py" }),
        makeTool("4", "read_file", { file_path: "/c.py" }),
      ]),
    ).toBe("폴더 1개 탐색 · 파일 3개 읽음");
  });
});

describe("formatWorkedDuration & computeAssistantTurnDurationMs", () => {
  it("formats worked and live working durations concisely", () => {
    expect(formatWorkedDuration(0)).toBe("Worked for <1s");
    expect(formatWorkedDuration(850)).toBe("Worked for <1s");
    expect(formatWorkedDuration(-500)).toBe("Worked for <1s");
    expect(formatWorkedDuration(Number.NaN)).toBe("Worked for <1s");
    expect(formatWorkedDuration(8000)).toBe("Worked for 8s");
    expect(formatWorkedDuration(60_000)).toBe("Worked for 1m");
    expect(formatWorkedDuration(74_000)).toBe("Worked for 1m 14s");
    expect(formatWorkedDuration(3_600_000)).toBe("Worked for 1h");
    expect(formatWorkedDuration(3_665_000)).toBe("Worked for 1h 1m 5s");

    expect(formatWorkingDuration(400)).toBe("Working · <1s");
    expect(formatWorkingDuration(3_200)).toBe("Working · 3s");
    expect(formatWorkingDuration(74_000)).toBe("Working · 1m 14s");
  });

  it("prefers recorded duration and falls back to valid preceding user timestamp delta", () => {
    const messages: ChatMessage[] = [
      makeMessage("u1", "user", "2026-11-22T00:00:00.000Z"),
      makeMessage("a1", "assistant", "2026-11-22T00:00:08.500Z"),
      makeMessage("a2", "assistant", "2026-11-22T00:00:15.000Z"),
      makeMessage("u2", "user", "2026-11-22T00:01:00.000Z"),
      makeMessage("a3", "assistant", "2026-11-22T00:00:50.000Z"), // negative delta
      makeMessage("u3", "user", "2026-11-22T00:00:00.000Z"),
      makeMessage("a4", "assistant", "2026-11-23T00:00:00.000Z"), // exactly 24h -> invalid
      makeMessage("u4", "user", "not-a-date"),
      makeMessage("a5", "assistant", "2026-11-22T00:00:05.000Z"),
    ];

    expect(computeAssistantTurnDurationMs(messages, 0)).toBeNull();
    expect(computeAssistantTurnDurationMs(messages, 1)).toBe(8500);
    expect(computeAssistantTurnDurationMs(messages, 1, { a1: 4200 })).toBe(4200);
    expect(computeAssistantTurnDurationMs(messages, 2)).toBeNull();
    expect(computeAssistantTurnDurationMs(messages, 2, { a2: 6100 })).toBe(6100);
    expect(computeAssistantTurnDurationMs(messages, 4)).toBeNull();
    expect(computeAssistantTurnDurationMs(messages, 6)).toBeNull();
    expect(computeAssistantTurnDurationMs(messages, 8)).toBeNull();
  });
});
