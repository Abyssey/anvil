import DOMPurify from "dompurify";
import type { RenderOptions } from "pele";

export type MermaidRenderResult =
  | {
      readonly ok: true;
      readonly svg: string;
      readonly width: number;
      readonly height: number;
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

export interface MermaidSvgDisplayOptions {
  readonly stageFill?: boolean;
  readonly maxWidthPx?: number;
}

const MAX_REPAIR_PASSES = 5;
const MERMAID_BASE_FONT_SIZE = 14;
const MERMAID_VIEWBOX_PADDING = 24;
const TEXT_SAFETY_SCALE = 1.1;

const FALLBACK_BODY_FONT =
  '-apple-system, BlinkMacSystemFont, "Segoe UI", "Apple SD Gothic Neo", "Noto Sans KR", "Malgun Gothic", sans-serif';
const FALLBACK_MONO_FONT =
  'ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, "Noto Sans Mono CJK KR", monospace';

const STYLE_BOLD = 1;
const STYLE_ITALIC = 2;
const STYLE_MONO = 4;
const STYLE_SUB_OR_SUP = 24;
const SUB_SUP_DIVISOR = 1.2;

const WIDE_ASCII_CHARS = new Set([
  "W",
  "M",
  "@",
  "%",
  "&",
  "Q",
  "O",
  "G",
  "D",
  "H",
  "N",
  "U",
]);

const NARROW_ASCII_CHARS = new Set([
  "i",
  "l",
  "j",
  "1",
  ".",
  ",",
  ":",
  ";",
  "'",
  "|",
  "!",
]);

const COMPACT_ASCII_CHARS = new Set([
  "I",
  "t",
  "f",
  "r",
  "(",
  ")",
  "[",
  "]",
  "/",
  "-",
]);

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

const SEQUENCE_HEADER_PATTERN =
  /^(?:\s*%%[^\r\n]*\r?\n)*\s*sequenceDiagram\b/;

const QUOTED_PARTICIPANT_ALIAS_PATTERN =
  /^(\s*(?:participant|actor)\s+[^\s"';]+\s+as\s+)"([^"\r\n;]+)"(\s*)$/gm;

function isFullwidthOrCjk(codePoint: number): boolean {
  return (
    (codePoint >= 0x1100 && codePoint <= 0x11ff) ||
    (codePoint >= 0x2e80 && codePoint <= 0x9fff) ||
    (codePoint >= 0xac00 && codePoint <= 0xd7af) ||
    (codePoint >= 0xf900 && codePoint <= 0xfaff) ||
    (codePoint >= 0xfe30 && codePoint <= 0xfe4f) ||
    (codePoint >= 0xff01 && codePoint <= 0xff60) ||
    (codePoint >= 0xffe0 && codePoint <= 0xffe6) ||
    (codePoint >= 0x20000 && codePoint <= 0x2fa1f)
  );
}

function isZeroWidthCodePoint(codePoint: number): boolean {
  return (
    codePoint === 0x00ad ||
    (codePoint >= 0x0300 && codePoint <= 0x036f) ||
    (codePoint >= 0x200b && codePoint <= 0x200f) ||
    (codePoint >= 0xfe00 && codePoint <= 0xfe0f) ||
    codePoint === 0xfeff
  );
}

export function measureGlyphFloorWidth(
  text: string,
  size: number,
  style: number,
): number {
  if (text.length === 0 || size <= 0) {
    return 0;
  }

  const effectiveSize =
    (style & STYLE_SUB_OR_SUP) !== 0 ? size / SUB_SUP_DIVISOR : size;
  const isMono = (style & STYLE_MONO) !== 0;
  const boldMultiplier = (style & STYLE_BOLD) !== 0 ? 1.06 : 1;

  let units = 0;
  for (const ch of text) {
    const cp = ch.codePointAt(0) ?? 0;
    if (cp < 32 || isZeroWidthCodePoint(cp)) {
      continue;
    }
    if (isFullwidthOrCjk(cp)) {
      units += isMono ? 1.06 : 1.02;
    } else if (isMono) {
      units += 0.62;
    } else if (ch === " ") {
      units += 0.33;
    } else if (WIDE_ASCII_CHARS.has(ch)) {
      units += 0.72;
    } else if (ch >= "A" && ch <= "Z") {
      units += 0.64;
    } else if (NARROW_ASCII_CHARS.has(ch)) {
      units += 0.38;
    } else if (COMPACT_ASCII_CHARS.has(ch)) {
      units += 0.47;
    } else {
      units += 0.58;
    }
  }

  return units * effectiveSize * boldMultiplier;
}

function resolveConcreteFontFamily(cssVarName: string, fallback: string): string {
  if (typeof window === "undefined" || typeof document === "undefined") {
    return fallback;
  }
  const root = document.documentElement;
  if (!root) {
    return fallback;
  }
  const computed = window.getComputedStyle(root).getPropertyValue(cssVarName).trim();
  if (!computed || computed.includes("var(")) {
    return fallback;
  }
  return `${computed}, ${fallback}`;
}

type Canvas2DLike = {
  font: string;
  measureText(text: string): { readonly width: number };
};

let sharedCanvasContext: Canvas2DLike | null | undefined;

function getMeasurementCanvasContext(): Canvas2DLike | null {
  if (sharedCanvasContext !== undefined) {
    return sharedCanvasContext;
  }
  try {
    if (typeof OffscreenCanvas !== "undefined") {
      sharedCanvasContext = new OffscreenCanvas(1, 1).getContext("2d");
      return sharedCanvasContext;
    }
    if (typeof document !== "undefined") {
      sharedCanvasContext = document.createElement("canvas").getContext("2d");
      return sharedCanvasContext;
    }
  } catch {
    sharedCanvasContext = null;
    return null;
  }
  sharedCanvasContext = null;
  return null;
}

function createHybridTextMeasurer(
  bodyFont: string,
  monoFont: string,
): NonNullable<RenderOptions["measurer"]> {
  let activeFontSpec = "";
  return {
    width(text: string, size: number, style: number): number {
      if (text.length === 0 || size <= 0) {
        return 0;
      }
      const floorWidth = measureGlyphFloorWidth(text, size, style);
      const ctx = getMeasurementCanvasContext();
      if (!ctx) {
        return floorWidth * TEXT_SAFETY_SCALE;
      }

      const effectiveSize =
        (style & STYLE_SUB_OR_SUP) !== 0 ? size / SUB_SUP_DIVISOR : size;
      const italicPrefix = (style & STYLE_ITALIC) !== 0 ? "italic " : "";
      const boldPrefix = (style & STYLE_BOLD) !== 0 ? "600 " : "";
      const family = (style & STYLE_MONO) !== 0 ? monoFont : bodyFont;
      const fontSpec = `${italicPrefix}${boldPrefix}${effectiveSize}px ${family}`;

      let canvasWidth = 0;
      try {
        if (activeFontSpec !== fontSpec) {
          ctx.font = fontSpec;
          activeFontSpec = fontSpec;
        }
        canvasWidth = ctx.measureText(text).width;
      } catch {
        canvasWidth = 0;
      }

      return Math.max(canvasWidth, floorWidth) * TEXT_SAFETY_SCALE;
    },
  };
}

function buildPeleRenderOptions(): RenderOptions {
  const bodyFont = resolveConcreteFontFamily(
    "--font-family-body",
    FALLBACK_BODY_FONT,
  );
  const monoFont = resolveConcreteFontFamily(
    "--font-family-code",
    FALLBACK_MONO_FONT,
  );

  return {
    responsive: false,
    fontSize: MERMAID_BASE_FONT_SIZE,
    padding: MERMAID_VIEWBOX_PADDING,
    fontFamily: bodyFont,
    fontFamilyMono: monoFont,
    measurer: createHybridTextMeasurer(bodyFont, monoFont),
    config: {
      flowchart: { wrappingWidth: 280 },
      state: { wrappingWidth: 260 },
    },
  };
}

export function normalizeMermaidSourceForRender(source: string): string {
  if (!SEQUENCE_HEADER_PATTERN.test(source)) {
    return source;
  }
  return source.replace(QUOTED_PARTICIPANT_ALIAS_PATTERN, "$1$2$3");
}

function stripExternalStyleImports(svg: string): string {
  return svg.replace(
    /<style\b([^>]*)>([\s\S]*?)<\/style>/gi,
    (_match, attrs: string, cssBody: string) =>
      `<style${attrs}>${cssBody.replace(EXTERNAL_IMPORT_PATTERN, "")}</style>`,
  );
}

function applySvgDisplaySizing(
  svgElement: SVGSVGElement,
  options?: MermaidSvgDisplayOptions,
): void {
  if (options?.stageFill) {
    svgElement.setAttribute("preserveAspectRatio", "xMidYMid meet");
    svgElement.style.display = "block";
    svgElement.style.width = "100%";
    svgElement.style.height = "100%";
    svgElement.style.minWidth = "0";
    svgElement.style.maxWidth = "none";
    svgElement.style.margin = "0";
    return;
  }

  svgElement.style.display = "block";
  svgElement.style.width = "100%";
  svgElement.style.height = "auto";
  svgElement.style.marginInline = "auto";
  svgElement.style.removeProperty("min-width");
  svgElement.style.maxWidth =
    options?.maxWidthPx !== undefined && options.maxWidthPx > 0
      ? `${Math.round(options.maxWidthPx)}px`
      : "100%";
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

export function sanitizeMermaidSvgToFragment(
  svg: string,
  options?: MermaidSvgDisplayOptions,
): DocumentFragment {
  const cleaned = stripExternalStyleImports(svg);
  const fragment = DOMPurify.sanitize(cleaned, {
    USE_PROFILES: { svg: true, svgFilters: true },
    FORBID_TAGS: ["foreignObject", "script", "iframe", "object", "embed"],
    RETURN_DOM_FRAGMENT: true,
  });

  const svgElement = fragment.querySelector("svg");
  if (svgElement) {
    applyAstryxThemeVars(svgElement);
    applySvgDisplaySizing(svgElement, options);
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

  const normalizedSource = normalizeMermaidSourceForRender(source);
  const renderOptions = buildPeleRenderOptions();
  const { renderAsync } = await import("pele/lazy");

  try {
    const rendered = await renderAsync(normalizedSource, renderOptions);
    return {
      ok: true,
      svg: rendered.svg,
      width: rendered.width,
      height: rendered.height,
      repaired: false,
      fixCount: 0,
      diagramType: rendered.type,
    };
  } catch (initialError) {
    let repaired: { readonly fixed: string; readonly fixCount: number };
    try {
      repaired = await repairMermaidSource(normalizedSource);
    } catch {
      return {
        ok: false,
        error: formatRenderError(initialError),
        attemptedRepair: true,
      };
    }

    if (repaired.fixCount > 0 && repaired.fixed !== normalizedSource) {
      try {
        const rendered = await renderAsync(repaired.fixed, renderOptions);
        return {
          ok: true,
          svg: rendered.svg,
          width: rendered.width,
          height: rendered.height,
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
