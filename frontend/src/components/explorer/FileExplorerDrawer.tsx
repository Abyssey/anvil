import { useCallback, useEffect, useState } from "react";
import { Button } from "@astryxdesign/core/Button";
import { CodeBlock } from "@astryxdesign/core/CodeBlock";
import { Icon } from "@astryxdesign/core/Icon";
import { List, ListItem } from "@astryxdesign/core/List";
import { HStack, VStack } from "@astryxdesign/core/Stack";
import { Text } from "@astryxdesign/core/Text";
import { TextInput } from "@astryxdesign/core/TextInput";
import {
  ArrowLeftIcon,
  DocumentTextIcon,
  FolderIcon,
  FolderOpenIcon,
} from "@heroicons/react/24/outline";
import { apiClient } from "../../api/client";
import type { FilePreview, TreeNode } from "../../api/types";
import { useWorkspace } from "../../state/workspaceContext";
import { inferCodeLanguage } from "../../utils/codeLanguage";

interface FlatTreeItem {
  node: TreeNode;
  depth: number;
}

function mergeChildrenAtPath(
  nodes: TreeNode[],
  targetPath: string,
  children: TreeNode[],
): TreeNode[] {
  return nodes.map((node) => {
    if (node.path === targetPath) {
      return { ...node, children };
    }
    if (node.children.length > 0 && targetPath.startsWith(`${node.path}/`)) {
      return { ...node, children: mergeChildrenAtPath(node.children, targetPath, children) };
    }
    return node;
  });
}

function filterTree(nodes: TreeNode[], query: string): TreeNode[] {
  const lowered = query.trim().toLowerCase();
  if (!lowered) return nodes;
  const matched: TreeNode[] = [];
  for (const node of nodes) {
    if (node.is_dir) {
      const childMatches = filterTree(node.children, lowered);
      if (node.name.toLowerCase().includes(lowered) || childMatches.length > 0) {
        matched.push({ ...node, children: childMatches });
      }
    } else if (
      node.name.toLowerCase().includes(lowered) ||
      node.path.toLowerCase().includes(lowered)
    ) {
      matched.push(node);
    }
  }
  return matched;
}

function flattenNodes(
  nodes: TreeNode[],
  expanded: Set<string>,
  forceExpand: boolean,
  depth = 0,
): FlatTreeItem[] {
  const out: FlatTreeItem[] = [];
  for (const node of nodes) {
    out.push({ node, depth });
    if (node.is_dir && (forceExpand || expanded.has(node.path))) {
      out.push(...flattenNodes(node.children, expanded, forceExpand, depth + 1));
    }
  }
  return out;
}

export function FileExplorerDrawer() {
  const { state } = useWorkspace();
  const [nodes, setNodes] = useState<TreeNode[]>([]);
  const [expanded, setExpanded] = useState<Set<string>>(() => new Set());
  const [filterQuery, setFilterQuery] = useState("");
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
    setFilterQuery("");
    setExpanded(new Set());
    void loadTree();
  }, [loadTree]);

  useEffect(() => {
    if (state.treeRevision > 0) {
      void loadTree();
    }
  }, [state.treeRevision, loadTree]);

  const handleSelectNode = async (node: TreeNode) => {
    if (node.is_dir) {
      const willExpand = !expanded.has(node.path);
      setExpanded((prev) => {
        const next = new Set(prev);
        if (next.has(node.path)) {
          next.delete(node.path);
        } else {
          next.add(node.path);
        }
        return next;
      });
      if (willExpand && node.children.length === 0 && state.workspacePath) {
        try {
          const deeper = await apiClient.getWorkspaceTree(state.workspacePath, node.path, 2);
          setNodes((prev) => mergeChildrenAtPath(prev, node.path, deeper));
        } catch (err) {
          setError(err instanceof Error ? err.message : "Failed to load folder");
        }
      }
      return;
    }
    try {
      const fileData = await apiClient.getWorkspaceFile(state.workspacePath, node.path);
      setPreview(fileData);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to read file");
    }
  };

  if (preview) {
    return (
      <VStack gap={3} padding={3}>
        <HStack hAlign="between" vAlign="center" gap={2}>
          <Button
            label="Back to Files"
            size="sm"
            variant="ghost"
            icon={<Icon icon={ArrowLeftIcon} size="sm" />}
            onClick={() => setPreview(null)}
          />
          <Text type="supporting" color="secondary">
            {preview.path}
          </Text>
        </HStack>

        <CodeBlock
          title={preview.path}
          code={preview.is_binary ? "[Binary file omitted]" : preview.content}
          language={preview.is_binary ? "plaintext" : inferCodeLanguage(preview.path)}
          hasLineNumbers
          maxHeight={640}
          width="100%"
        />
      </VStack>
    );
  }

  const isFiltering = filterQuery.trim().length > 0;
  const filteredNodes = filterTree(nodes, filterQuery);
  const visibleItems = flattenNodes(filteredNodes, expanded, isFiltering);

  return (
    <VStack gap={3} padding={3}>
      <HStack hAlign="between" vAlign="center">
        <Text type="label" weight="semibold">
          {state.workspaceInfo?.name ? `${state.workspaceInfo.name} Files` : "Workspace Files"}
        </Text>
        <Button label="Refresh" size="sm" variant="ghost" onClick={() => void loadTree()} />
      </HStack>

      <TextInput
        label="Filter files"
        isLabelHidden
        value={filterQuery}
        onChange={setFilterQuery}
        placeholder="Filter files..."
        hasClear
      />

      {error && (
        <Text type="supporting" color="secondary">
          {error}
        </Text>
      )}

      {visibleItems.length > 0 ? (
        <List density="compact" hasDividers={false}>
          {visibleItems.map(({ node, depth }) => (
            <ListItem
              key={node.path}
              label={`${"  ".repeat(depth)}${node.name}`}
              startContent={
                <Icon
                  icon={
                    node.is_dir
                      ? expanded.has(node.path) || isFiltering
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
      ) : (
        <Text type="supporting" color="secondary">
          {isFiltering ? "No matching files." : "No files found in this folder."}
        </Text>
      )}
    </VStack>
  );
}
