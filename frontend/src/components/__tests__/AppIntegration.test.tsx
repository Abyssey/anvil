import { Heading } from "@astryxdesign/core/Heading";
import { Theme } from "@astryxdesign/core/theme";
import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { App } from "../../App";
import { defaultThemeSlug, themes } from "../../astryx-themes";
import { SETTINGS_STORAGE_KEY } from "../../state/settings";
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
        if (url === "/api/sessions/sess-live" || url.startsWith("/api/sessions/sess-live?")) {
          return jsonResponse({ session, messages: storedMessages, has_more_messages: false });
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

    // 4. Verify assistant message, collapsed ExplorationGroup, Worked for duration, auto-opened Walkthrough drawer, and settled stream state
    await screen.findByText("온보딩 문서를 생성했습니다.");
    await screen.findByText("DDD bounded contexts verified.");
    expect(screen.getByRole("button", { name: "폴더 1개 탐색" })).toHaveAttribute(
      "aria-expanded",
      "false",
    );
    expect(screen.getByText(/^Worked for /)).toBeInTheDocument();
    await waitFor(() => {
      expect(screen.getByRole("textbox", { name: "Message input" })).not.toHaveAttribute(
        "aria-disabled",
        "true",
      );
    });
  });

  it("loads older messages automatically on scroll-to-top via cursor pagination without a button", async () => {
    const project = {
      id: "proj-page",
      name: "repo",
      path: "/repo",
      is_git_repo: true,
      branch: "main",
      created_at: "2026-11-22T00:00:00Z",
      updated_at: "2026-11-22T00:00:00Z",
    };
    const session = {
      id: "sess-page",
      project_id: "proj-page",
      title: "Long Chat",
      workspace_path: "/repo",
      mode: "ask" as const,
      status: "idle" as const,
      pending_interrupt: null,
      created_at: "2026-11-22T00:00:00Z",
      updated_at: "2026-11-22T00:00:00Z",
    };

    const pageUrls: string[] = [];
    vi.stubGlobal(
      "fetch",
      vi.fn(async (input: RequestInfo | URL) => {
        const url = String(input);
        if (url === "/api/projects") return jsonResponse([project]);
        if (url === "/api/sessions") return jsonResponse([session]);
        if (url.startsWith("/api/workspace/tree")) return jsonResponse([]);
        if (url.startsWith("/api/workspace/validate")) {
          return jsonResponse({ path: "/repo", name: "repo", is_git_repo: true, branch: "main" });
        }
        if (url.startsWith("/api/workspace/diff")) {
          return jsonResponse({ workspace_path: "/repo", branch: "main", git_head: "abc", files: [] });
        }
        if (url === "/api/sessions/sess-page/artifacts") {
          return jsonResponse({ artifacts: [], walkthrough_plan: null });
        }
        if (url.startsWith("/api/sessions/sess-page?")) {
          return jsonResponse({
            session,
            has_more_messages: true,
            messages: [
              {
                id: "m-21",
                session_id: "sess-page",
                role: "user",
                content: "Recent message 21",
                thinking: "",
                tool_calls: [],
                created_at: "2026-11-22T00:01:00Z",
              },
              {
                id: "m-22",
                session_id: "sess-page",
                role: "assistant",
                content: "Recent answer 22",
                thinking: "",
                tool_calls: [],
                created_at: "2026-11-22T00:01:01Z",
              },
            ],
          });
        }
        if (url.startsWith("/api/sessions/sess-page/messages?")) {
          pageUrls.push(url);
          return jsonResponse({
            has_more: false,
            messages: [
              {
                id: "m-1",
                session_id: "sess-page",
                role: "user",
                content: "Oldest message 1",
                thinking: "",
                tool_calls: [],
                created_at: "2026-11-22T00:00:01Z",
              },
            ],
          });
        }
        return jsonResponse({});
      }),
    );

    render(
      <Theme theme={themes[defaultThemeSlug]}>
        <App />
      </Theme>,
    );

    await screen.findByText("Recent answer 22");
    expect(screen.queryByRole("button", { name: /new messages/i })).not.toBeInTheDocument();

    // Wait for requestAnimationFrame arming, then trigger scroll at top on ChatLayout
    await act(async () => {
      await new Promise((r) => window.requestAnimationFrame(() => r(undefined)));
    });
    const log = screen.getByRole("log");
    const scroller = log.closest(".astryx-chat-layout") as HTMLElement | null;
    expect(scroller).not.toBeNull();
    let simulatedHeight = 800;
    if (scroller) {
      Object.defineProperty(scroller, "scrollHeight", {
        get: () => simulatedHeight,
        configurable: true,
      });
      Object.defineProperty(scroller, "scrollTop", {
        value: 0,
        writable: true,
        configurable: true,
      });
      fireEvent.scroll(scroller);
      simulatedHeight = 1450;
    }

    await screen.findByText("Oldest message 1");
    expect(pageUrls.some((u) => u.includes("before_id=m-21"))).toBe(true);
    expect(scroller?.scrollTop).toBe(650);
  });

  it("aborts Project A in-flight SSE stream on switch to Project B and prevents cross-project token leaks", async () => {
    window.localStorage.setItem(
      SETTINGS_STORAGE_KEY,
      JSON.stringify({
        baseUrl: "http://127.0.0.1:8099/v1",
        model: "qwen3.8-flash",
        apiKey: "sk-live-test-key",
        enableThinking: true,
      }),
    );

    const projectA = {
      id: "proj-a",
      name: "alpha-repo",
      path: "/work/alpha",
      is_git_repo: true,
      branch: "main",
      created_at: "2026-11-22T00:00:00Z",
      updated_at: "2026-11-22T00:00:00Z",
    };
    const projectB = {
      id: "proj-b",
      name: "beta-repo",
      path: "/work/beta",
      is_git_repo: true,
      branch: "dev",
      created_at: "2026-11-22T00:00:00Z",
      updated_at: "2026-11-22T00:00:00Z",
    };
    const sessionA = {
      id: "sess-a",
      project_id: "proj-a",
      title: "Alpha Session",
      workspace_path: "/work/alpha",
      mode: "ask" as const,
      status: "idle" as const,
      pending_interrupt: null,
      created_at: "2026-11-22T00:00:00Z",
      updated_at: "2026-11-22T00:00:00Z",
    };
    const sessionB = {
      id: "sess-b",
      project_id: "proj-b",
      title: "Beta Session",
      workspace_path: "/work/beta",
      mode: "ask" as const,
      status: "idle" as const,
      pending_interrupt: null,
      created_at: "2026-11-22T00:00:00Z",
      updated_at: "2026-11-22T00:00:00Z",
    };

    const enc = new TextEncoder();
    let streamController: ReadableStreamDefaultController<Uint8Array> | null = null;
    let streamAbortSignal: AbortSignal | undefined;
    let capturedStreamBody: Record<string, unknown> | null = null;
    let skillsLoaded = false;

    vi.stubGlobal(
      "fetch",
      vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
        const url = String(input);
        if (url === "/api/projects") return jsonResponse([projectA, projectB]);
        if (url === "/api/sessions") return jsonResponse([sessionA, sessionB]);
        if (url.startsWith("/api/skills/installed")) {
          skillsLoaded = true;
          return jsonResponse([
            {
              name: "sec-audit",
              description: "Security audit skill",
              scope: "global",
              source_label: "Global",
              path: "/global/sec-audit",
              deletable: true,
            },
          ]);
        }
        if (url.startsWith("/api/workspace/tree")) return jsonResponse([]);
        if (url.startsWith("/api/workspace/validate")) {
          const isBeta = url.includes("beta");
          return jsonResponse(
            isBeta
              ? { path: "/work/beta", name: "beta-repo", is_git_repo: true, branch: "dev" }
              : { path: "/work/alpha", name: "alpha-repo", is_git_repo: true, branch: "main" },
          );
        }
        if (url.startsWith("/api/workspace/diff")) {
          return jsonResponse({ workspace_path: "/work/alpha", branch: "main", git_head: "abc", files: [] });
        }
        if (url.startsWith("/api/sessions/sess-a/artifacts")) {
          return jsonResponse({ artifacts: [], walkthrough_plan: null });
        }
        if (url.startsWith("/api/sessions/sess-b/artifacts")) {
          return jsonResponse({ artifacts: [], walkthrough_plan: null });
        }
        if (url === "/api/sessions/sess-a" || url.startsWith("/api/sessions/sess-a?")) {
          return jsonResponse({ session: sessionA, messages: [], has_more_messages: false });
        }
        if (url === "/api/sessions/sess-b" || url.startsWith("/api/sessions/sess-b?")) {
          return jsonResponse({
            session: sessionB,
            messages: [
              {
                id: "b-msg-1",
                session_id: "sess-b",
                role: "assistant",
                content: "Beta clean state ready.",
                thinking: "",
                tool_calls: [],
                created_at: "2026-11-22T00:00:10Z",
              },
            ],
            has_more_messages: false,
          });
        }
        if (url === "/api/sessions/sess-a/messages/stream") {
          streamAbortSignal = init?.signal ?? undefined;
          capturedStreamBody = JSON.parse(String(init?.body ?? "{}")) as Record<string, unknown>;
          const stream = new ReadableStream<Uint8Array>({
            start(ctrl) {
              streamController = ctrl;
              ctrl.enqueue(enc.encode('event: token\ndata: {"text":"ALPHASTREAMTOKEN"}\n\n'));
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

    // Wait for initial Alpha session detail & installed skills to finish loading
    await waitFor(() => {
      expect(screen.getAllByText("Alpha Session").length).toBeGreaterThanOrEqual(2);
      expect(skillsLoaded).toBe(true);
      expect(screen.getByRole("textbox", { name: "Message input" })).toBeInTheDocument();
    });

    // Send a message invoking /sec-audit on Alpha
    const textbox = screen.getByRole("textbox", { name: "Message input" });
    textbox.textContent = "/sec-audit Inspect alpha auth";
    fireEvent.input(textbox);
    fireEvent.keyDown(textbox, { key: "Enter", code: "Enter" });

    // Confirm Project A stream started and forwarded pinned_skills
    await waitFor(() => {
      expect(capturedStreamBody).toEqual({
        content: "Inspect alpha auth",
        mode: "ask",
        pinned_skills: ["sec-audit"],
      });
      expect(document.body.textContent).toContain("ALPHASTREAMTOKEN");
    });
    expect(streamAbortSignal?.aborted).toBe(false);

    // Switch to Project B's session while Project A is still streaming
    fireEvent.click(screen.getByText("Beta Session"));

    // AbortSignal for Project A MUST be aborted immediately
    await waitFor(() => {
      expect(streamAbortSignal?.aborted).toBe(true);
    });

    // Even if Project A's stream pushes late tokens & closes after switching, Project B MUST stay clean
    await act(async () => {
      streamController?.enqueue(
        enc.encode('event: token\ndata: {"text":"ALPHALEAKEDSECRET"}\n\n'),
      );
      streamController?.close();
      await Promise.resolve();
    });

    await screen.findByText("Beta clean state ready.");
    expect(document.body.textContent).not.toContain("ALPHASTREAMTOKEN");
    expect(document.body.textContent).not.toContain("ALPHALEAKEDSECRET");
  });

  it("dismisses ShellApprovalCard immediately upon clicking Approve while resume SSE is still streaming", async () => {
    window.localStorage.setItem(
      SETTINGS_STORAGE_KEY,
      JSON.stringify({
        baseUrl: "http://127.0.0.1:8099/v1",
        model: "qwen3.8-flash",
        apiKey: "sk-shell-approve-test",
        enableThinking: true,
      }),
    );

    const project = {
      id: "proj-shell",
      name: "repo",
      path: "/repo",
      is_git_repo: true,
      branch: "main",
      created_at: "2026-11-23T00:00:00Z",
      updated_at: "2026-11-23T00:00:00Z",
    };
    let currentSession: Record<string, unknown> = {
      id: "sess-shell",
      project_id: "proj-shell",
      title: "Cleanup Task",
      workspace_path: "/repo",
      mode: "auto",
      status: "awaiting_approval",
      pending_interrupt: {
        kind: "shell_approval",
        command: "rm -f stale.tmp",
        reason: "File/directory deletion requires approval",
        workspace: "/repo",
      },
      created_at: "2026-11-23T00:00:00Z",
      updated_at: "2026-11-23T00:00:00Z",
    };

    const enc = new TextEncoder();
    let resumeController: ReadableStreamDefaultController<Uint8Array> | null = null;

    vi.stubGlobal(
      "fetch",
      vi.fn(async (input: RequestInfo | URL) => {
        const url = String(input);
        if (url === "/api/projects") return jsonResponse([project]);
        if (url === "/api/sessions") return jsonResponse([currentSession]);
        if (url.startsWith("/api/skills/installed")) return jsonResponse([]);
        if (url.startsWith("/api/workspace/tree")) return jsonResponse([]);
        if (url.startsWith("/api/workspace/validate")) {
          return jsonResponse({ path: "/repo", name: "repo", is_git_repo: true, branch: "main" });
        }
        if (url.startsWith("/api/workspace/diff")) {
          return jsonResponse({ workspace_path: "/repo", branch: "main", git_head: "abc", files: [] });
        }
        if (url === "/api/sessions/sess-shell/artifacts") {
          return jsonResponse({ artifacts: [], walkthrough_plan: null });
        }
        if (url.startsWith("/api/sessions/sess-shell")) {
          if (url === "/api/sessions/sess-shell/resume") {
            currentSession = {
              ...currentSession,
              status: "idle",
              pending_interrupt: null,
            };
            const stream = new ReadableStream<Uint8Array>({
              start(ctrl) {
                resumeController = ctrl;
                ctrl.enqueue(enc.encode('event: token\ndata: {"text":"Removing stale.tmp..."}\n\n'));
              },
            });
            return new Response(stream, { status: 200 });
          }
          return jsonResponse({
            session: currentSession,
            messages: [
              {
                id: "u-shell",
                session_id: "sess-shell",
                role: "user",
                content: "Clean up stale.tmp",
                thinking: "",
                tool_calls: [],
                created_at: "2026-11-23T00:00:01Z",
              },
            ],
            has_more_messages: false,
          });
        }
        return jsonResponse({});
      }),
    );

    render(
      <Theme theme={themes[defaultThemeSlug]}>
        <App />
      </Theme>,
    );

    await screen.findByText("Shell command approval required");
    expect(screen.getByText("rm -f stale.tmp")).toBeInTheDocument();

    // Click Approve -> banner MUST disappear immediately even BEFORE resumeController closes!
    fireEvent.click(screen.getByRole("button", { name: "Approve" }));

    await waitFor(() => {
      expect(screen.queryByText("Shell command approval required")).not.toBeInTheDocument();
      expect(document.body.textContent).toContain("Removing stale.tmp...");
    });

    // Finish the stream cleanly
    await act(async () => {
      resumeController?.enqueue(enc.encode('event: done\ndata: {"session_id":"sess-shell"}\n\n'));
      resumeController?.close();
      await Promise.resolve();
    });
  });
});
