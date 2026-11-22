const BASENAME_TO_LANGUAGE: Readonly<Record<string, string>> = {
  ".bashrc": "bash",
  ".bash_profile": "bash",
  ".profile": "bash",
  ".zshrc": "bash",
  dockerfile: "dockerfile",
};

const EXTENSION_TO_LANGUAGE: Readonly<Record<string, string>> = {
  bash: "bash",
  c: "c",
  cc: "cpp",
  cjs: "javascript",
  cpp: "cpp",
  css: "css",
  cts: "typescript",
  cxx: "cpp",
  go: "go",
  h: "c",
  hack: "hack",
  hh: "cpp",
  hpp: "cpp",
  htm: "html",
  html: "html",
  hxx: "cpp",
  js: "javascript",
  json: "json",
  json5: "json",
  jsonc: "json",
  jsx: "jsx",
  kt: "kotlin",
  kts: "kotlin",
  less: "less",
  markdown: "markdown",
  md: "markdown",
  mdx: "markdown",
  mjs: "javascript",
  mts: "typescript",
  php: "php",
  plist: "xml",
  py: "python",
  pyi: "python",
  pyw: "python",
  rs: "rust",
  scss: "scss",
  sh: "bash",
  sql: "sql",
  svg: "svg",
  swift: "swift",
  toml: "toml",
  ts: "typescript",
  tsx: "tsx",
  xhtml: "xml",
  xml: "xml",
  yaml: "yaml",
  yml: "yaml",
  zsh: "bash",
};

/**
 * Infer an Astryx CodeBlock grammar identifier from a workspace file path.
 * Falls back to `"plaintext"` when the extension is absent or unrecognized.
 */
export function inferCodeLanguage(filePath: string): string {
  const trimmed = filePath.trim();
  if (!trimmed) {
    return "plaintext";
  }

  const normalized = trimmed.replaceAll("\\", "/");
  const lastSlash = normalized.lastIndexOf("/");
  const basename = (lastSlash >= 0 ? normalized.slice(lastSlash + 1) : normalized).toLowerCase();
  if (!basename) {
    return "plaintext";
  }

  const exactMatch = BASENAME_TO_LANGUAGE[basename];
  if (exactMatch) {
    return exactMatch;
  }

  const dotIdx = basename.lastIndexOf(".");
  if (dotIdx <= 0 || dotIdx === basename.length - 1) {
    return "plaintext";
  }

  const ext = basename.slice(dotIdx + 1);
  return EXTENSION_TO_LANGUAGE[ext] ?? "plaintext";
}
