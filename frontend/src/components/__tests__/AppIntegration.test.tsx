import { Heading } from "@astryxdesign/core/Heading";
import { Theme } from "@astryxdesign/core/theme";
import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { App } from "../../App";
import { defaultThemeSlug, themes } from "../../astryx-themes";
import { AppErrorBoundary } from "../workspace/AppErrorBoundary";

function jsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

describe("App + LiveWorkspaceProvider end-to-end integration", () => {
  beforeEach(() => {
    window.localStorage.clear();
  });

  afterEach(() => {
    vi.restoreAllMocks();
  });

  it("catches unexpected render crashes in AppErrorBoundary and recovers on Try Again", async () => {
    const spy = vi.spyOn(console, "error").mockImplementation(() => undefined);
    let shouldThrow = true;

    function UnstableChild() {
      if (shouldThrow) {
        throw new Error("Simulated render crash");
      }
      return <Heading level={2}>Recovered Workspace</Heading>;
    }

    render(
      <Theme theme={themes[defaultThemeSlug]}>
        <AppErrorBoundary>
          <UnstableChild />
        </AppErrorBoundary>
      </Theme>,
    );

    expect(screen.getByRole("alert")).toBeInTheDocument();
    expect(
      screen.getByText("Workspace UI encountered an unexpected error"),
    ).toBeInTheDocument();

    shouldThrow = false;
    fireEvent.click(screen.getByRole("button", { name: "Try Again" }));

    await waitFor(() => {
      expect(screen.getByRole("heading", { name: "Recovered Workspace" })).toBeInTheDocument();
    });
    spy.mockRestore();
  });

  it("completes Project Files boot, Settings setup, [6] Code Onboarding SSE stream, and Artifact Drawer auto-open", async () => {
    const project = {
      id: "proj-live",
      name: "repo",
      path: "/repo",
      is_git_repo: true,
      branch: "main",
      created_at: "2026-11-22T00:00:00Z",
      updated_at: "2026-11-22T00:00:00Z",
    };
    const session = {
      id: "sess-live",
      project_id: "proj-live",
      title: "New Session",
      workspace_path: "/repo",
      mode: "ask" as const,
      status: "idle" as const,
      pending_interrupt: null,
      created_at: "2026-11-22T00:00:00Z",
      updated_at: "2026-11-22T00:00:00Z",
    };

    let storedMessages: Array<Record<string, unknown>> = [];
    let storedArtifacts: Array<Record<string, unknown>> = [];

    vi.stubGlobal(
      "fetch",
      vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
        const url = String(input);
        if (url === "/api/projects" && (!init?.method || init.method === "GET")) {
          return jsonResponse([project]);
        }
        if (url.startsWith("/api/workspace/tree")) {
          return jsonResponse([
            { name: "README.md", path: "README.md", is_dir: false, size: 120, children: [] },
          ]);
        }
        if (url.startsWith("/api/workspace/default") || url.startsWith("/api/workspace/validate")) {
          return jsonResponse({ path: "/repo", name: "repo", is_git_repo: true, branch: "main" });
        }
        if (url === "/api/sessions" && (!init?.method || init.method === "GET")) {
          return jsonResponse([session]);
        }
        if (url === "/api/sessions/sess-live") {
          return jsonResponse({ session, messages: storedMessages });
        }
        if (url === "/api/sessions/sess-live/artifacts") {
          return jsonResponse({ artifacts: storedArtifacts, walkthrough_plan: null });
        }
        if (url.startsWith("/api/workspace/diff")) {
          return jsonResponse({ workspace_path: "/repo", branch: "main", git_head: "abc", files: [] });
        }
        if (url === "/api/workspace/llm-ping") {
          return jsonResponse({
            ok: true,
            base_url: "http://127.0.0.1:8099/v1",
            model: "qwen3.8-flash",
          });
        }
        if (url === "/api/sessions/sess-live/messages/stream") {
          const walkthroughDoc = {
            kind: "walkthrough",
            title: "Walkthrough",
            content: "# Codebase Onboarding\nDDD bounded contexts verified.",
            updated_at: "2026-11-22T00:01:00Z",
          };
          storedArtifacts = [walkthroughDoc];
          storedMessages = [
            {
              id: "u1",
              session_id: "sess-live",
              role: "user",
              content: "이 프로젝트 구조와 핵심 의존성을 파악해서 온보딩 문서로 정리해줘",
              thinking: "",
              tool_calls: [],
              created_at: "2026-11-22T00:01:00Z",
            },
            {
              id: "a1",
              session_id: "sess-live",
              role: "assistant",
              content: "온보딩 문서를 생성했습니다.",
              thinking: "Scanning modules.",
              tool_calls: [
                {
                  id: "tc1",
                  name: "ls",
                  args: { path: "/" },
                  output: "backend\nfrontend",
                  status: "success",
                  subagent: "explorer",
                },
              ],
              created_at: "2026-11-22T00:01:01Z",
            },
          ];
          const sse = [
            'event: thinking\ndata: {"text":"Scanning modules."}\n\n',
            'event: tool_start\ndata: {"id":"tc1","name":"ls","args":{"path":"/"},"subagent":"explorer"}\n\n',
            'event: tool_end\ndata: {"id":"tc1","name":"ls","status":"success","output":"backend\\nfrontend"}\n\n',
            `event: artifact_updated\ndata: ${JSON.stringify(walkthroughDoc)}\n\n`,
            'event: token\ndata: {"text":"온보딩 문서를 생성했습니다."}\n\n',
            'event: done\ndata: {"session_id":"sess-live"}\n\n',
          ].join("");
          const enc = new TextEncoder();
          const stream = new ReadableStream<Uint8Array>({
            start(ctrl) {
              ctrl.enqueue(enc.encode(sse));
              ctrl.close();
            },
          });
          return new Response(stream, { status: 200 });
        }
        return jsonResponse({});
      }),
    );

    render(
      <Theme theme={themes[defaultThemeSlug]}>
        <App />
      </Theme>,
    );

    // 1. Project Files auto-open immediately on boot + empty state shows unconfigured banner
    await screen.findByText("README.md");
    const openBtn = await screen.findByRole("button", { name: "Open Settings" });
    fireEvent.click(openBtn);

    // 2. Configure Endpoint URL + API Key, Test Connection, and Save
    const urlInput = await screen.findByLabelText(/Endpoint URL/i);
    fireEvent.change(urlInput, { target: { value: "http://127.0.0.1:8099/v1/chat/completions" } });
    const keyInput = screen.getByLabelText(/API Key/i);
    fireEvent.change(keyInput, { target: { value: "sk-live-test-key" } });

    await act(async () => {
      fireEvent.click(screen.getByRole("button", { name: "Test Connection" }));
    });
    await screen.findByText(/Connected to http:\/\/127\.0\.0\.1:8099\/v1/i);

    fireEvent.click(screen.getByRole("button", { name: "Save" }));

    // 3. Trigger [6] Code Onboarding sample prompt
    const sampleBtn = await screen.findByRole("button", {
      name: "이 프로젝트 구조와 핵심 의존성을 파악해서 온보딩 문서로 정리해줘",
    });
    await act(async () => {
      fireEvent.click(sampleBtn);
    });

    // 4. Verify assistant message, auto-opened Walkthrough artifact drawer, and settled stream state
    await screen.findByText("온보딩 문서를 생성했습니다.");
    await screen.findByText("DDD bounded contexts verified.");
    await waitFor(() => {
      expect(screen.getByRole("textbox", { name: "Message input" })).not.toHaveAttribute(
        "aria-disabled",
        "true",
      );
    });
  });
});
