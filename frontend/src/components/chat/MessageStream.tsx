import { Avatar } from "@astryxdesign/core/Avatar";
import { Banner } from "@astryxdesign/core/Banner";
import { Button } from "@astryxdesign/core/Button";
import {
  ChatLayout,
  ChatMessage,
  ChatMessageBubble,
  ChatMessageList,
  ChatToolCalls,
  type ChatToolCallItem,
} from "@astryxdesign/core/Chat";
import { CodeBlock } from "@astryxdesign/core/CodeBlock";
import { Collapsible } from "@astryxdesign/core/Collapsible";
import { Heading } from "@astryxdesign/core/Heading";
import { Markdown } from "@astryxdesign/core/Markdown";
import { HStack, VStack } from "@astryxdesign/core/Stack";
import { Text } from "@astryxdesign/core/Text";
import type { ToolCallRecord } from "../../api/types";
import { useWorkspace } from "../../state/workspaceContext";
import { Composer } from "./Composer";
import { ShellApprovalCard } from "./ShellApprovalCard";

const ONBOARDING_SAMPLE_PROMPT =
  "이 프로젝트 구조와 핵심 의존성을 파악해서 온보딩 문서로 정리해줘";

function formatToolTarget(args: Record<string, unknown>): string | undefined {
  for (const key of ["file_path", "path", "pattern", "command", "subagent_type"]) {
    const val = args[key];
    if (typeof val === "string" && val.trim()) {
      return val.trim();
    }
  }
  return undefined;
}

function toChatToolCalls(records: ToolCallRecord[]): ChatToolCallItem[] {
  return records.map((tc) => ({
    key: tc.id || tc.name,
    name: tc.name,
    target: formatToolTarget(tc.args),
    status: tc.status === "running" ? "running" : tc.status === "error" ? "error" : "complete",
    node: tc.subagent ?? undefined,
    resultDetail: tc.output ? (
      <CodeBlock code={tc.output} language="text" maxHeight={180} width="100%" />
    ) : undefined,
  }));
}

export function MessageStream() {
  const { state, actions, meta } = useWorkspace();

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

  const pending = state.activeSession?.pending_interrupt;
  const showInlinePlanReview =
    pending?.kind === "plan_review" && state.rightDrawerTab !== "artifacts";
  const hasContent =
    state.messages.length > 0 || state.streamingAssistant !== null || Boolean(pending);

  return (
    <ChatLayout
      density="balanced"
      composer={<Composer />}
      emptyState={!hasContent ? emptyState : undefined}
    >
      {hasContent ? (
        <ChatMessageList isStreaming={state.isStreaming}>
          {state.messages.map((msg) =>
            msg.role === "user" ? (
              <ChatMessage key={msg.id} sender="user">
                <ChatMessageBubble>{msg.content}</ChatMessageBubble>
              </ChatMessage>
            ) : (
              <ChatMessage
                key={msg.id}
                sender="assistant"
                avatar={<Avatar name="Abyss" size="sm" />}
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
                      <Markdown density="compact">{msg.thinking}</Markdown>
                    </ChatMessageBubble>
                  </Collapsible>
                )}
                {Array.isArray(msg.tool_calls) && msg.tool_calls.length > 0 && (
                  <ChatToolCalls calls={toChatToolCalls(msg.tool_calls)} />
                )}
                {msg.content && (
                  <ChatMessageBubble variant="ghost">
                    <Markdown density="compact">{msg.content}</Markdown>
                  </ChatMessageBubble>
                )}
              </ChatMessage>
            ),
          )}

          {state.streamingAssistant && (
            <ChatMessage sender="assistant" avatar={<Avatar name="Abyss" size="sm" />}>
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
                    <Markdown density="compact" isStreaming>
                      {state.streamingAssistant.thinking}
                    </Markdown>
                  </ChatMessageBubble>
                </Collapsible>
              )}
              {state.streamingAssistant.toolCalls.length > 0 && (
                <ChatToolCalls
                  defaultIsExpanded
                  calls={toChatToolCalls(state.streamingAssistant.toolCalls)}
                />
              )}
              {state.streamingAssistant.content && (
                <ChatMessageBubble variant="ghost">
                  <Markdown density="compact" isStreaming>
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
