import { useEffect, useMemo, useState } from "react";
import { Badge } from "@astryxdesign/core/Badge";
import { Banner } from "@astryxdesign/core/Banner";
import { Button } from "@astryxdesign/core/Button";
import { Dialog, DialogHeader } from "@astryxdesign/core/Dialog";
import { Layout, LayoutContent, LayoutFooter } from "@astryxdesign/core/Layout";
import { HStack, VStack } from "@astryxdesign/core/Stack";
import { Text } from "@astryxdesign/core/Text";
import { TextArea } from "@astryxdesign/core/TextArea";
import { apiClient } from "../../api/client";
import type { ProjectMemory } from "../../api/types";
import { useWorkspace } from "../../state/workspaceContext";

const DEFAULT_MAX_BYTES = 32 * 1024;
const RECOMMENDED_MAX_LINES = 200;

function computeUtf8Bytes(text: string): number {
  return new TextEncoder().encode(text).length;
}

function computeLineCount(text: string): number {
  if (!text) return 0;
  return text.split(/\r?\n/).length;
}

function formatByteSize(bytes: number): string {
  if (bytes >= 1024) {
    return `${(bytes / 1024).toFixed(1)} KB`;
  }
  return `${bytes} B`;
}

export function ProjectMemoryDialog() {
  const { state, actions } = useWorkspace();
  const [memory, setMemory] = useState<ProjectMemory | null>(null);
  const [draft, setDraft] = useState("");
  const [isLoading, setIsLoading] = useState(false);
  const [isSaving, setIsSaving] = useState(false);
  const [isClearing, setIsClearing] = useState(false);
  const [confirmClear, setConfirmClear] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);

  const activeProject = useMemo(
    () =>
      state.projects.find((p) => p.id === state.activeProjectId) ??
      state.projects.find((p) => p.path === state.workspacePath) ??
      null,
    [state.projects, state.activeProjectId, state.workspacePath],
  );

  const projectId = activeProject?.id ?? state.activeProjectId;
  const projectName = activeProject?.name ?? state.workspaceInfo?.name ?? "Active Project";

  useEffect(() => {
    if (!state.memoryOpen) {
      setConfirmClear(false);
      setError(null);
      setNotice(null);
      return;
    }
    if (!projectId) {
      setMemory(null);
      setDraft("");
      return;
    }
    let cancelled = false;
    setIsLoading(true);
    setError(null);
    setNotice(null);
    setConfirmClear(false);

    void (async () => {
      try {
        const res = await apiClient.getProjectMemory(projectId);
        if (cancelled) return;
        setMemory(res);
        setDraft(res.content);
      } catch (err) {
        if (cancelled) return;
        setError(err instanceof Error ? err.message : "Failed to load project memory");
      } finally {
        if (!cancelled) {
          setIsLoading(false);
        }
      }
    })();

    return () => {
      cancelled = true;
    };
  }, [state.memoryOpen, projectId]);

  const maxBytes = memory?.max_bytes ?? DEFAULT_MAX_BYTES;
  const byteSize = useMemo(() => computeUtf8Bytes(draft), [draft]);
  const lineCount = useMemo(() => computeLineCount(draft), [draft]);
  const isOverBytes = byteSize > maxBytes;
  const isOverLines = lineCount > RECOMMENDED_MAX_LINES;
  const isDirty = draft !== (memory?.content ?? "");

  const handleSave = async () => {
    if (!projectId || isOverBytes) return;
    setIsSaving(true);
    setError(null);
    setNotice(null);
    try {
      const saved = await apiClient.updateProjectMemory(projectId, draft);
      setMemory(saved);
      setDraft(saved.content);
      setNotice("Project memory saved.");
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to save project memory");
    } finally {
      setIsSaving(false);
    }
  };

  const handleConfirmClear = async () => {
    if (!projectId) return;
    setIsClearing(true);
    setError(null);
    setNotice(null);
    try {
      await apiClient.clearProjectMemory(projectId);
      setMemory((prev) =>
        prev
          ? { ...prev, content: "", updated_at: null }
          : {
              project_id: projectId,
              content: "",
              updated_at: null,
              has_repo_agents_md: false,
              max_bytes: DEFAULT_MAX_BYTES,
            },
      );
      setDraft("");
      setConfirmClear(false);
      setNotice("Project memory cleared.");
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to clear project memory");
    } finally {
      setIsClearing(false);
    }
  };

  const textAreaStatus = isOverBytes
    ? {
        type: "error" as const,
        message: `Memory exceeds the ${formatByteSize(maxBytes)} limit (${formatByteSize(byteSize)}).`,
      }
    : isOverLines
      ? {
          type: "warning" as const,
          message: `Keep memory concise (recommended <= ${RECOMMENDED_MAX_LINES} lines; currently ${lineCount}).`,
        }
      : undefined;

  return (
    <Dialog
      isOpen={state.memoryOpen}
      onOpenChange={(open) => {
        if (!open) actions.closeMemory();
      }}
      purpose="form"
      width={620}
    >
      <Layout
        height="auto"
        header={
          <DialogHeader
            title={`Project Memory · ${projectName}`}
            subtitle="Durable per-project instructions injected at /memories/project/AGENTS.md on every turn."
            onOpenChange={(open) => {
              if (!open) actions.closeMemory();
            }}
          />
        }
        content={
          <LayoutContent padding={4}>
            <VStack gap={3}>
              <HStack hAlign="between" vAlign="center" gap={2} wrap="wrap">
                <HStack gap={1.5} vAlign="center" wrap="wrap">
                  <Badge label="Project-scoped" variant="info" />
                  <Badge
                    label={
                      memory?.has_repo_agents_md
                        ? "Repo AGENTS.md detected"
                        : "No repo AGENTS.md"
                    }
                    variant={memory?.has_repo_agents_md ? "success" : "neutral"}
                  />
                </HStack>

                <Text type="supporting" color="secondary">
                  {formatByteSize(byteSize)} / {formatByteSize(maxBytes)} · {lineCount} lines
                </Text>
              </HStack>

              {error && <Banner status="error" title={error} />}
              {notice && <Banner status="success" title={notice} />}

              <TextArea
                label="Project memory (/memories/project/AGENTS.md)"
                description="Isolated to this project only. Keep concise (<= 200 lines, max 32 KB) and never store secrets."
                value={draft}
                onChange={setDraft}
                rows={12}
                width="100%"
                isLoading={isLoading}
                isDisabled={!projectId || isLoading}
                placeholder="# Project conventions, architecture notes, and recurring commands..."
                status={textAreaStatus}
                hasAutoFocus
              />
            </VStack>
          </LayoutContent>
        }
        footer={
          <LayoutFooter hasDivider>
            <HStack hAlign="between" vAlign="center" width="100%" gap={2}>
              {confirmClear ? (
                <HStack gap={1.5} vAlign="center">
                  <Button
                    label="Confirm Clear"
                    variant="destructive"
                    size="sm"
                    isLoading={isClearing}
                    onClick={() => void handleConfirmClear()}
                  />
                  <Button
                    label="Cancel"
                    variant="ghost"
                    size="sm"
                    isDisabled={isClearing}
                    onClick={() => setConfirmClear(false)}
                  />
                </HStack>
              ) : (
                <Button
                  label="Clear Memory"
                  variant="ghost"
                  size="sm"
                  isDisabled={!projectId || isLoading || (!draft && !memory?.content)}
                  onClick={() => setConfirmClear(true)}
                />
              )}

              <HStack gap={2} vAlign="center">
                <Button label="Close" variant="secondary" onClick={actions.closeMemory} />
                <Button
                  label="Save"
                  variant="primary"
                  isLoading={isSaving}
                  isDisabled={!projectId || isLoading || isOverBytes || !isDirty}
                  onClick={() => void handleSave()}
                />
              </HStack>
            </HStack>
          </LayoutFooter>
        }
      />
    </Dialog>
  );
}
