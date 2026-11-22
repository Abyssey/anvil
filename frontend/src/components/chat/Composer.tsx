import { useState } from "react";
import { ChatComposer } from "@astryxdesign/core/Chat";
import { SegmentedControl, SegmentedControlItem } from "@astryxdesign/core/SegmentedControl";
import type { RunMode } from "../../api/types";
import { useWorkspace } from "../../state/workspaceContext";

const PLACEHOLDER_BY_MODE: Record<RunMode, string> = {
  ask: "Ask about this codebase (read-only exploration)...",
  plan: "Describe a task to plan, approve, and implement...",
  auto: "Describe a task to execute autonomously...",
};

export function Composer() {
  const { state, actions } = useWorkspace();
  const [draft, setDraft] = useState("");

  const hasPendingInterrupt = Boolean(state.activeSession?.pending_interrupt);

  const handleSubmit = (submitted: string) => {
    const trimmed = submitted.trim();
    if (!trimmed) return;
    setDraft("");
    void actions.sendMessage(trimmed);
  };

  return (
    <ChatComposer
      value={draft}
      onChange={setDraft}
      onSubmit={handleSubmit}
      onStop={actions.stopStream}
      isStopShown={state.isStreaming}
      isDisabled={hasPendingInterrupt || state.isStreaming}
      placeholder={PLACEHOLDER_BY_MODE[state.mode]}
      status={state.streamError ? { type: "error", message: state.streamError } : undefined}
      footerActions={
        <SegmentedControl
          label="Execution mode"
          size="sm"
          value={state.mode}
          isDisabled={state.isStreaming}
          onChange={(val) => void actions.setMode(val as RunMode)}
        >
          <SegmentedControlItem value="ask" label="Ask" />
          <SegmentedControlItem value="plan" label="Plan" />
          <SegmentedControlItem value="auto" label="Auto" />
        </SegmentedControl>
      }
    />
  );
}
