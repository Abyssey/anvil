import { useCallback, useEffect, useState } from "react";
import { Badge } from "@astryxdesign/core/Badge";
import { Banner } from "@astryxdesign/core/Banner";
import { Button } from "@astryxdesign/core/Button";
import { Dialog, DialogHeader } from "@astryxdesign/core/Dialog";
import { Icon } from "@astryxdesign/core/Icon";
import { Layout, LayoutContent, LayoutFooter } from "@astryxdesign/core/Layout";
import { List, ListItem } from "@astryxdesign/core/List";
import { HStack, VStack } from "@astryxdesign/core/Stack";
import { Text } from "@astryxdesign/core/Text";
import { TextInput } from "@astryxdesign/core/TextInput";
import { ArrowUpIcon, FolderIcon } from "@heroicons/react/24/outline";
import { apiClient } from "../../api/client";
import type { DirectoryBrowseResponse } from "../../api/types";
import { useWorkspace } from "../../state/workspaceContext";

export function AddProjectDialog() {
  const { state, actions } = useWorkspace();
  const [pathInput, setPathInput] = useState("");
  const [browseData, setBrowseData] = useState<DirectoryBrowseResponse | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [isLoading, setIsLoading] = useState(false);
  const [isSubmitting, setIsSubmitting] = useState(false);

  const loadDirectory = useCallback(async (targetPath?: string) => {
    setIsLoading(true);
    setError(null);
    try {
      const data = await apiClient.browseDirectories(targetPath);
      setBrowseData(data);
      setPathInput(data.current_path);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Unable to browse directory");
    } finally {
      setIsLoading(false);
    }
  }, []);

  useEffect(() => {
    if (state.addProjectOpen) {
      void loadDirectory(state.workspacePath || undefined);
    }
  }, [state.addProjectOpen, state.workspacePath, loadDirectory]);

  const handleOpenPath = () => {
    const trimmed = pathInput.trim();
    if (!trimmed) return;
    void loadDirectory(trimmed);
  };

  const handleConfirm = async () => {
    const target = pathInput.trim() || browseData?.current_path || "";
    if (!target) return;
    setIsSubmitting(true);
    setError(null);
    try {
      await actions.addProject(target);
      actions.closeAddProject();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Unable to add project");
    } finally {
      setIsSubmitting(false);
    }
  };

  return (
    <Dialog
      isOpen={state.addProjectOpen}
      onOpenChange={(open) => {
        if (!open) actions.closeAddProject();
      }}
      purpose="form"
      width={540}
    >
      <Layout
        height="auto"
        header={
          <DialogHeader
            title="Add Project"
            subtitle="Paste a local folder path or browse directories on this machine."
            onOpenChange={(open) => {
              if (!open) actions.closeAddProject();
            }}
          />
        }
        content={
          <LayoutContent padding={4}>
            <VStack gap={3}>
              <HStack gap={2} vAlign="end">
                <TextInput
                  label="Folder path"
                  value={pathInput}
                  onChange={setPathInput}
                  onEnter={handleOpenPath}
                  placeholder="/path/to/repository"
                  hasAutoFocus
                />
                <Button
                  label="Open"
                  variant="secondary"
                  size="md"
                  isLoading={isLoading}
                  onClick={handleOpenPath}
                />
              </HStack>

              {browseData && (
                <HStack hAlign="between" vAlign="center" gap={2}>
                  <HStack gap={2} vAlign="center">
                    <Button
                      label="Up"
                      variant="ghost"
                      size="sm"
                      icon={<Icon icon={ArrowUpIcon} size="sm" />}
                      isDisabled={!browseData.parent_path || isLoading}
                      onClick={() => {
                        if (browseData.parent_path) {
                          void loadDirectory(browseData.parent_path);
                        }
                      }}
                    />
                    <Text type="supporting" color="secondary">
                      {browseData.current_path}
                    </Text>
                  </HStack>
                  {browseData.is_git_repo && (
                    <Badge label={`git: ${browseData.branch || "repo"}`} variant="info" />
                  )}
                </HStack>
              )}

              {error && <Banner status="error" title={error} />}

              <VStack height={240} isScrollable>
                {browseData && browseData.directories.length > 0 ? (
                  <List density="compact" hasDividers={false}>
                    {browseData.directories.map((dir) => (
                      <ListItem
                        key={dir.path}
                        label={dir.name}
                        startContent={<Icon icon={FolderIcon} size="sm" color="accent" />}
                        endContent={
                          dir.is_git_repo ? <Badge label="git" variant="neutral" /> : undefined
                        }
                        onClick={() => void loadDirectory(dir.path)}
                      />
                    ))}
                  </List>
                ) : (
                  <Text type="supporting" color="secondary">
                    {isLoading ? "Loading folders..." : "No subdirectories in this folder."}
                  </Text>
                )}
              </VStack>
            </VStack>
          </LayoutContent>
        }
        footer={
          <LayoutFooter hasDivider>
            <HStack hAlign="end" gap={2} width="100%">
              <Button label="Cancel" variant="secondary" onClick={actions.closeAddProject} />
              <Button
                label="Add Folder"
                variant="primary"
                isLoading={isSubmitting}
                isDisabled={!pathInput.trim()}
                onClick={() => void handleConfirm()}
              />
            </HStack>
          </LayoutFooter>
        }
      />
    </Dialog>
  );
}
