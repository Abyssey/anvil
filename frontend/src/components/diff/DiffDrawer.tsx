import { useEffect, useState } from "react";
import { Badge } from "@astryxdesign/core/Badge";
import { Button } from "@astryxdesign/core/Button";
import { CodeBlock } from "@astryxdesign/core/CodeBlock";
import { List, ListItem } from "@astryxdesign/core/List";
import { HStack, VStack } from "@astryxdesign/core/Stack";
import { Text } from "@astryxdesign/core/Text";
import type { FileDiff } from "../../api/types";
import { useWorkspace } from "../../state/workspaceContext";

function formatFileDiff(file: FileDiff): string {
  if (file.status === "untracked") {
    const lines = (file.preview_content ?? "").split("\n");
    return [`--- /dev/null`, `+++ b/${file.path}`, `@@ -0,0 +1,${lines.length} @@`, ...lines.map((l) => `+${l}`)].join(
      "\n",
    );
  }
  const chunks: string[] = [`--- a/${file.path}`, `+++ b/${file.path}`];
  for (const hunk of file.hunks) {
    chunks.push(hunk.header);
    chunks.push(...hunk.lines);
  }
  return chunks.join("\n");
}

export function DiffDrawer() {
  const { state, actions } = useWorkspace();
  const files = state.diff?.files ?? [];
  const [selectedPath, setSelectedPath] = useState<string | null>(null);

  useEffect(() => {
    if (state.diffFocus?.path) {
      setSelectedPath(state.diffFocus.path);
    } else if (!selectedPath && files.length > 0) {
      setSelectedPath(files[0].path);
    }
  }, [state.diffFocus, files, selectedPath]);

  const activeFile = files.find((f) => f.path === selectedPath) ?? files[0] ?? null;

  return (
    <VStack gap={3} padding={3}>
      <HStack hAlign="between" vAlign="center">
        <Text type="label" weight="semibold">
          {`Working Tree Changes (${files.length})`}
        </Text>
        <Button label="Refresh" size="sm" variant="ghost" onClick={() => void actions.refreshDiff()} />
      </HStack>

      {files.length === 0 ? (
        <Text color="secondary">Working tree is clean.</Text>
      ) : (
        <>
          <List density="compact" hasDividers={false}>
            {files.map((file) => (
              <ListItem
                key={file.path}
                label={file.path}
                isSelected={activeFile?.path === file.path}
                endContent={
                  <Badge
                    label={file.status}
                    variant={file.status === "deleted" ? "error" : "info"}
                  />
                }
                onClick={() => setSelectedPath(file.path)}
              />
            ))}
          </List>

          {activeFile && (
            <CodeBlock
              title={
                state.diffFocus?.path === activeFile.path && state.diffFocus.hunkName
                  ? `${activeFile.path} (${state.diffFocus.hunkName})`
                  : activeFile.path
              }
              code={formatFileDiff(activeFile)}
              isDiff
              hasLineNumbers
              maxHeight={400}
              width="100%"
            />
          )}
        </>
      )}
    </VStack>
  );
}
