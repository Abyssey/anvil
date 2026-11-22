import { Banner } from "@astryxdesign/core/Banner";
import { Button } from "@astryxdesign/core/Button";
import { CodeBlock } from "@astryxdesign/core/CodeBlock";
import { HStack, VStack } from "@astryxdesign/core/Stack";
import { Text } from "@astryxdesign/core/Text";
import { useWorkspace } from "../../state/workspaceContext";

export function ShellApprovalCard() {
  const { state, actions } = useWorkspace();
  const pending = state.activeSession?.pending_interrupt;
  if (!pending || pending.kind !== "shell_approval") {
    return null;
  }

  return (
    <Banner
      status="warning"
      collapsible={false}
      title="Shell command approval required"
      description={pending.reason ?? "Confirm before running this command on the host."}
    >
      <VStack gap={3}>
        <CodeBlock
          code={pending.command ?? ""}
          language="bash"
          width="100%"
          hasCopyButton={false}
        />
        <Text type="supporting" color="secondary">
          {`Workspace: ${pending.workspace ?? state.workspacePath}`}
        </Text>
        <HStack gap={2} hAlign="end">
          <Button
            label="Deny"
            variant="destructive"
            size="sm"
            isDisabled={state.isStreaming}
            onClick={() => void actions.resumeInterrupt("deny")}
          />
          <Button
            label="Approve"
            variant="primary"
            size="sm"
            isDisabled={state.isStreaming}
            onClick={() => void actions.resumeInterrupt("approve")}
          />
        </HStack>
      </VStack>
    </Banner>
  );
}
