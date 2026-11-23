import { useCallback, useEffect, useState } from "react";
import { AlertDialog } from "@astryxdesign/core/AlertDialog";
import { Badge } from "@astryxdesign/core/Badge";
import { Button } from "@astryxdesign/core/Button";
import { CodeBlock } from "@astryxdesign/core/CodeBlock";
import { Icon } from "@astryxdesign/core/Icon";
import { List, ListItem } from "@astryxdesign/core/List";
import { HStack, VStack } from "@astryxdesign/core/Stack";
import { Text } from "@astryxdesign/core/Text";
import { ArrowUturnLeftIcon } from "@heroicons/react/24/outline";
import type { FileDiff } from "../../api/types";
import { useWorkspace } from "../../state/workspaceContext";
import { inferCodeLanguage } from "../../utils/codeLanguage";

type DiffRevertTarget = { scope: "all" } | { scope: "file"; path: string };

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
  const activeSessionId = state.activeSession?.id ?? null;
  const [selectedPath, setSelectedPath] = useState<string | null>(null);
  const [confirmTarget, setConfirmTarget] = useState<DiffRevertTarget | null>(null);
  const [isReverting, setIsReverting] = useState(false);

  useEffect(() => {
    setSelectedPath(null);
    setConfirmTarget(null);
    setIsReverting(false);
  }, [activeSessionId, state.workspacePath]);

  useEffect(() => {
    if (state.diffFocus?.path) {
      setSelectedPath(state.diffFocus.path);
    } else if (!selectedPath && files.length > 0) {
      setSelectedPath(files[0].path);
    }
  }, [state.diffFocus, files, selectedPath]);

  const activeFile = files.find((f) => f.path === selectedPath) ?? files[0] ?? null;

  const handleConfirmRevert = useCallback(async () => {
    if (!confirmTarget || isReverting) return;
    setIsReverting(true);
    try {
      if (confirmTarget.scope === "all") {
        await actions.revertDiffFiles(undefined);
        setSelectedPath(null);
      } else {
        await actions.revertDiffFiles([confirmTarget.path]);
        if (selectedPath === confirmTarget.path) {
          setSelectedPath(null);
        }
      }
      setConfirmTarget(null);
    } catch {
      // Surfaced via state.streamError
    } finally {
      setIsReverting(false);
    }
  }, [actions, confirmTarget, isReverting, selectedPath]);

  const dialogTitle =
    confirmTarget?.scope === "all"
      ? "Discard all working tree changes?"
      : `Revert ${confirmTarget?.path ?? "file"}?`;
  const dialogDescription =
    confirmTarget?.scope === "all"
      ? `Discards uncommitted changes across all ${files.length} modified or untracked ${files.length === 1 ? "file" : "files"} in this Git repository. This cannot be undone.`
      : `Discards uncommitted changes to ${confirmTarget?.scope === "file" ? confirmTarget.path : "this file"} and restores it to HEAD (or removes it if untracked). This cannot be undone.`;

  return (
    <>
      <VStack gap={3} padding={3}>
        <HStack hAlign="between" vAlign="center">
          <Text type="label" weight="semibold">
            {`Working Tree Changes (${files.length})`}
          </Text>
          <HStack gap={1} vAlign="center">
            {files.length > 0 && (
              <Button
                label="Discard All"
                size="sm"
                variant="ghost"
                isDisabled={state.isStreaming || isReverting}
                onClick={() => setConfirmTarget({ scope: "all" })}
              />
            )}
            <Button
              label="Refresh"
              size="sm"
              variant="ghost"
              onClick={() => void actions.refreshDiff()}
            />
          </HStack>
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
                endContent={
                  <Button
                    label="Revert File"
                    size="sm"
                    variant="ghost"
                    icon={<Icon icon={ArrowUturnLeftIcon} size="sm" />}
                    isDisabled={state.isStreaming || isReverting}
                    onClick={() => setConfirmTarget({ scope: "file", path: activeFile.path })}
                  />
                }
                code={formatFileDiff(activeFile)}
                language={inferCodeLanguage(activeFile.path)}
                isDiff
                hasLineNumbers
                maxHeight={400}
                width="100%"
              />
            )}
          </>
        )}
      </VStack>
      <AlertDialog
        isOpen={confirmTarget !== null}
        onOpenChange={(open) => {
          if (!open && !isReverting) {
            setConfirmTarget(null);
          }
        }}
        title={dialogTitle}
        description={dialogDescription}
        cancelLabel="Cancel"
        actionLabel={confirmTarget?.scope === "all" ? "Discard All" : "Revert File"}
        actionVariant="destructive"
        isActionLoading={isReverting}
        onAction={() => void handleConfirmRevert()}
      />
    </>
  );
}
