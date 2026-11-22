import DOMPurify from "dompurify";

export type MermaidRenderResult =
  | {
      readonly ok: true;
      readonly svg: string;
      readonly repaired: boolean;
      readonly repairedSource?: string;
      readonly fixCount: number;
      readonly diagramType: string;
    }
  | {
      readonly ok: false;
      readonly error: string;
      readonly attemptedRepair: boolean;
    };

const MAX_REPAIR_PASSES = 5;

const PELE_RENDER_OPTIONS = {
  responsive: true,
  fontFamily: "var(--font-family-body)",
  fontFamilyMono: "var(--font-family-code)",
} as const;

const ASTRYX_PELE_THEME_VARS: ReadonlyArray<readonly [string, string]> = [
  ["--pele-bg", "var(--color-background-card)"],
  ["--pele-fg", "var(--color-text-primary)"],
  ["--pele-muted", "var(--color-text-secondary)"],
  ["--pele-line", "var(--color-text-secondary)"],
  ["--pele-surface", "var(--color-background-muted)"],
  ["--pele-surface-alt", "var(--color-background-body)"],
  ["--pele-border", "var(--color-border-emphasized)"],
  ["--pele-accent", "var(--color-accent)"],
  ["--pele-radius", "var(--radius-element)"],
  ["--pele-font", "var(--font-family-body)"],
  ["--pele-font-mono", "var(--font-family-code)"],
];

const EXTERNAL_IMPORT_PATTERN =
  /@import\s+(?:url\([^)]*\)|"[^"]*"|'[^']*')[^;]*;?/gi;

function stripExternalStyleImports(svg: string): string {
  return svg.replace(
    /<style\b([^>]*)>([\s\S]*?)<\/style>/gi,
    (_match, attrs: string, cssBody: string) =>
      `<style${attrs}>${cssBody.replace(EXTERNAL_IMPORT_PATTERN, "")}</style>`,
  );
}

function applyAstryxThemeVars(svgElement: SVGSVGElement): void {
  for (const [token, value] of ASTRYX_PELE_THEME_VARS) {
    svgElement.style.setProperty(token, value);
  }
  if (!svgElement.hasAttribute("role")) {
    svgElement.setAttribute("role", "img");
  }
}

function formatRenderError(error: unknown): string {
  if (error instanceof Error && error.message.trim().length > 0) {
    const firstLine = error.message.trim().split(/\r?\n/)[0] ?? error.message.trim();
    return firstLine.length > 220 ? `${firstLine.slice(0, 217)}...` : firstLine;
  }
  return "Unable to render Mermaid diagram syntax.";
}

async function repairMermaidSource(
  source: string,
): Promise<{ readonly fixed: string; readonly fixCount: number }> {
  const [{ validate }, { computeFixes }, { applyEdits }] = await Promise.all([
    import("@probelabs/maid/core/router"),
    import("@probelabs/maid/core/fixes"),
    import("@probelabs/maid/core/edits"),
  ]);

  let current = source;
  let totalFixes = 0;

  for (let pass = 0; pass < MAX_REPAIR_PASSES; pass += 1) {
    const validation = validate(current, { strict: false });
    const edits = computeFixes(current, validation.errors, "safe");
    if (edits.length === 0) {
      break;
    }
    const next = applyEdits(current, edits);
    if (next === current) {
      break;
    }
    totalFixes += edits.length;
    current = next;
  }

  return { fixed: current, fixCount: totalFixes };
}

export function sanitizeMermaidSvgToFragment(svg: string): DocumentFragment {
  const cleaned = stripExternalStyleImports(svg);
  const fragment = DOMPurify.sanitize(cleaned, {
    USE_PROFILES: { svg: true, svgFilters: true },
    FORBID_TAGS: ["foreignObject", "script", "iframe", "object", "embed"],
    RETURN_DOM_FRAGMENT: true,
  });

  const svgElement = fragment.querySelector("svg");
  if (svgElement) {
    applyAstryxThemeVars(svgElement);
  }

  return fragment;
}

export async function renderMermaidSafe(source: string): Promise<MermaidRenderResult> {
  const trimmed = source.trim();
  if (!trimmed) {
    return {
      ok: false,
      error: "Empty Mermaid diagram.",
      attemptedRepair: false,
    };
  }

  const { renderAsync } = await import("pele/lazy");

  try {
    const rendered = await renderAsync(source, PELE_RENDER_OPTIONS);
    return {
      ok: true,
      svg: rendered.svg,
      repaired: false,
      fixCount: 0,
      diagramType: rendered.type,
    };
  } catch (initialError) {
    let repaired: { readonly fixed: string; readonly fixCount: number };
    try {
      repaired = await repairMermaidSource(source);
    } catch {
      return {
        ok: false,
        error: formatRenderError(initialError),
        attemptedRepair: true,
      };
    }

    if (repaired.fixCount > 0 && repaired.fixed !== source) {
      try {
        const rendered = await renderAsync(repaired.fixed, PELE_RENDER_OPTIONS);
        return {
          ok: true,
          svg: rendered.svg,
          repaired: true,
          repairedSource: repaired.fixed,
          fixCount: repaired.fixCount,
          diagramType: rendered.type,
        };
      } catch (repairRenderError) {
        return {
          ok: false,
          error: formatRenderError(repairRenderError),
          attemptedRepair: true,
        };
      }
    }

    return {
      ok: false,
      error: formatRenderError(initialError),
      attemptedRepair: repaired.fixCount > 0,
    };
  }
}
