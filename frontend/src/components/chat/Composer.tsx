import { useEffect, useRef, useState } from "react";
import { Badge } from "@astryxdesign/core/Badge";
import { Button } from "@astryxdesign/core/Button";
import { ChatComposer, ChatComposerDrawer } from "@astryxdesign/core/Chat";
import { useHotkeys } from "@astryxdesign/core/hooks";
import { Icon } from "@astryxdesign/core/Icon";
import { IconButton } from "@astryxdesign/core/IconButton";
import { Kbd } from "@astryxdesign/core/Kbd";
import { SegmentedControl, SegmentedControlItem } from "@astryxdesign/core/SegmentedControl";
import { HStack, StackItem, VStack } from "@astryxdesign/core/Stack";
import { Text } from "@astryxdesign/core/Text";
import { Token } from "@astryxdesign/core/Token";
import {
  PaperAirplaneIcon,
  PencilSquareIcon,
  StopIcon,
  XMarkIcon,
} from "@heroicons/react/24/outline";
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
  const queuedMessages = state.queuedMessages ?? [];
  const isQueuePaused = Boolean(state.isQueuePaused);
  const trimmedDraft = draft.trim();
  const hasStreamingDraft = state.isStreaming && trimmedDraft.length > 0;

  useHotkeys([
    {
      keys: "mod+enter",
      allowInInputs: true,
      isDisabled: !state.isStreaming || hasPendingInterrupt,
      onPress: () => {
        const current = draft.trim();
        if (current) {
          setDraft("");
          void actions.steerMessage?.(current);
        } else if (queuedMessages.length > 0) {
          void actions.steerQueuedMessage?.(queuedMessages[0].id);
        }
      },
    },
  ]);

  const handleSubmit = (submitted: string) => {
    const trimmed = submitted.trim();
    if (!trimmed) return;
    setDraft("");
    if (state.isStreaming) {
      if (actions.enqueueMessage) {
        void actions.enqueueMessage(trimmed);
      }
      return;
    }
    void actions.sendMessage(trimmed);
  };

  const handleSteerDraft = () => {
    if (!trimmedDraft) return;
    setDraft("");
    void actions.steerMessage?.(trimmedDraft);
  };

  const hasDrawerContent = state.pinnedSkills.length > 0 || queuedMessages.length > 0;

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
        isStopShown={state.isStreaming && !hasStreamingDraft}
        isDisabled={hasPendingInterrupt}
        placeholder={
          isUserQuestionPending
            ? "Answer the question above to continue..."
            : state.isStreaming
              ? "Queue follow-up (Enter) or steer active run..."
              : PLACEHOLDER_BY_MODE[state.mode]
        }
        status={state.streamError ? { type: "error", message: state.streamError } : undefined}
        drawer={
          hasDrawerContent ? (
            <ChatComposerDrawer>
              <VStack gap={2} width="100%">
                {queuedMessages.length > 0 && (
                  <VStack gap={1.5} width="100%">
                    <HStack hAlign="between" vAlign="center" gap={2}>
                      <HStack gap={1.5} vAlign="center">
                        <Text type="label" weight="semibold">
                          {`Queued (${queuedMessages.length})`}
                        </Text>
                        {isQueuePaused && !state.isStreaming && (
                          <Badge label="Paused" variant="warning" />
                        )}
                      </HStack>
                      {state.isStreaming && (
                        <HStack gap={1} vAlign="center">
                          <Text type="supporting" color="secondary">
                            Steer
                          </Text>
                          <Kbd keys="mod+enter" />
                        </HStack>
                      )}
                    </HStack>

                    {queuedMessages.map((item, idx) => (
                      <HStack key={item.id} gap={2} vAlign="center" width="100%">
                        <StackItem size="static">
                          <Kbd keys={String(idx + 1)} />
                        </StackItem>
                        <StackItem size="fill">
                          <HStack gap={1.5} vAlign="center" width="100%">
                            <StackItem size="fill">
                              <Text type="body" size="sm" maxLines={1}>
                                {item.content}
                              </Text>
                            </StackItem>
                            {item.pinnedSkills.map((pin) => (
                              <StackItem key={pin} size="static">
                                <Kbd keys={`/${pin}`} />
                              </StackItem>
                            ))}
                          </HStack>
                        </StackItem>
                        <StackItem size="static">
                          <HStack gap={1} vAlign="center">
                            {state.isStreaming ? (
                              <Button
                                label="Steer"
                                variant="secondary"
                                size="sm"
                                tooltip="Inject immediately at next step"
                                onClick={() => void actions.steerQueuedMessage?.(item.id)}
                              />
                            ) : (
                              <IconButton
                                label={`Send queued message ${idx + 1}`}
                                tooltip="Send now"
                                variant="primary"
                                size="sm"
                                icon={<Icon icon={PaperAirplaneIcon} size="sm" />}
                                onClick={() => void actions.sendQueuedMessageNow?.(item.id)}
                              />
                            )}
                            <IconButton
                              label={`Edit queued message ${idx + 1}`}
                              tooltip="Edit in composer"
                              variant="ghost"
                              size="sm"
                              icon={<Icon icon={PencilSquareIcon} size="sm" />}
                              onClick={() => actions.editQueuedMessage?.(item.id)}
                            />
                            <IconButton
                              label={`Remove queued message ${idx + 1}`}
                              tooltip="Remove from queue"
                              variant="ghost"
                              size="sm"
                              icon={<Icon icon={XMarkIcon} size="sm" />}
                              onClick={() => actions.removeQueuedMessage?.(item.id)}
                            />
                          </HStack>
                        </StackItem>
                      </HStack>
                    ))}
                  </VStack>
                )}

                {state.pinnedSkills.length > 0 && (
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
                )}
              </VStack>
            </ChatComposerDrawer>
          ) : undefined
        }
        sendActions={
          hasStreamingDraft ? (
            <HStack gap={1} vAlign="center">
              <Button
                label="Steer"
                variant="secondary"
                size="sm"
                tooltip="Steer active run now"
                onClick={handleSteerDraft}
              />
              <Kbd keys="mod+enter" />
              <IconButton
                label="Stop generation"
                tooltip="Stop"
                variant="ghost"
                size="sm"
                icon={<Icon icon={StopIcon} size="sm" />}
                onClick={actions.stopStream}
              />
            </HStack>
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
