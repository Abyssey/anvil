import { useEffect, useRef, useState } from "react";
import { ChatComposer, ChatComposerDrawer } from "@astryxdesign/core/Chat";
import { SegmentedControl, SegmentedControlItem } from "@astryxdesign/core/SegmentedControl";
import { HStack, VStack } from "@astryxdesign/core/Stack";
import { Token } from "@astryxdesign/core/Token";
import type { RunMode } from "../../api/types";
import { useWorkspace } from "../../state/workspaceContext";
import { UserQuestionCard } from "./UserQuestionCard";

const PLACEHOLDER_BY_MODE: Record<RunMode, string> = {
  ask: "Ask about this codebase (read-only exploration)...",
  plan: "Describe a task to plan, approve, and implement...",
  auto: "Describe a task to execute autonomously...",
};

export function Composer() {
  const { state, actions } = useWorkspace();
  const [draft, setDraft] = useState("");
  const lastRewoundSeqRef = useRef<number | null>(null);

  useEffect(() => {
    if (state.rewoundDraft && state.rewoundDraft.seq !== lastRewoundSeqRef.current) {
      lastRewoundSeqRef.current = state.rewoundDraft.seq;
      setDraft(state.rewoundDraft.text);
    }
  }, [state.rewoundDraft]);

  const pendingInterrupt = state.activeSession?.pending_interrupt ?? null;
  const hasPendingInterrupt = Boolean(pendingInterrupt);
  const isUserQuestionPending = pendingInterrupt?.kind === "user_question";

  const handleSubmit = (submitted: string) => {
    const trimmed = submitted.trim();
    if (!trimmed) return;
    setDraft("");
    void actions.sendMessage(trimmed);
  };

  return (
    <VStack gap={2} width="100%">
      {isUserQuestionPending && pendingInterrupt && (
        <UserQuestionCard pending={pendingInterrupt} />
      )}
      <ChatComposer
        value={draft}
        onChange={setDraft}
        onSubmit={handleSubmit}
        onStop={actions.stopStream}
        isStopShown={state.isStreaming}
        isDisabled={hasPendingInterrupt || state.isStreaming}
        placeholder={
          isUserQuestionPending
            ? "Answer the question above to continue..."
            : PLACEHOLDER_BY_MODE[state.mode]
        }
        status={state.streamError ? { type: "error", message: state.streamError } : undefined}
        drawer={
          state.pinnedSkills.length > 0 ? (
            <ChatComposerDrawer>
              <HStack gap={1.5} vAlign="center" wrap="wrap">
                {state.pinnedSkills.map((skillName) => (
                  <Token
                    key={skillName}
                    label={`/${skillName}`}
                    size="sm"
                    color="purple"
                    onRemove={() => actions.unpinSkill(skillName)}
                  />
                ))}
              </HStack>
            </ChatComposerDrawer>
          ) : undefined
        }
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
    </VStack>
  );
}
