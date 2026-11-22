import { afterEach, describe, expect, it, vi } from "vitest";
import { apiClient, ApiError, llmHeaders } from "../client";
import type { StreamEvent } from "../types";

describe("apiClient SSE streaming and X-LLM headers", () => {
  afterEach(() => {
    vi.restoreAllMocks();
  });

  it("maps settings to X-LLM-* headers without mutating URL", () => {
    const headers = llmHeaders({
      baseUrl: "http://localhost:8001/v1",
      model: "qwen3.8-flash",
      apiKey: "sk-secret",
      enableThinking: true,
    });
    expect(headers).toEqual({
      "X-LLM-Base-Url": "http://localhost:8001/v1",
      "X-LLM-Model": "qwen3.8-flash",
      "X-LLM-Api-Key": "sk-secret",
      "X-LLM-Enable-Thinking": "true",
    });
  });

  it("throws ApiError with HTTP 428 LLM_SETTINGS_REQUIRED when settings missing", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(
        new Response(
          JSON.stringify({
            error: {
              code: "LLM_SETTINGS_REQUIRED",
              message: "Configure Endpoint URL, Model ID, and API Key in Settings.",
            },
          }),
          { status: 428, headers: { "Content-Type": "application/json" } },
        ),
      ),
    );

    const controller = new AbortController();
    await expect(
      apiClient.streamSSE(
        "/api/sessions/s1/messages/stream",
        { content: "hi", mode: "ask" },
        { baseUrl: "", model: "qwen3.8-flash", apiKey: "", enableThinking: true },
        controller.signal,
        () => undefined,
      ),
    ).rejects.toSatisfy(
      (err: unknown) =>
        err instanceof ApiError && err.status === 428 && err.code === "LLM_SETTINGS_REQUIRED",
    );
  });

  it("parses chunked SSE events across ReadableStream boundaries", async () => {
    const encoder = new TextEncoder();
    const chunks = [
      encoder.encode('event: thinking\ndata: {"text":"Inspecting"}\n\nevent: tok'),
      encoder.encode('en\ndata: {"text":"Hello"}\n\nevent: done\ndata: {"session_id":"s1"}\n\n'),
    ];
    const stream = new ReadableStream<Uint8Array>({
      start(ctrl) {
        for (const chunk of chunks) {
          ctrl.enqueue(chunk);
        }
        ctrl.close();
      },
    });
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(new Response(stream, { status: 200 })),
    );

    const received: StreamEvent[] = [];
    const controller = new AbortController();
    await apiClient.streamSSE(
      "/api/sessions/s1/messages/stream",
      { content: "hi", mode: "ask" },
      {
        baseUrl: "http://localhost:8001/v1",
        model: "qwen3.8-flash",
        apiKey: "sk-test",
        enableThinking: true,
      },
      controller.signal,
      (ev) => received.push(ev),
    );

    expect(received).toEqual([
      { event: "thinking", data: { text: "Inspecting" } },
      { event: "token", data: { text: "Hello" } },
      { event: "done", data: { session_id: "s1" } },
    ]);
  });

  it("normalizes /chat/completions in llmHeaders and reassembles split multi-byte UTF-8 SSE frames", async () => {
    const headers = llmHeaders({
      baseUrl: "http://localhost:8001/v1/chat/completions/",
      model: "qwen3.8-flash",
      apiKey: "sk-secret",
      enableThinking: true,
    });
    expect(headers["X-LLM-Base-Url"]).toBe("http://localhost:8001/v1");

    const fullBytes = new TextEncoder().encode(
      'event: token\ndata: {"text":"온보딩 문서"}'
    );
    // Split right inside the 3-byte UTF-8 sequence for "온" (after "event: token\ndata: {\"text\":\"" = 28 bytes + 1 byte = 29)
    const chunks = [fullBytes.slice(0, 29), fullBytes.slice(29)];
    const stream = new ReadableStream<Uint8Array>({
      start(ctrl) {
        for (const chunk of chunks) {
          ctrl.enqueue(chunk);
        }
        ctrl.close();
      },
    });
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(new Response(stream, { status: 200 })),
    );

    const received: StreamEvent[] = [];
    await apiClient.streamSSE(
      "/api/sessions/s1/messages/stream",
      { content: "온보딩", mode: "ask" },
      {
        baseUrl: "http://localhost:8001/v1/chat/completions",
        model: "qwen3.8-flash",
        apiKey: "sk-test",
        enableThinking: true,
      },
      new AbortController().signal,
      (ev) => received.push(ev),
    );
    expect(received).toEqual([{ event: "token", data: { text: "온보딩 문서" } }]);
  });
});
