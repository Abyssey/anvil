import { ChatToolCalls, type ChatToolCallItem } from "@astryxdesign/core/Chat";
import { CodeBlock } from "@astryxdesign/core/CodeBlock";
import { Collapsible } from "@astryxdesign/core/Collapsible";
import { Spinner } from "@astryxdesign/core/Spinner";
import { HStack, VStack } from "@astryxdesign/core/Stack";
import { Text } from "@astryxdesign/core/Text";
import type { ToolCallRecord } from "../../api/types";
import {
  formatToolTarget,
  type ExplorationGroupBlock,
} from "../../utils/toolCallGroups";

export function toChatToolCallItem(tc: ToolCallRecord, index: number): ChatToolCallItem {
  const status =
    tc.status === "running"
      ? "running"
      : tc.status === "pending"
        ? "pending"
        : tc.status === "error" || tc.status === "cancelled"
          ? "error"
          : "complete";

  return {
    key: tc.id || `${tc.name}-${index}`,
    name: tc.name,
    target: formatToolTarget(tc.args, tc.name),
    status,
    node: tc.subagent ?? undefined,
    errorMessage: status === "error" && tc.output ? tc.output.slice(0, 200) : undefined,
    resultDetail: tc.output ? (
      <CodeBlock code={tc.output} language="plaintext" size="sm" maxHeight={160} width="100%" />
    ) : undefined,
  };
}

export interface ExplorationGroupViewProps {
  group: ExplorationGroupBlock;
  isOpen: boolean;
  onToggle: (groupId: string, nextOpen: boolean) => void;
}

export function ExplorationGroupView({
  group,
  isOpen,
  onToggle,
}: ExplorationGroupViewProps) {
  if (group.status === "running") {
    return (
      <HStack gap={2} vAlign="center">
        <Spinner size="sm" shade="subtle" aria-hidden="true" />
        <Text type="supporting" color="secondary">
          {group.activeLabel}
        </Text>
      </HStack>
    );
  }

  return (
    <Collapsible
      isOpen={isOpen}
      onOpenChange={(next) => onToggle(group.id, next)}
      chevronPosition="start"
      trigger={
        <Text type="supporting" color="secondary">
          {group.summaryLabel}
        </Text>
      }
    >
      {isOpen ? (
        <VStack gap={0} paddingInlineStart={3}>
          {group.calls.map((tc, index) => (
            <ChatToolCalls
              key={tc.id || `${tc.name}-${index}`}
              calls={[toChatToolCallItem(tc, index)]}
            />
          ))}
        </VStack>
      ) : null}
    </Collapsible>
  );
}
