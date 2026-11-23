import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { Markdown } from "@astryxdesign/core/Markdown";
import { Theme } from "@astryxdesign/core/theme";
import { afterEach, describe, expect, it } from "vitest";
import { defaultThemeSlug, themes } from "../../../astryx-themes";
import {
  measureGlyphFloorWidth,
  normalizeMermaidSourceForRender,
  renderMermaidSafe,
  sanitizeMermaidSvgToFragment,
} from "../../../utils/mermaidRenderer";
import { MermaidDiagram } from "../MermaidDiagram";
import {
  MARKDOWN_PLUGINS,
  STREAMING_MARKDOWN_PLUGINS,
} from "../markdownPlugins";

afterEach(() => {
  cleanup();
});

function renderWithTheme(ui: React.ReactElement) {
  return render(<Theme theme={themes[defaultThemeSlug]}>{ui}</Theme>);
}

describe("mermaidRenderer sanitization and auto-fix", () => {
  it("strips script, foreignObject, event handlers, and external style @import rules", () => {
    const tag = (name: string, attrs = "", body = "") =>
      `\u003c${name}${attrs ? ` ${attrs}` : ""}>${body}\u003c/${name}>`;

    const dirtySvg = tag(
      "svg",
      'xmlns="http://www.w3.org/2000/svg" viewBox="0 0 120 60"',
      [
        tag(
          "style",
          "",
          '@import url("https://evil.example/font.css"); text { fill: var(--_fg); }',
        ),
        tag("script", "", "window.__xss = true;"),
        tag(
          "foreignObject",
          'width="50" height="20"',
          tag(
            "body",
            'xmlns="http://www.w3.org/1999/xhtml"',
            '\u003cimg src="x" onerror="alert(1)" />',
          ),
        ),
        '\u003crect x="0" y="0" width="80" height="30" onclick="alert(1)" />',
        tag("text", 'x="10" y="20"', "Safe Node"),
      ].join(""),
    );

    const fragment = sanitizeMermaidSvgToFragment(dirtySvg);
    const svg = fragment.querySelector("svg");

    expect(svg).not.toBeNull();
    expect(fragment.querySelector("script")).toBeNull();
    expect(fragment.querySelector("foreignObject")).toBeNull();
    expect(fragment.querySelector("rect")?.getAttribute("onclick")).toBeNull();
    expect(fragment.querySelector("style")?.textContent).not.toContain("@import");
    expect(fragment.textContent).toContain("Safe Node");
    expect(svg?.style.getPropertyValue("--pele-fg")).toBe(
      "var(--color-text-primary)",
    );
  });

  it("auto-repairs multi-pass broken syntax via @probelabs/maid and renders via pele/lazy", async () => {
    const broken = "flowchart TD\n  A[Item (1)] --> B[Item (2)]";
    const result = await renderMermaidSafe(broken);

    expect(result.ok).toBe(true);
    if (result.ok) {
      expect(result.repaired).toBe(true);
      expect(result.fixCount).toBeGreaterThanOrEqual(2);
      expect(result.svg).toContain("\u003csvg");
      expect(result.diagramType).toBe("flowchart");
      expect(result.width).toBeGreaterThan(80);
      expect(result.height).toBeGreaterThan(80);
    }
  });

  it("measures Korean subgraph headers and strips sequence participant outer quotes", async () => {
    expect(measureGlyphFloorWidth("", 14, 0)).toBe(0);
    expect(measureGlyphFloorWidth("프롬프트", 14, 0)).toBeGreaterThan(
      measureGlyphFloorWidth("abcd", 14, 0),
    );

    const koreanFlowchart = [
      "flowchart TD",
      '  REQ["모델 호출 / 도구 실행 요청"]',
      '  subgraph SG["프롬프트-기억 계층 (awrap_model_call / before_agent)"]',
      '    M1["1. TodoListMiddleware\\nwrite_todos 상태 관리"]',
      "  end",
      "  REQ --> M1",
    ].join("\n");

    const flowResult = await renderMermaidSafe(koreanFlowchart);
    expect(flowResult.ok).toBe(true);
    if (flowResult.ok) {
      expect(flowResult.width).toBeGreaterThanOrEqual(450);
      const fragment = sanitizeMermaidSvgToFragment(flowResult.svg);
      const clusterRect = fragment.querySelector(
        'g.pele-cluster[data-id="SG"] > rect',
      );
      const clusterWidth = Number(clusterRect?.getAttribute("width") ?? "0");
      expect(clusterWidth).toBeGreaterThanOrEqual(420);
    }

    const quotedSequence = [
      "sequenceDiagram",
      '  participant UI as "사용자 UI (Composer)"',
      '  participant R as "sessions/router.py (SSE)"',
      "  UI->>R: stream_message",
    ].join("\n");

    expect(normalizeMermaidSourceForRender(quotedSequence)).toContain(
      "participant UI as 사용자 UI (Composer)",
    );

    const seqResult = await renderMermaidSafe(quotedSequence);
    expect(seqResult.ok).toBe(true);
    if (seqResult.ok) {
      expect(seqResult.svg).toContain("사용자 UI (Composer)");
      expect(seqResult.svg).not.toContain("&quot;사용자 UI (Composer)&quot;");
    }
  });
});

describe("MermaidDiagram and Astryx Markdown integration", () => {
  it("renders a valid flowchart inside Markdown via pele/lazy and toggles Diagram / Source", async () => {
    const md = [
      "Architecture overview:",
      "",
      "```mermaid System Flow",
      "flowchart LR",
      "  Client[Browser UI] --> API[FastAPI]",
      "```",
    ].join("\n");

    const { container } = renderWithTheme(
      <Markdown density="compact" plugins={MARKDOWN_PLUGINS}>
        {md}
      </Markdown>,
    );

    await waitFor(() => {
      expect(container.querySelector("svg.pele")).not.toBeNull();
    });

    expect(screen.getByText("Mermaid · System Flow")).toBeInTheDocument();
    expect(screen.queryByText("Auto-fixed")).not.toBeInTheDocument();

    fireEvent.click(screen.getByRole("radio", { name: "Source" }));
    await waitFor(() => {
      expect(container.querySelector("svg.pele")).toBeNull();
    });
    expect(screen.getByText(/Client\[Browser UI\]/)).toBeInTheDocument();

    fireEvent.click(screen.getByRole("radio", { name: "Diagram" }));
    await waitFor(() => {
      expect(container.querySelector("svg.pele")).not.toBeNull();
    });
  });

  it("auto-repairs broken Mermaid syntax and displays the Auto-fixed badge", async () => {
    const brokenArrow = "flowchart TD\n  A[Start] -> B[End]";

    const { container } = renderWithTheme(
      <MermaidDiagram code={brokenArrow} />,
    );

    await waitFor(() => {
      expect(container.querySelector("svg.pele")).not.toBeNull();
    });

    expect(screen.getByText("Auto-fixed")).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "Expand" }));
    await waitFor(() => {
      const dialog = container.querySelector("dialog[open]");
      expect(dialog).not.toBeNull();
      expect(dialog?.querySelector("svg.pele")).not.toBeNull();
    });
  });

  it("renders raw CodeBlock fallback while streaming (isFinal=false)", async () => {
    const partialSource = "flowchart TD\n  A[Streaming";
    const { container, rerender } = renderWithTheme(
      <MermaidDiagram code={partialSource} isFinal={false} />,
    );

    expect(container.querySelector("svg.pele")).toBeNull();
    expect(screen.queryByText("Rendering Mermaid diagram...")).not.toBeInTheDocument();
    expect(screen.getByText(/A\[Streaming/)).toBeInTheDocument();

    const partialMd = "```mermaid\nflowchart TD\n  A[StreamingNode]\n```";
    rerender(
      <Theme theme={themes[defaultThemeSlug]}>
        <Markdown
          density="compact"
          isStreaming
          plugins={STREAMING_MARKDOWN_PLUGINS}
        >
          {partialMd}
        </Markdown>
      </Theme>,
    );

    await waitFor(() => {
      expect(screen.getByText(/StreamingNode/)).toBeInTheDocument();
    });
    expect(container.querySelector("svg.pele")).toBeNull();
  });

  it("shows concise error fallback with raw source on unrecoverable syntax", async () => {
    const unfixable = "notADiagramType !!!\n  ??? ===";

    renderWithTheme(<MermaidDiagram code={unfixable} />);

    await waitFor(() => {
      expect(screen.getByText("Syntax fallback")).toBeInTheDocument();
    });

    expect(screen.getByText(/Could not render Mermaid diagram:/i)).toBeInTheDocument();
    expect(screen.getByText(/notADiagramType/)).toBeInTheDocument();
  });

  it("renders Copy/Expand as IconButtons, opens Expanded view at Fit, and zooms via mouse wheel + Reset", async () => {
    const wideSequence = [
      "sequenceDiagram",
      "  autonumber",
      '  participant UI as "사용자 UI (Composer)"',
      '  participant R as "sessions/router.py (SSE)"',
      '  participant S as "SessionService"',
      '  participant H as "DeepAgentHarness"',
      '  participant LG as "LangGraph (LRU 캐시)"',
      '  participant FS as "워크스페이스 / .data"',
      "  UI->>R: POST /api/sessions/{id}/messages/stream + X-LLM-* 헤더",
      "  R->>S: stream_message(...)",
      "  S->>H: stream_run(mode, creds, prior_messages)",
      "  LG->>FS: ls / read_file / write_file / execute",
    ].join("\n");

    const { container } = renderWithTheme(<MermaidDiagram code={wideSequence} />);

    await waitFor(() => {
      expect(container.querySelector("svg.pele")).not.toBeNull();
    });

    const copyBtn = screen.getByRole("button", { name: "Copy" });
    const expandBtn = screen.getByRole("button", { name: "Expand" });
    expect(copyBtn.textContent?.trim()).toBe("");
    expect(expandBtn.textContent?.trim()).toBe("");

    const inlineSvg = container.querySelector("svg.pele") as SVGSVGElement | null;
    expect(inlineSvg).not.toBeNull();
    expect(inlineSvg?.style.minWidth).toBe("");
    const naturalWidth = Number.parseInt(inlineSvg?.style.maxWidth ?? "0", 10);
    expect(naturalWidth).toBeGreaterThan(1200);

    fireEvent.click(expandBtn);

    let fitVbWidth = 0;
    await waitFor(() => {
      const dialogSvg = container.querySelector("dialog[open] svg.pele");
      expect(dialogSvg).not.toBeNull();
      const vb = (dialogSvg?.getAttribute("viewBox") ?? "").split(/\s+/).map(Number);
      fitVbWidth = vb[2] ?? 0;
      expect(fitVbWidth).toBeGreaterThanOrEqual(naturalWidth * 0.99);
    });

    // At initial Fit, no Reset zoom button or +/- button clutter is shown
    expect(screen.queryByRole("button", { name: "Reset zoom" })).toBeNull();
    expect(screen.queryByRole("button", { name: "Zoom in" })).toBeNull();

    const dialogSvg = container.querySelector("dialog[open] svg.pele")!;
    const stageHost = dialogSvg.parentElement!;

    // Mouse wheel scroll zooms in around cursor (viewBox width shrinks) and shows Reset zoom IconButton
    fireEvent.wheel(stageHost, { deltaY: -120, clientX: 400, clientY: 250 });

    await waitFor(() => {
      const vb = (dialogSvg.getAttribute("viewBox") ?? "").split(/\s+/).map(Number);
      const zoomedVbWidth = vb[2] ?? 0;
      expect(zoomedVbWidth).toBeLessThan(fitVbWidth);
      expect(screen.getByRole("button", { name: "Reset zoom" })).toBeInTheDocument();
    });

    // Clicking Reset zoom restores full Fit viewBox and hides Reset zoom IconButton
    fireEvent.click(screen.getByRole("button", { name: "Reset zoom" }));
    await waitFor(() => {
      const vb = (dialogSvg.getAttribute("viewBox") ?? "").split(/\s+/).map(Number);
      expect(vb[2]).toBeCloseTo(fitVbWidth, 1);
      expect(screen.queryByRole("button", { name: "Reset zoom" })).toBeNull();
    });
  });
});
