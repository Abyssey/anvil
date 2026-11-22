import { describe, expect, it } from "vitest";
import { inferCodeLanguage } from "../codeLanguage";

describe("inferCodeLanguage", () => {
  it("maps common programming and markup extensions to Astryx CodeBlock grammars", () => {
    expect(inferCodeLanguage("backend/app/main.py")).toBe("python");
    expect(inferCodeLanguage("types/stubs.pyi")).toBe("python");
    expect(inferCodeLanguage("frontend/src/App.tsx")).toBe("tsx");
    expect(inferCodeLanguage("frontend/src/api/client.ts")).toBe("typescript");
    expect(inferCodeLanguage("frontend/vite.config.mts")).toBe("typescript");
    expect(inferCodeLanguage("scripts/build.js")).toBe("javascript");
    expect(inferCodeLanguage("scripts/run.mjs")).toBe("javascript");
    expect(inferCodeLanguage("src/Widget.jsx")).toBe("jsx");
    expect(inferCodeLanguage("package.json")).toBe("json");
    expect(inferCodeLanguage("docs/README.md")).toBe("markdown");
    expect(inferCodeLanguage("styles/app.css")).toBe("css");
    expect(inferCodeLanguage("styles/app.scss")).toBe("scss");
    expect(inferCodeLanguage("styles/app.less")).toBe("less");
    expect(inferCodeLanguage("index.html")).toBe("html");
    expect(inferCodeLanguage("assets/logo.svg")).toBe("svg");
    expect(inferCodeLanguage("config/schema.xml")).toBe("xml");
    expect(inferCodeLanguage(".github/workflows/ci.yml")).toBe("yaml");
    expect(inferCodeLanguage("docker-compose.yaml")).toBe("yaml");
    expect(inferCodeLanguage("scripts/deploy.sh")).toBe("bash");
    expect(inferCodeLanguage("scripts/setup.zsh")).toBe("bash");
    expect(inferCodeLanguage("migrations/001_init.sql")).toBe("sql");
    expect(inferCodeLanguage("pyproject.toml")).toBe("toml");
    expect(inferCodeLanguage("src/lib.rs")).toBe("rust");
    expect(inferCodeLanguage("cmd/server/main.go")).toBe("go");
    expect(inferCodeLanguage("native/hash.c")).toBe("c");
    expect(inferCodeLanguage("native/engine.cpp")).toBe("cpp");
    expect(inferCodeLanguage("app/Main.kt")).toBe("kotlin");
    expect(inferCodeLanguage("ios/App.swift")).toBe("swift");
    expect(inferCodeLanguage("web/index.php")).toBe("php");
  });

  it("handles uppercase extensions, Windows separators, and dotfiles", () => {
    expect(inferCodeLanguage("DOCS/README.MD")).toBe("markdown");
    expect(inferCodeLanguage("src\\components\\Panel.TSX")).toBe("tsx");
    expect(inferCodeLanguage(".bashrc")).toBe("bash");
    expect(inferCodeLanguage(".zshrc")).toBe("bash");
    expect(inferCodeLanguage("  backend/app/main.py  ")).toBe("python");
  });

  it("falls back to plaintext for unknown extensions, dot-directories, or empty paths", () => {
    expect(inferCodeLanguage("")).toBe("plaintext");
    expect(inferCodeLanguage("LICENSE")).toBe("plaintext");
    expect(inferCodeLanguage("v1.0/README")).toBe("plaintext");
    expect(inferCodeLanguage("assets/photo.png")).toBe("plaintext");
    expect(inferCodeLanguage("notes.txt")).toBe("plaintext");
    expect(inferCodeLanguage(".gitignore")).toBe("plaintext");
  });
});
