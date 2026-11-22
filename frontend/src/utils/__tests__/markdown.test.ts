import { describe, expect, it } from "vitest";
import { renderSafeMarkdownInto } from "../markdown";

describe("renderSafeMarkdownInto XSS sanitization", () => {
  it("renders markdown headings/code while stripping scripts and event attributes", async () => {
    const host = document.createElement("section");
    await renderSafeMarkdownInto(
      host,
      "# Architecture\n\nSafe `code`\n\n<script>alert('xss')</script><img src=x onerror=\"alert(1)\">",
    );
    expect(host.querySelector("h1")?.textContent).toBe("Architecture");
    expect(host.querySelector("code")?.textContent).toBe("code");
    expect(host.querySelector("script")).toBeNull();
    expect(host.innerHTML).not.toContain("onerror");
    expect(host.innerHTML).not.toContain("alert");
  });
});
