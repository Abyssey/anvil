export interface LLMSettings {
  baseUrl: string;
  model: string;
  apiKey: string;
  enableThinking: boolean;
}

export const SETTINGS_STORAGE_KEY = "abyss.llm.settings.v1";

export const DEFAULT_LLM_SETTINGS: LLMSettings = {
  baseUrl: "",
  model: "qwen3.8-flash",
  apiKey: "",
  enableThinking: true,
};

export function loadLLMSettings(): LLMSettings {
  try {
    const raw = window.localStorage.getItem(SETTINGS_STORAGE_KEY);
    if (!raw) {
      return { ...DEFAULT_LLM_SETTINGS };
    }
    const parsed = JSON.parse(raw) as Partial<LLMSettings>;
    return {
      baseUrl: typeof parsed.baseUrl === "string" ? parsed.baseUrl : DEFAULT_LLM_SETTINGS.baseUrl,
      model:
        typeof parsed.model === "string" && parsed.model.trim()
          ? parsed.model
          : DEFAULT_LLM_SETTINGS.model,
      apiKey: typeof parsed.apiKey === "string" ? parsed.apiKey : "",
      enableThinking:
        typeof parsed.enableThinking === "boolean"
          ? parsed.enableThinking
          : DEFAULT_LLM_SETTINGS.enableThinking,
    };
  } catch {
    return { ...DEFAULT_LLM_SETTINGS };
  }
}

export function saveLLMSettings(settings: LLMSettings): LLMSettings {
  const normalized: LLMSettings = {
    baseUrl: settings.baseUrl.trim().replace(/\/+$/, ""),
    model: settings.model.trim() || DEFAULT_LLM_SETTINGS.model,
    apiKey: settings.apiKey.trim(),
    enableThinking: settings.enableThinking,
  };
  window.localStorage.setItem(SETTINGS_STORAGE_KEY, JSON.stringify(normalized));
  return normalized;
}

export function clearLLMSettings(): LLMSettings {
  window.localStorage.removeItem(SETTINGS_STORAGE_KEY);
  return { ...DEFAULT_LLM_SETTINGS };
}

export function isLLMConfigured(settings: LLMSettings): boolean {
  const url = settings.baseUrl.trim();
  const hasProtocol = url.startsWith("http://") || url.startsWith("https://");
  return hasProtocol && settings.model.trim().length > 0 && settings.apiKey.trim().length > 0;
}
