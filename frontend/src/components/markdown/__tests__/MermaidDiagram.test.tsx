import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { Markdown } from "@astryxdesign/core/Markdown";
import { Theme } from "@astryxdesign/core/theme";
import { afterEach, describe, expect, it } from "vitest";
import { defaultThemeSlug, themes } from "../../../astryx-themes";
import {
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
});
