import { useState } from "react";
import { Theme } from "@astryxdesign/core/theme";
import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import type { ToolCallRecord } from "../../../api/types";
import { defaultThemeSlug, themes } from "../../../astryx-themes";
import { groupToolCalls } from "../../../utils/toolCallGroups";
import { SubagentGroupView } from "../SubagentCardView";

function Harness({
  records,
  isStreaming = false,
}: {
  records: ToolCallRecord[];
  isStreaming?: boolean;
}) {
  const [openOverrides, setOpenOverrides] = useState<Record<string, boolean>>({});
  const blocks = groupToolCalls(records, {
    isStreaming,
    hasAssistantTextStarted: !isStreaming,
    groupKeyPrefix: "test",
  });
  const subBlock = blocks.find((b) => b.kind === "subagent_group");
  if (!subBlock || subBlock.kind !== "subagent_group") {
    return null;
  }
  return (
    <Theme theme={themes[defaultThemeSlug]}>
      <SubagentGroupView
        group={subBlock}
        openOverrides={openOverrides}
        onToggle={(id, next) => setOpenOverrides((prev) => ({ ...prev, [id]: next }))}
      />
    </Theme>
  );
}

describe("SubagentGroupView", () => {
  it("renders a collapsed Code Reviewer card with summary telemetry and expands nested tools + report on click", () => {
    const records: ToolCallRecord[] = [
      {
        id: "t-rev",
        name: "task",
        args: {
          subagent_type: "reviewer",
          description: "SQLite 저널 모드 및 ShellGuard 변경 사항 독립 리뷰",
        },
        status: "success",
        output: "### 리뷰 요약\n- [Nit] `config.py`: 주석 보강 가능",
        duration_ms: 3100,
      },
      {
        id: "c-1",
        name: "read_file",
        args: { file_path: "/backend/app/shared/config.py" },
        status: "success",
        output: "lines",
        subagent: "reviewer",
        parent_id: "t-rev",
      },
    ];

    render(<Harness records={records} />);

    expect(screen.getByText("Code Reviewer")).toBeInTheDocument();
    expect(
      screen.getByText("SQLite 저널 모드 및 ShellGuard 변경 사항 독립 리뷰"),
    ).toBeInTheDocument();
    expect(screen.getByText("Clean · 1 Nit")).toBeInTheDocument();
    expect(
      screen.getByText("완료 · 도구 1회 사용 (파일 1개 읽음) · 3s"),
    ).toBeInTheDocument();

    // Collapsed by default when zero [Important] issues
    expect(screen.queryByText("독립 코드 리뷰 결과")).not.toBeInTheDocument();

    // Expand card -> shows nested tool calls and formatted Markdown review report
    fireEvent.click(
      screen.getByText("SQLite 저널 모드 및 ShellGuard 변경 사항 독립 리뷰"),
    );
    expect(screen.getByText("내부 도구 호출 (1)")).toBeInTheDocument();
    expect(screen.getByText("독립 코드 리뷰 결과")).toBeInTheDocument();
    expect(screen.getByText(/주석 보강 가능/)).toBeInTheDocument();
  });

  it("auto-expands Code Reviewer card immediately when [Important] findings exist", () => {
    const records: ToolCallRecord[] = [
      {
        id: "t-imp",
        name: "task",
        args: {
          subagent_type: "reviewer",
          description: "인증 미들웨어 보안 점검",
        },
        status: "success",
        output: "- [Important] `auth.py`: 토큰 만료 검증 누락",
        duration_ms: 1800,
      },
    ];

    render(<Harness records={records} />);

    expect(screen.getByText("1 Important")).toBeInTheDocument();
    expect(screen.getByText("독립 코드 리뷰 결과")).toBeInTheDocument();
    expect(screen.getByText(/토큰 만료 검증 누락/)).toBeInTheDocument();
  });
});
