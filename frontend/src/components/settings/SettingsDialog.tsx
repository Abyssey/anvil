import { useEffect, useState } from "react";
import { Banner } from "@astryxdesign/core/Banner";
import { Button } from "@astryxdesign/core/Button";
import { Dialog, DialogHeader } from "@astryxdesign/core/Dialog";
import { Layout, LayoutContent, LayoutFooter } from "@astryxdesign/core/Layout";
import { HStack, VStack } from "@astryxdesign/core/Stack";
import { Switch } from "@astryxdesign/core/Switch";
import { TextInput } from "@astryxdesign/core/TextInput";
import { apiClient } from "../../api/client";
import { DEFAULT_LLM_SETTINGS, isLLMConfigured, type LLMSettings } from "../../state/settings";
import { useWorkspace } from "../../state/workspaceContext";

export function SettingsDialog() {
  const { state, actions } = useWorkspace();
  const [draft, setDraft] = useState<LLMSettings>(state.settings);
  const [pingResult, setPingResult] = useState<{ status: "success" | "error"; text: string } | null>(
    null,
  );
  const [isTesting, setIsTesting] = useState(false);

  useEffect(() => {
    if (state.settingsOpen) {
      setDraft(state.settings);
      setPingResult(null);
    }
  }, [state.settingsOpen, state.settings]);

  const handleTestConnection = async () => {
    if (!isLLMConfigured(draft)) {
      setPingResult({
        status: "error",
        text: "Enter a valid http(s) Endpoint URL, Model ID, and API Key first.",
      });
      return;
    }
    setIsTesting(true);
    setPingResult(null);
    try {
      const res = await apiClient.pingLLM(draft);
      setPingResult({
        status: "success",
        text: `Connected to ${res.base_url} (${res.model}).`,
      });
    } catch (err) {
      setPingResult({
        status: "error",
        text: err instanceof Error ? err.message : "Connection failed.",
      });
    } finally {
      setIsTesting(false);
    }
  };

  const handleSave = () => {
    actions.updateSettings(draft);
    actions.closeSettings();
  };

  const handleClear = () => {
    actions.clearSettings();
    setDraft({ ...DEFAULT_LLM_SETTINGS });
    setPingResult(null);
  };

  return (
    <Dialog
      isOpen={state.settingsOpen}
      onOpenChange={(open) => {
        if (!open) actions.closeSettings();
      }}
      purpose="form"
      width={480}
    >
      <Layout
        height="auto"
        header={
          <DialogHeader
            title="LLM Endpoint Settings"
            subtitle="OpenAI-compatible provider credentials (stored in your browser only)."
            onOpenChange={(open) => {
              if (!open) actions.closeSettings();
            }}
          />
        }
        content={
          <LayoutContent padding={4}>
            <VStack gap={4}>
              <TextInput
                label="Endpoint URL"
                type="url"
                value={draft.baseUrl}
                onChange={(val) => setDraft((prev) => ({ ...prev, baseUrl: val }))}
                placeholder="http://localhost:8001/v1"
                description="Base URL for any OpenAI-compatible API (DashScope, vLLM, SGLang, OpenAI)."
                isRequired
                hasAutoFocus
              />
              <TextInput
                label="Model ID"
                value={draft.model}
                onChange={(val) => setDraft((prev) => ({ ...prev, model: val }))}
                placeholder="qwen3.8-flash"
                isRequired
              />
              <TextInput
                label="API Key"
                type="password"
                value={draft.apiKey}
                onChange={(val) => setDraft((prev) => ({ ...prev, apiKey: val }))}
                placeholder="sk-..."
                description="Sent via X-LLM-Api-Key header per request; never saved on the server."
                hasClear
                isRequired
              />
              <Switch
                label="Enable Thinking"
                description="Stream Qwen 3.8 Flash reasoning_content traces into collapsible blocks."
                value={draft.enableThinking}
                onChange={(checked) =>
                  setDraft((prev) => ({ ...prev, enableThinking: checked }))
                }
              />
              {pingResult && (
                <Banner status={pingResult.status} title={pingResult.text} />
              )}
            </VStack>
          </LayoutContent>
        }
        footer={
          <LayoutFooter hasDivider>
            <HStack hAlign="between" vAlign="center" width="100%">
              <HStack gap={2}>
                <Button
                  label="Test Connection"
                  variant="ghost"
                  size="sm"
                  isLoading={isTesting}
                  onClick={() => void handleTestConnection()}
                />
                <Button label="Clear" variant="ghost" size="sm" onClick={handleClear} />
              </HStack>
              <HStack gap={2}>
                <Button label="Cancel" variant="secondary" onClick={actions.closeSettings} />
                <Button
                  label="Save"
                  variant="primary"
                  isDisabled={!isLLMConfigured(draft)}
                  onClick={handleSave}
                />
              </HStack>
            </HStack>
          </LayoutFooter>
        }
      />
    </Dialog>
  );
}
