import { beforeEach, describe, expect, it } from "vitest";
import {
  clearLLMSettings,
  DEFAULT_LLM_SETTINGS,
  isLLMConfigured,
  loadLLMSettings,
  saveLLMSettings,
} from "../settings";

describe("LLMSettings localStorage persistence", () => {
  beforeEach(() => {
    window.localStorage.clear();
  });

  it("defaults to qwen3.8-flash with thinking enabled and unconfigured status", () => {
    const loaded = loadLLMSettings();
    expect(loaded).toEqual(DEFAULT_LLM_SETTINGS);
    expect(loaded.model).toBe("qwen3.8-flash");
    expect(loaded.enableThinking).toBe(true);
    expect(isLLMConfigured(loaded)).toBe(false);
  });

  it("saves normalized credentials and validates http(s) URLs", () => {
    const saved = saveLLMSettings({
      baseUrl: "http://localhost:8001/v1/",
      model: "qwen3.8-flash",
      apiKey: " sk-test-123 ",
      enableThinking: false,
    });
    expect(saved.baseUrl).toBe("http://localhost:8001/v1");
    expect(saved.apiKey).toBe("sk-test-123");
    expect(isLLMConfigured(saved)).toBe(true);
    expect(loadLLMSettings()).toEqual(saved);

    const cleared = clearLLMSettings();
    expect(isLLMConfigured(cleared)).toBe(false);
  });

  it("strips /chat/completions and /models suffixes from pasted endpoint URLs", () => {
    const fromCompletions = saveLLMSettings({
      baseUrl: "http://localhost:8001/v1/chat/completions/",
      model: "qwen3.8-flash",
      apiKey: "sk-test",
      enableThinking: true,
    });
    expect(fromCompletions.baseUrl).toBe("http://localhost:8001/v1");

    const fromModels = saveLLMSettings({
      baseUrl: "https://dashscope-intl.aliyuncs.com/compatible-mode/v1/models",
      model: "qwen3.8-flash",
      apiKey: "sk-test",
      enableThinking: true,
    });
    expect(fromModels.baseUrl).toBe("https://dashscope-intl.aliyuncs.com/compatible-mode/v1");
  });
});
