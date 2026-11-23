import { Badge } from "@astryxdesign/core/Badge";
import { Card } from "@astryxdesign/core/Card";
import { ChatToolCalls } from "@astryxdesign/core/Chat";
import { Collapsible } from "@astryxdesign/core/Collapsible";
import { Divider } from "@astryxdesign/core/Divider";
import { Icon } from "@astryxdesign/core/Icon";
import { Markdown } from "@astryxdesign/core/Markdown";
import { Spinner } from "@astryxdesign/core/Spinner";
import { HStack, StackItem, VStack } from "@astryxdesign/core/Stack";
import { Text } from "@astryxdesign/core/Text";
import {
  CpuChipIcon,
  MagnifyingGlassIcon,
  ShieldCheckIcon,
  SparklesIcon,
} from "@heroicons/react/24/outline";
import type {
  SubagentGroupBlock,
  SubagentRole,
  SubagentRunItem,
} from "../../utils/toolCallGroups";
import { MARKDOWN_PLUGINS } from "../markdown/markdownPlugins";
import { toChatToolCallItem } from "./ExplorationGroupView";

function roleBadgeVariant(role: SubagentRole): "purple" | "blue" | "cyan" {
  if (role === "reviewer") return "purple";
  if (role === "explorer") return "blue";
  return "cyan";
}

function roleIconFor(role: SubagentRole) {
  if (role === "reviewer") return ShieldCheckIcon;
  if (role === "explorer") return MagnifyingGlassIcon;
  return CpuChipIcon;
}

function startsWithMarkdownHeading(text: string): boolean {
  return /^\s*#{1,6}\s+\S/.test(text);
}

function renderStatusBadge(item: SubagentRunItem) {
  if (item.status === "running") {
    return null;
  }
  if (item.status === "error") {
    return <Badge label="Failed" variant="error" />;
  }
  if (item.role === "reviewer" && item.findingCounts) {
    if (item.findingCounts.important > 0) {
      return (
        <Badge
          label={`${item.findingCounts.important} Important`}
          variant="orange"
        />
      );
    }
    if (item.findingCounts.nit > 0) {
      return (
        <Badge
          label={`Clean · ${item.findingCounts.nit} Nit`}
          variant="green"
        />
      );
    }
    return <Badge label="Review clean" variant="green" />;
  }
  return null;
}

export interface SubagentGroupViewProps {
  group: SubagentGroupBlock;
  openOverrides: Readonly<Record<string, boolean>>;
  onToggle: (itemId: string, nextOpen: boolean) => void;
}

export function SubagentGroupView({
  group,
  openOverrides,
  onToggle,
}: SubagentGroupViewProps) {
  return (
    <VStack gap={1} paddingBlockStart={1} paddingBlockEnd={1.5} width="100%">
      {group.isParallel && (
        <HStack gap={1.5} vAlign="center">
          {group.runningCount > 0 ? (
            <Spinner size="sm" shade="subtle" aria-hidden="true" />
          ) : (
            <Icon icon={SparklesIcon} size="sm" color="secondary" />
          )}
          <Text type="supporting" color="secondary" hasTabularNumbers>
            {group.runningCount > 0
              ? `병렬 서브에이전트 ${group.items.length}개 실행 중 (${group.runningCount}개 활성)`
              : `병렬 서브에이전트 ${group.items.length}개 완료`}
          </Text>
        </HStack>
      )}

      <VStack gap={1.5} width="100%">
        {group.items.map((item) => {
          const hasNestedTools = item.nestedCalls.length > 0;
          const hasReport = item.output.trim().length > 0;
          const hasExpandableContent = hasNestedTools || hasReport;
          const autoExpandImportant =
            item.role === "reviewer" && (item.findingCounts?.important ?? 0) > 0;
          const isOpen = hasExpandableContent
            ? (openOverrides[item.id] ?? autoExpandImportant)
            : false;
          const statusBadge = renderStatusBadge(item);
          const RoleIcon = roleIconFor(item.role);
          const showReportLabel = hasReport && !startsWithMarkdownHeading(item.output);

          return (
            <Card key={item.id} variant="muted" width="100%">
              <Collapsible
                isOpen={isOpen}
                isDisabled={!hasExpandableContent}
                onOpenChange={(next) => onToggle(item.id, next)}
                chevronPosition="end"
                trigger={
                  <VStack gap={0.5} width="100%">
                    <HStack hAlign="between" vAlign="center" gap={1.5} wrap="wrap" width="100%">
                      <HStack gap={1.5} vAlign="center" wrap="wrap">
                        <StackItem size="static">
                          {item.status === "running" ? (
                            <Spinner size="sm" shade="subtle" aria-hidden="true" />
                          ) : (
                            <Icon
                              icon={RoleIcon}
                              size="sm"
                              color={item.status === "error" ? "error" : "secondary"}
                            />
                          )}
                        </StackItem>
                        <StackItem size="static">
                          <Badge
                            label={item.roleLabel}
                            variant={roleBadgeVariant(item.role)}
                          />
                        </StackItem>
                        <Text type="label" weight="semibold">
                          {item.description}
                        </Text>
                      </HStack>

                      {statusBadge && <StackItem size="static">{statusBadge}</StackItem>}
                    </HStack>

                    <Text type="supporting" color="secondary" hasTabularNumbers>
                      {item.status === "running" ? item.activeLabel : item.summaryLabel}
                    </Text>
                  </VStack>
                }
              >
                {isOpen && hasExpandableContent ? (
                  <VStack gap={3} paddingBlockStart={0.5} width="100%">
                    <Divider />

                    <VStack gap={4} width="100%">
                      {hasNestedTools && (
                        <VStack gap={0.5} width="100%">
                          <Text type="supporting" color="secondary" hasTabularNumbers>
                            {`내부 도구 호출 (${item.nestedCalls.length})`}
                          </Text>
                          <VStack gap={0} width="100%">
                            {item.nestedCalls.map((tc, idx) => (
                              <ChatToolCalls
                                key={tc.id || `${tc.name}-${idx}`}
                                calls={[toChatToolCallItem({ ...tc, subagent: null }, idx)]}
                              />
                            ))}
                          </VStack>
                        </VStack>
                      )}

                      {hasReport && (
                        <VStack gap={0.5} width="100%">
                          {showReportLabel && (
                            <Text type="supporting" color="secondary">
                              {item.role === "reviewer"
                                ? "독립 코드 리뷰 결과"
                                : "서브에이전트 리포트"}
                            </Text>
                          )}
                          <Markdown
                            density="compact"
                            headingLevelStart={4}
                            plugins={MARKDOWN_PLUGINS}
                          >
                            {item.output}
                          </Markdown>
                        </VStack>
                      )}
                    </VStack>
                  </VStack>
                ) : null}
              </Collapsible>
            </Card>
          );
        })}
      </VStack>
    </VStack>
  );
}
