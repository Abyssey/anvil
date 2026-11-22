import { useCallback, useEffect, useState } from "react";
import { Button } from "@astryxdesign/core/Button";
import { CodeBlock } from "@astryxdesign/core/CodeBlock";
import { Icon } from "@astryxdesign/core/Icon";
import { List, ListItem } from "@astryxdesign/core/List";
import { HStack, VStack } from "@astryxdesign/core/Stack";
import { Text } from "@astryxdesign/core/Text";
import { DocumentTextIcon, FolderIcon, FolderOpenIcon } from "@heroicons/react/24/outline";
import { apiClient } from "../../api/client";
import type { FilePreview, TreeNode } from "../../api/types";
import { useWorkspace } from "../../state/workspaceContext";

interface FlatTreeItem {
  node: TreeNode;
  depth: number;
}

function flattenNodes(
  nodes: TreeNode[],
  expanded: Set<string>,
  depth = 0,
): FlatTreeItem[] {
  const out: FlatTreeItem[] = [];
  for (const node of nodes) {
    out.push({ node, depth });
    if (node.is_dir && expanded.has(node.path)) {
      out.push(...flattenNodes(node.children, expanded, depth + 1));
    }
  }
  return out;
}

export function FileExplorerDrawer() {
  const { state } = useWorkspace();
  const [nodes, setNodes] = useState<TreeNode[]>([]);
  const [expanded, setExpanded] = useState<Set<string>>(() => new Set());
  const [preview, setPreview] = useState<FilePreview | null>(null);
  const [error, setError] = useState<string | null>(null);

  const loadTree = useCallback(async () => {
    if (!state.workspacePath) return;
    setError(null);
    try {
      const tree = await apiClient.getWorkspaceTree(state.workspacePath, "", 3);
      setNodes(tree);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to load files");
    }
  }, [state.workspacePath]);

  useEffect(() => {
    setPreview(null);
    void loadTree();
  }, [loadTree]);

  const handleSelectNode = async (node: TreeNode) => {
    if (node.is_dir) {
      setExpanded((prev) => {
        const next = new Set(prev);
        if (next.has(node.path)) {
          next.delete(node.path);
        } else {
          next.add(node.path);
        }
        return next;
      });
      return;
    }
    try {
      const fileData = await apiClient.getWorkspaceFile(state.workspacePath, node.path);
      setPreview(fileData);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to read file");
    }
  };

  const visibleItems = flattenNodes(nodes, expanded);

  return (
    <VStack gap={3} padding={3}>
      <HStack hAlign="between" vAlign="center">
        <Text type="label" weight="semibold">
          Workspace Files
        </Text>
        <Button label="Refresh" size="sm" variant="ghost" onClick={() => void loadTree()} />
      </HStack>

      {error && (
        <Text type="supporting" color="secondary">
          {error}
        </Text>
      )}

      <List density="compact" hasDividers={false}>
        {visibleItems.map(({ node, depth }) => (
          <ListItem
            key={node.path}
            label={`${"  ".repeat(depth)}${node.name}`}
            isSelected={preview?.path === node.path}
            startContent={
              <Icon
                icon={
                  node.is_dir
                    ? expanded.has(node.path)
                      ? FolderOpenIcon
                      : FolderIcon
                    : DocumentTextIcon
                }
                size="sm"
                color={node.is_dir ? "accent" : "secondary"}
              />
            }
            onClick={() => void handleSelectNode(node)}
          />
        ))}
      </List>

      {preview && (
        <CodeBlock
          title={preview.path}
          code={preview.is_binary ? "[Binary file omitted]" : preview.content}
          hasLineNumbers
          maxHeight={340}
          width="100%"
        />
      )}
    </VStack>
  );
}
