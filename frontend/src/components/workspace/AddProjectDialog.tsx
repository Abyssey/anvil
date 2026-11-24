import { useCallback, useEffect, useMemo, useState } from "react";
import { Badge } from "@astryxdesign/core/Badge";
import { Banner } from "@astryxdesign/core/Banner";
import { Button } from "@astryxdesign/core/Button";
import { Dialog, DialogHeader } from "@astryxdesign/core/Dialog";
import { Icon } from "@astryxdesign/core/Icon";
import { Layout, LayoutContent, LayoutFooter } from "@astryxdesign/core/Layout";
import { List, ListItem } from "@astryxdesign/core/List";
import { HStack, StackItem, VStack } from "@astryxdesign/core/Stack";
import { Text } from "@astryxdesign/core/Text";
import { TextInput } from "@astryxdesign/core/TextInput";
import { ArrowUpIcon, FolderIcon, FolderPlusIcon } from "@heroicons/react/24/outline";
import { ApiError, apiClient } from "../../api/client";
import type { DirectoryBrowseResponse } from "../../api/types";
import { useWorkspace } from "../../state/workspaceContext";

function normalizeFolderPath(raw: string): string {
  const trimmed = raw.trim();
  if (trimmed === "/") return "/";
  return trimmed.replace(/\/+$/, "");
}

function getParentDirectoryPath(raw: string): string | undefined {
  const normalized = normalizeFolderPath(raw);
  if (!normalized || normalized === "/") return undefined;
  const lastSlash = normalized.lastIndexOf("/");
  if (lastSlash < 0) return undefined;
  if (lastSlash === 0) return "/";
  return normalized.slice(0, lastSlash);
}

function splitParentAndChildName(
  raw: string,
): { parentPath: string; name: string; fullPath: string } | null {
  const normalized = normalizeFolderPath(raw);
  if (!normalized || normalized === "/") return null;
  const lastSlash = normalized.lastIndexOf("/");
  if (lastSlash < 0) return null;
  const parentPath = lastSlash === 0 ? "/" : normalized.slice(0, lastSlash);
  const name = normalized.slice(lastSlash + 1).trim();
  if (!name || name === "." || name === ".." || name.includes("/") || name.includes("\\")) {
    return null;
  }
  return { parentPath, name, fullPath: normalized };
}

export function AddProjectDialog() {
  const { state, actions } = useWorkspace();
  const [pathInput, setPathInput] = useState("");
  const [browseData, setBrowseData] = useState<DirectoryBrowseResponse | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [missingTarget, setMissingTarget] = useState<{
    parentPath: string;
    name: string;
    fullPath: string;
  } | null>(null);
  const [isLoading, setIsLoading] = useState(false);
  const [isSubmitting, setIsSubmitting] = useState(false);

  const [isCreatingFolder, setIsCreatingFolder] = useState(false);
  const [newFolderName, setNewFolderName] = useState("");
  const [folderError, setFolderError] = useState<string | null>(null);
  const [isCreatingSubmitting, setIsCreatingSubmitting] = useState(false);

  const resetFolderCreator = useCallback(() => {
    setIsCreatingFolder(false);
    setNewFolderName("");
    setFolderError(null);
  }, []);

  const loadDirectory = useCallback(
    async (targetPath?: string) => {
      setIsLoading(true);
      setError(null);
      setMissingTarget(null);
      resetFolderCreator();
      try {
        const data = await apiClient.browseDirectories(targetPath);
        setBrowseData(data);
        setPathInput(data.current_path);
      } catch (err) {
        if (err instanceof ApiError && err.code === "INVALID_WORKSPACE" && targetPath) {
          const split = splitParentAndChildName(targetPath);
          if (split) {
            setMissingTarget(split);
            setIsLoading(false);
            return;
          }
        }
        setError(err instanceof Error ? err.message : "Unable to browse directory");
      } finally {
        setIsLoading(false);
      }
    },
    [resetFolderCreator],
  );

  useEffect(() => {
    if (state.addProjectOpen) {
      const parentPath = getParentDirectoryPath(state.workspacePath);
      void loadDirectory(parentPath);
    } else {
      resetFolderCreator();
      setMissingTarget(null);
      setError(null);
    }
  }, [state.addProjectOpen, state.workspacePath, loadDirectory, resetFolderCreator]);

  const registeredPaths = useMemo(
    () => new Set(state.projects.map((p) => normalizeFolderPath(p.path))),
    [state.projects],
  );

  const effectiveTarget = normalizeFolderPath(pathInput || browseData?.current_path || "");
  const existingProject = useMemo(
    () => state.projects.find((p) => normalizeFolderPath(p.path) === effectiveTarget) ?? null,
    [state.projects, effectiveTarget],
  );

  const handleOpenPath = () => {
    const trimmed = pathInput.trim();
    if (!trimmed) return;
    void loadDirectory(trimmed);
  };

  const handleCreateFolder = async () => {
    if (!browseData) return;
    const trimmed = newFolderName.trim();
    if (!trimmed) {
      setFolderError("Enter a folder name.");
      return;
    }
    if (trimmed === "." || trimmed === ".." || trimmed.includes("/") || trimmed.includes("\\")) {
      setFolderError("Folder name cannot contain path separators or '..'.");
      return;
    }
    setIsCreatingSubmitting(true);
    setFolderError(null);
    setError(null);
    try {
      const created = await apiClient.createDirectory(browseData.current_path, trimmed);
      resetFolderCreator();
      await loadDirectory(created.path);
    } catch (err) {
      setFolderError(err instanceof Error ? err.message : "Unable to create folder");
    } finally {
      setIsCreatingSubmitting(false);
    }
  };

  const handleCreateMissingTarget = async () => {
    if (!missingTarget) return;
    setIsCreatingSubmitting(true);
    setError(null);
    try {
      const created = await apiClient.createDirectory(missingTarget.parentPath, missingTarget.name);
      setMissingTarget(null);
      await loadDirectory(created.path);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Unable to create folder");
    } finally {
      setIsCreatingSubmitting(false);
    }
  };

  const handleConfirm = async () => {
    const target = pathInput.trim() || browseData?.current_path || "";
    if (!target) return;
    setIsSubmitting(true);
    setError(null);
    setMissingTarget(null);
    try {
      if (existingProject) {
        await actions.selectProject(existingProject.id);
      } else {
        await actions.addProject(target);
      }
      actions.closeAddProject();
    } catch (err) {
      if (err instanceof ApiError && err.code === "INVALID_WORKSPACE") {
        const split = splitParentAndChildName(target);
        if (split) {
          setMissingTarget(split);
          setIsSubmitting(false);
          return;
        }
      }
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
      width={600}
    >
      <Layout
        height="auto"
        header={
          <DialogHeader
            title="Add Project"
            subtitle="Paste a local folder path, browse directories, or create a new folder."
            onOpenChange={(open) => {
              if (!open) actions.closeAddProject();
            }}
          />
        }
        content={
          <LayoutContent padding={4}>
            <VStack gap={3}>
              <HStack gap={2} vAlign="end" width="100%">
                <StackItem size="fill">
                  <TextInput
                    label="Folder path"
                    width="100%"
                    value={pathInput}
                    onChange={(val) => {
                      setPathInput(val);
                      if (missingTarget) setMissingTarget(null);
                    }}
                    onEnter={handleOpenPath}
                    placeholder="/path/to/repository"
                    hasAutoFocus
                  />
                </StackItem>
                <StackItem size="static">
                  <Button
                    label="Open"
                    variant="secondary"
                    size="md"
                    isLoading={isLoading}
                    onClick={handleOpenPath}
                  />
                </StackItem>
              </HStack>

              {browseData && (
                <HStack hAlign="between" vAlign="center" gap={2} wrap="wrap">
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
                  <HStack gap={2} vAlign="center">
                    {existingProject && <Badge label="Already added" variant="warning" />}
                    {browseData.is_git_repo && (
                      <Badge label={`git: ${browseData.branch || "repo"}`} variant="info" />
                    )}
                    <Button
                      label="New Folder"
                      variant="secondary"
                      size="sm"
                      icon={<Icon icon={FolderPlusIcon} size="sm" />}
                      isDisabled={isLoading || isCreatingFolder}
                      onClick={() => {
                        setError(null);
                        setFolderError(null);
                        setIsCreatingFolder(true);
                      }}
                    />
                  </HStack>
                </HStack>
              )}

              {isCreatingFolder && browseData && (
                <HStack gap={2} vAlign="start" width="100%">
                  <StackItem size="fill">
                    <TextInput
                      label="New folder name"
                      isLabelHidden
                      width="100%"
                      size="sm"
                      value={newFolderName}
                      onChange={(val) => {
                        setNewFolderName(val);
                        if (folderError) setFolderError(null);
                      }}
                      onEnter={() => void handleCreateFolder()}
                      placeholder="new-project-folder"
                      hasAutoFocus
                      autoComplete="off"
                      hasSpellCheck={false}
                      status={folderError ? { type: "error", message: folderError } : undefined}
                      statusVariant="detached"
                    />
                  </StackItem>
                  <StackItem size="static">
                    <Button
                      label="Create"
                      variant="primary"
                      size="sm"
                      isLoading={isCreatingSubmitting}
                      onClick={() => void handleCreateFolder()}
                    />
                  </StackItem>
                  <StackItem size="static">
                    <Button
                      label="Cancel"
                      variant="ghost"
                      size="sm"
                      isDisabled={isCreatingSubmitting}
                      onClick={resetFolderCreator}
                    />
                  </StackItem>
                </HStack>
              )}

              {missingTarget && (
                <Banner
                  status="warning"
                  collapsible={false}
                  title={`Folder "${missingTarget.name}" does not exist yet.`}
                  description={`Create it inside ${missingTarget.parentPath} and select it as the project path.`}
                  endContent={
                    <Button
                      label="Create Folder"
                      variant="secondary"
                      size="sm"
                      icon={<Icon icon={FolderPlusIcon} size="sm" />}
                      isLoading={isCreatingSubmitting}
                      onClick={() => void handleCreateMissingTarget()}
                    />
                  }
                />
              )}

              {error && <Banner status="error" collapsible={false} title={error} />}

              <VStack height={240} isScrollable>
                {browseData && browseData.directories.length > 0 ? (
                  <List density="compact" hasDividers={false}>
                    {browseData.directories.map((dir) => {
                      const isAdded = registeredPaths.has(normalizeFolderPath(dir.path));
                      return (
                        <ListItem
                          key={dir.path}
                          label={dir.name}
                          startContent={<Icon icon={FolderIcon} size="sm" color="accent" />}
                          endContent={
                            isAdded || dir.is_git_repo ? (
                              <HStack gap={1} vAlign="center">
                                {isAdded && <Badge label="Added" variant="neutral" />}
                                {dir.is_git_repo && <Badge label="git" variant="neutral" />}
                              </HStack>
                            ) : undefined
                          }
                          onClick={() => void loadDirectory(dir.path)}
                        />
                      );
                    })}
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
                label={existingProject ? "Open Existing Project" : "Add Project"}
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
