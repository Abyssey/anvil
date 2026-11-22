import { useCallback, useEffect, useLayoutEffect, useRef, useState } from "react";
import { Avatar } from "@astryxdesign/core/Avatar";
import { Banner } from "@astryxdesign/core/Banner";
import { Button } from "@astryxdesign/core/Button";
import {
  ChatLayout,
  ChatMessage,
  ChatMessageBubble,
  ChatMessageList,
  ChatToolCalls,
} from "@astryxdesign/core/Chat";
import { Collapsible } from "@astryxdesign/core/Collapsible";
import { Heading } from "@astryxdesign/core/Heading";
import { Markdown } from "@astryxdesign/core/Markdown";
import { Spinner } from "@astryxdesign/core/Spinner";
import { HStack, VStack } from "@astryxdesign/core/Stack";
import { Text } from "@astryxdesign/core/Text";
import { useWorkspace } from "../../state/workspaceContext";
import {
  computeAssistantTurnDurationMs,
  formatWorkedDuration,
  formatWorkingDuration,
  groupToolCalls,
  type GroupedToolBlock,
} from "../../utils/toolCallGroups";
import {
  ABYSS_MARKDOWN_PLUGINS,
  ABYSS_STREAMING_MARKDOWN_PLUGINS,
} from "../markdown/markdownPlugins";
import { Composer } from "./Composer";
import { ExplorationGroupView, toChatToolCallItem } from "./ExplorationGroupView";
import { ShellApprovalCard } from "./ShellApprovalCard";

const ONBOARDING_SAMPLE_PROMPT =
  "이 프로젝트 구조와 핵심 의존성을 파악해서 온보딩 문서로 정리해줘";

function renderGroupedToolBlocks(
  blocks: readonly GroupedToolBlock[],
  groupOpenOverrides: Readonly<Record<string, boolean>>,
  onToggleGroup: (groupId: string, nextOpen: boolean) => void,
) {
  if (blocks.length === 0) {
    return null;
  }
  return (
    <VStack gap={0.5} width="100%">
      {blocks.map((block, blockIndex) =>
        block.kind === "exploration_group" ? (
          <ExplorationGroupView
            key={block.id}
            group={block}
            isOpen={groupOpenOverrides[block.id] ?? false}
            onToggle={onToggleGroup}
          />
        ) : (
          <ChatToolCalls
            key={block.id}
            calls={[toChatToolCallItem(block.call, blockIndex)]}
          />
        ),
      )}
    </VStack>
  );
}

export function MessageStream() {
  const { state, actions, meta } = useWorkspace();
  const chatScrollRef = useRef<HTMLDivElement>(null);
  const pendingScrollRestoreRef = useRef<{
    prevHeight: number;
    prevTop: number;
    prevFirstId: string;
    sawLoading: boolean;
  } | null>(null);
  const [isTopObserverArmed, setIsTopObserverArmed] = useState(false);
  const [groupOpenOverrides, setGroupOpenOverrides] = useState<Record<string, boolean>>({});
  const [liveNowMs, setLiveNowMs] = useState<number>(() => Date.now());

  const sessionKey = state.activeSession?.id ?? "empty";
  const streamingStartedAtMs = state.streamingAssistant?.startedAtMs ?? null;
  const firstMsgId = state.messages[0]?.id ?? "";
  const lastMsgId = state.messages[state.messages.length - 1]?.id ?? "";
  const streamingContentLen = state.streamingAssistant?.content.length ?? 0;
  const streamingThinkingLen = state.streamingAssistant?.thinking.length ?? 0;
  const streamingToolSig =
    state.streamingAssistant?.toolCalls
      .map((tc) => `${tc.id}:${tc.status}:${tc.output.length}`)
      .join(",") ?? "";

  const pending = state.activeSession?.pending_interrupt;
  const pendingKind = pending?.kind ?? "";
  const showInlinePlanReview =
    pending?.kind === "plan_review" && state.rightDrawerTab !== "artifacts";
  const hasContent =
    state.messages.length > 0 || state.streamingAssistant !== null || Boolean(pending);

  const isTopObserverArmedRef = useRef(isTopObserverArmed);
  const hasMoreRef = useRef(state.hasMoreMessages);
  const isLoadingOlderRef = useRef(state.isLoadingOlderMessages);
  const firstMsgIdRef = useRef(firstMsgId);
  isTopObserverArmedRef.current = isTopObserverArmed;
  hasMoreRef.current = state.hasMoreMessages;
  isLoadingOlderRef.current = state.isLoadingOlderMessages;
  firstMsgIdRef.current = firstMsgId;

  const { loadOlderMessages } = actions;

  const handleToggleGroup = useCallback((groupId: string, nextOpen: boolean) => {
    setGroupOpenOverrides((prev) => ({ ...prev, [groupId]: nextOpen }));
  }, []);

  useEffect(() => {
    setGroupOpenOverrides({});
  }, [sessionKey]);

  useEffect(() => {
    if (!state.isStreaming || streamingStartedAtMs === null) {
      return;
    }
    setLiveNowMs(Date.now());
    const intervalId = window.setInterval(() => {
      setLiveNowMs(Date.now());
    }, 1000);
    return () => window.clearInterval(intervalId);
  }, [state.isStreaming, streamingStartedAtMs]);

  const streamingToolBlocks = state.streamingAssistant
    ? groupToolCalls(state.streamingAssistant.toolCalls, {
        isStreaming: state.isStreaming,
        hasAssistantTextStarted:
          state.streamingAssistant.hasTrailingAssistantText ??
          state.streamingAssistant.content.trim().length > 0,
        groupKeyPrefix: `${sessionKey}:stream`,
        textSplitIndices: state.streamingAssistant.textSplitAfterIndices,
      })
    : [];
  const liveWorkingLabel =
    state.isStreaming && streamingStartedAtMs !== null
      ? formatWorkingDuration(Math.max(0, liveNowMs - streamingStartedAtMs))
      : null;

  const triggerLoadOlder = useCallback(() => {
    if (!isTopObserverArmedRef.current || !hasMoreRef.current || isLoadingOlderRef.current) {
      return;
    }
    const el = chatScrollRef.current;
    if (el) {
      pendingScrollRestoreRef.current = {
        prevHeight: el.scrollHeight,
        prevTop: el.scrollTop,
        prevFirstId: firstMsgIdRef.current,
        sawLoading: false,
      };
    }
    void loadOlderMessages();
  }, [loadOlderMessages]);

  const handleScrollToTopAction = useCallback(async () => {
    triggerLoadOlder();
  }, [triggerLoadOlder]);

  // Reset and arm the top-sentinel observer only after the session has settled at the bottom.
  useLayoutEffect(() => {
    setIsTopObserverArmed(false);
    pendingScrollRestoreRef.current = null;
    const el = chatScrollRef.current;
    if (el) {
      el.scrollTop = el.scrollHeight;
    }
    const rafId = window.requestAnimationFrame(() => {
      const target = chatScrollRef.current;
      if (target) {
        target.scrollTop = target.scrollHeight;
      }
      setIsTopObserverArmed(true);
    });
    return () => window.cancelAnimationFrame(rafId);
  }, [sessionKey]);

  // Always follow newly appended messages and streaming assistant responses at the bottom.
  useLayoutEffect(() => {
    if (pendingScrollRestoreRef.current) {
      return;
    }
    const el = chatScrollRef.current;
    if (!el) {
      return;
    }
    el.scrollTop = el.scrollHeight;
    const rafId = window.requestAnimationFrame(() => {
      if (!pendingScrollRestoreRef.current && chatScrollRef.current) {
        chatScrollRef.current.scrollTop = chatScrollRef.current.scrollHeight;
      }
    });
    return () => window.cancelAnimationFrame(rafId);
  }, [
    sessionKey,
    lastMsgId,
    streamingContentLen,
    streamingThinkingLen,
    streamingToolSig,
    pendingKind,
  ]);

  // Preserve visual scroll position when older messages are prepended above the viewport.
  useLayoutEffect(() => {
    const restore = pendingScrollRestoreRef.current;
    if (!restore) {
      return;
    }
    if (firstMsgId !== restore.prevFirstId) {
      pendingScrollRestoreRef.current = null;
      const el = chatScrollRef.current;
      if (el) {
        el.scrollTop = restore.prevTop + Math.max(0, el.scrollHeight - restore.prevHeight);
      }
    } else if (state.isLoadingOlderMessages) {
      restore.sawLoading = true;
    } else if (restore.sawLoading) {
      pendingScrollRestoreRef.current = null;
    }
  }, [firstMsgId, state.messages.length, state.isLoadingOlderMessages]);

  // Scroll-top fallback alongside Astryx's IntersectionObserver sentinel.
  useEffect(() => {
    const el = chatScrollRef.current;
    if (!el) {
      return;
    }
    const onScroll = () => {
      if (
        el.scrollTop <= 48 &&
        isTopObserverArmedRef.current &&
        hasMoreRef.current &&
        !isLoadingOlderRef.current
      ) {
        triggerLoadOlder();
      }
    };
    el.addEventListener("scroll", onScroll, { passive: true });
    return () => el.removeEventListener("scroll", onScroll);
  }, [sessionKey, triggerLoadOlder]);

  const emptyState = (
    <VStack gap={4} hAlign="center" padding={6}>
      <Heading level={2}>Abyss Coding Agent</Heading>
      <Text color="secondary">
        Inspect any repository in seconds with Ask mode, or switch to Plan to review edits before execution.
      </Text>
      {!meta.configured && (
        <Banner
          status="info"
          title="Configure your LLM endpoint"
          description="Set Endpoint URL, Model ID (e.g. qwen3.8-flash), and API Key in Settings."
          endContent={
            <Button
              label="Open Settings"
              variant="primary"
              size="sm"
              onClick={actions.openSettings}
            />
          }
        />
      )}
      <Button
        label={ONBOARDING_SAMPLE_PROMPT}
        variant="secondary"
        onClick={() => void actions.sendMessage(ONBOARDING_SAMPLE_PROMPT)}
      />
    </VStack>
  );

  return (
    <ChatLayout
      key={sessionKey}
      ref={chatScrollRef}
      density="balanced"
      scrollButton={null}
      composer={<Composer />}
      emptyState={!hasContent ? emptyState : undefined}
    >
      {hasContent ? (
        <ChatMessageList
          isStreaming={state.isStreaming}
          scrollToTopAction={
            state.hasMoreMessages && isTopObserverArmed ? handleScrollToTopAction : undefined
          }
        >
          {state.isLoadingOlderMessages && (
            <HStack gap={2} hAlign="center" vAlign="center" padding={2} role="status">
              <Spinner size="sm" shade="subtle" aria-hidden="true" />
              <Text type="supporting" color="secondary">
                Loading older messages...
              </Text>
            </HStack>
          )}
          {state.messages.map((msg, msgIndex) => {
            if (msg.role === "user") {
              return (
                <ChatMessage key={msg.id} sender="user">
                  <ChatMessageBubble>{msg.content}</ChatMessageBubble>
                </ChatMessage>
              );
            }
            const toolBlocks = Array.isArray(msg.tool_calls)
              ? groupToolCalls(msg.tool_calls, {
                  isStreaming: false,
                  hasAssistantTextStarted: msg.content.trim().length > 0,
                  groupKeyPrefix: msg.id,
                })
              : [];
            const durationMs = computeAssistantTurnDurationMs(
              state.messages,
              msgIndex,
              state.turnDurationsMs,
            );
            return (
              <ChatMessage
                key={msg.id}
                sender="assistant"
                avatar={<Avatar name="Abyss" size="sm" />}
                metadata={
                  durationMs !== null ? (
                    <Text type="supporting" color="secondary" hasTabularNumbers>
                      {formatWorkedDuration(durationMs)}
                    </Text>
                  ) : undefined
                }
              >
                {msg.thinking && (
                  <Collapsible
                    defaultIsOpen={false}
                    trigger={
                      <Text type="label" color="secondary">
                        Thinking
                      </Text>
                    }
                  >
                    <ChatMessageBubble variant="ghost">
                      <Markdown density="compact" plugins={ABYSS_MARKDOWN_PLUGINS}>
                        {msg.thinking}
                      </Markdown>
                    </ChatMessageBubble>
                  </Collapsible>
                )}
                {renderGroupedToolBlocks(toolBlocks, groupOpenOverrides, handleToggleGroup)}
                {msg.content && (
                  <ChatMessageBubble variant="ghost" width="100%">
                    <Markdown density="compact" plugins={ABYSS_MARKDOWN_PLUGINS}>
                      {msg.content}
                    </Markdown>
                  </ChatMessageBubble>
                )}
              </ChatMessage>
            );
          })}

          {state.streamingAssistant && (
            <ChatMessage
              sender="assistant"
              avatar={<Avatar name="Abyss" size="sm" />}
              metadata={
                liveWorkingLabel ? (
                  <Text type="supporting" color="secondary" hasTabularNumbers>
                    {liveWorkingLabel}
                  </Text>
                ) : undefined
              }
            >
              {state.streamingAssistant.thinking && (
                <Collapsible
                  defaultIsOpen
                  trigger={
                    <Text type="label" color="secondary">
                      Thinking
                    </Text>
                  }
                >
                  <ChatMessageBubble variant="ghost">
                    <Markdown
                      density="compact"
                      isStreaming
                      plugins={ABYSS_STREAMING_MARKDOWN_PLUGINS}
                    >
                      {state.streamingAssistant.thinking}
                    </Markdown>
                  </ChatMessageBubble>
                </Collapsible>
              )}
              {renderGroupedToolBlocks(streamingToolBlocks, groupOpenOverrides, handleToggleGroup)}
              {state.streamingAssistant.content && (
                <ChatMessageBubble variant="ghost" width="100%">
                  <Markdown
                    density="compact"
                    isStreaming
                    plugins={ABYSS_STREAMING_MARKDOWN_PLUGINS}
                  >
                    {state.streamingAssistant.content}
                  </Markdown>
                </ChatMessageBubble>
              )}
            </ChatMessage>
          )}

          {showInlinePlanReview && (
            <Banner
              status="info"
              collapsible={false}
              title="Plan ready for review"
              description={
                pending.summary ?? "Open the implementation plan to review or proceed."
              }
            >
              <HStack gap={2} hAlign="end">
                <Button
                  label="Open Plan"
                  variant="secondary"
                  size="sm"
                  onClick={() => {
                    actions.setActiveArtifactTab("implementation_plan");
                    actions.openRightDrawer("artifacts");
                  }}
                />
                <Button
                  label="Cancel"
                  variant="ghost"
                  size="sm"
                  isDisabled={state.isStreaming}
                  onClick={() => void actions.resumeInterrupt("deny")}
                />
                <Button
                  label="Proceed"
                  variant="primary"
                  size="sm"
                  isDisabled={state.isStreaming}
                  onClick={() => void actions.resumeInterrupt("approve")}
                />
              </HStack>
            </Banner>
          )}

          <ShellApprovalCard />
        </ChatMessageList>
      ) : null}
    </ChatLayout>
  );
}
