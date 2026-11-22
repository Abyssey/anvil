import { useCallback, useEffect, useId, useMemo, useState } from "react";
import { Badge, type BadgeVariant } from "@astryxdesign/core/Badge";
import { Banner } from "@astryxdesign/core/Banner";
import { Button } from "@astryxdesign/core/Button";
import { Card } from "@astryxdesign/core/Card";
import { CodeBlock } from "@astryxdesign/core/CodeBlock";
import { Dialog, DialogHeader } from "@astryxdesign/core/Dialog";
import { Divider } from "@astryxdesign/core/Divider";
import { EmptyState } from "@astryxdesign/core/EmptyState";
import { Heading } from "@astryxdesign/core/Heading";
import { Icon } from "@astryxdesign/core/Icon";
import { Layout, LayoutContent, LayoutFooter } from "@astryxdesign/core/Layout";
import { SegmentedControl, SegmentedControlItem } from "@astryxdesign/core/SegmentedControl";
import { HStack, VStack } from "@astryxdesign/core/Stack";
import { Tab, TabList } from "@astryxdesign/core/TabList";
import { Text } from "@astryxdesign/core/Text";
import { TextInput } from "@astryxdesign/core/TextInput";
import {
  ArrowDownTrayIcon,
  BookmarkIcon,
  EyeIcon,
  MagnifyingGlassIcon,
  SparklesIcon,
  TrashIcon,
} from "@heroicons/react/24/outline";
import { apiClient } from "../../api/client";
import type {
  CatalogTab,
  InstalledSkill,
  RemoteSkillSummary,
  SkillCatalogPage,
  SkillScope,
} from "../../api/types";
import { useWorkspace } from "../../state/workspaceContext";

type MainSkillsTab = "installed" | "discover";
type InstallTargetScope = "global" | "workspace";

interface PreviewState {
  title: string;
  subtitle: string;
  content: string;
  files: string[];
}

function scopeBadgeVariant(scope: SkillScope): BadgeVariant {
  if (scope === "workspace") return "success";
  if (scope === "global") return "info";
  return "neutral";
}

function formatInstalls(count: number): string {
  if (count >= 1000) {
    return `${(count / 1000).toFixed(1)}k`;
  }
  return String(count);
}

function parseDirectSource(raw: string): { source: string; skillId?: string } {
  const trimmed = raw.trim();
  const atIdx = trimmed.indexOf("@");
  if (atIdx > 0) {
    return {
      source: trimmed.slice(0, atIdx).trim(),
      skillId: trimmed.slice(atIdx + 1).trim() || undefined,
    };
  }
  return { source: trimmed };
}

export function SkillsDialog() {
  const { state, actions } = useWorkspace();
  const installedPanelId = useId();
  const discoverPanelId = useId();
  const [mainTab, setMainTab] = useState<MainSkillsTab>("installed");
  const [catalogTab, setCatalogTab] = useState<CatalogTab>("all-time");
  const [targetScope, setTargetScope] = useState<InstallTargetScope>("global");
  const [searchQuery, setSearchQuery] = useState("");
  const [directSource, setDirectSource] = useState("");

  const [catalogPage, setCatalogPage] = useState<SkillCatalogPage | null>(null);
  const [searchResults, setSearchResults] = useState<RemoteSkillSummary[] | null>(null);
  const [isLoadingDiscover, setIsLoadingDiscover] = useState(false);

  const [preview, setPreview] = useState<PreviewState | null>(null);
  const [isLoadingPreview, setIsLoadingPreview] = useState(false);

  const [pendingDeleteKey, setPendingDeleteKey] = useState<string | null>(null);
  const [deletingKey, setDeletingKey] = useState<string | null>(null);
  const [installingKey, setInstallingKey] = useState<string | null>(null);

  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);

  const { refreshInstalledSkills } = actions;

  useEffect(() => {
    if (state.skillsOpen) {
      setError(null);
      setNotice(null);
      setPendingDeleteKey(null);
      void refreshInstalledSkills();
    } else {
      setPreview(null);
      setPendingDeleteKey(null);
    }
  }, [state.skillsOpen, refreshInstalledSkills]);

  const loadCatalog = useCallback(
    async (tab: CatalogTab, page = 0, append = false) => {
      setIsLoadingDiscover(true);
      setError(null);
      try {
        const res = await apiClient.browseSkillsCatalog(
          tab,
          page,
          state.workspacePath || undefined,
        );
        setCatalogPage((prev) =>
          append && prev && prev.tab === tab
            ? { ...res, skills: [...prev.skills, ...res.skills] }
            : res,
        );
      } catch (err) {
        setError(err instanceof Error ? err.message : "Failed to load skills.sh catalog");
      } finally {
        setIsLoadingDiscover(false);
      }
    },
    [state.workspacePath],
  );

  const runSearch = useCallback(
    async (query: string) => {
      const trimmed = query.trim();
      if (trimmed.length < 2) {
        setSearchResults(null);
        return;
      }
      setIsLoadingDiscover(true);
      setError(null);
      try {
        const res = await apiClient.searchSkillsCatalog(
          trimmed,
          20,
          state.workspacePath || undefined,
        );
        setSearchResults(res);
      } catch (err) {
        setError(err instanceof Error ? err.message : "Failed to search skills.sh");
      } finally {
        setIsLoadingDiscover(false);
      }
    },
    [state.workspacePath],
  );

  useEffect(() => {
    if (!state.skillsOpen || mainTab !== "discover") return;
    const trimmed = searchQuery.trim();
    if (trimmed.length >= 2) {
      const timer = window.setTimeout(() => {
        void runSearch(trimmed);
      }, 150);
      return () => window.clearTimeout(timer);
    }
    setSearchResults(null);
    void loadCatalog(catalogTab, 0, false);
    return undefined;
  }, [state.skillsOpen, mainTab, catalogTab, searchQuery, loadCatalog, runSearch]);

  const installedKeysByScope = useMemo(() => {
    const map = new Map<InstallTargetScope, Set<string>>();
    map.set("global", new Set());
    map.set("workspace", new Set());
    for (const skill of state.installedSkills) {
      if (skill.scope === "global" || skill.scope === "workspace") {
        const bucket = map.get(skill.scope);
        if (bucket) {
          bucket.add(skill.name);
          if (skill.origin_skill_id) {
            bucket.add(skill.origin_skill_id);
          }
        }
      }
    }
    return map;
  }, [state.installedSkills]);

  const isRemoteInstalledInTarget = useCallback(
    (skill: RemoteSkillSummary): boolean => {
      const bucket = installedKeysByScope.get(targetScope);
      if (!bucket) return false;
      return bucket.has(skill.name) || bucket.has(skill.skill_id);
    },
    [installedKeysByScope, targetScope],
  );

  const handlePreviewInstalled = async (skill: InstalledSkill) => {
    setIsLoadingPreview(true);
    setError(null);
    try {
      const detail = await apiClient.getInstalledSkillDetail(
        skill.scope,
        skill.name,
        state.workspacePath || undefined,
      );
      setPreview({
        title: detail.name,
        subtitle: `${detail.scope} · ${detail.source_label}`,
        content: detail.content,
        files: detail.files,
      });
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to load skill preview");
    } finally {
      setIsLoadingPreview(false);
    }
  };

  const handlePreviewRemote = async (skill: RemoteSkillSummary) => {
    setIsLoadingPreview(true);
    setError(null);
    try {
      const detail = await apiClient.previewRemoteSkill(skill.source, skill.skill_id);
      setPreview({
        title: detail.name,
        subtitle: `${detail.source} (${detail.skill_id})`,
        content: detail.content,
        files: detail.files,
      });
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to preview remote skill");
    } finally {
      setIsLoadingPreview(false);
    }
  };

  const handleInstallRemote = async (source: string, skillId?: string, key?: string) => {
    const installKey = key ?? `${source}:${skillId ?? ""}`;
    setInstallingKey(installKey);
    setError(null);
    setNotice(null);
    try {
      const installed = await apiClient.installSkill({
        source,
        skill_id: skillId ?? null,
        scope: targetScope,
        workspace_path: targetScope === "workspace" ? state.workspacePath : null,
      });
      await actions.refreshInstalledSkills();
      setNotice(`Installed "${installed.name}" into ${installed.scope} scope.`);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to install skill");
    } finally {
      setInstallingKey(null);
    }
  };

  const handleDirectInstall = async () => {
    const parsed = parseDirectSource(directSource);
    if (!parsed.source) return;
    await handleInstallRemote(parsed.source, parsed.skillId, "direct-install");
    setDirectSource("");
  };

  const handleConfirmDelete = async (skill: InstalledSkill) => {
    const key = `${skill.scope}:${skill.name}`;
    setDeletingKey(key);
    setError(null);
    setNotice(null);
    try {
      await apiClient.deleteSkill(skill.scope, skill.name, state.workspacePath || undefined);
      setPendingDeleteKey(null);
      if (state.pinnedSkills.includes(skill.name)) {
        actions.unpinSkill(skill.name);
      }
      if (preview?.title === skill.name) {
        setPreview(null);
      }
      await actions.refreshInstalledSkills();
      setNotice(`Removed "${skill.name}" from ${skill.scope} scope.`);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to delete skill");
    } finally {
      setDeletingKey(null);
    }
  };

  const handlePinSkill = (name: string) => {
    actions.pinSkill(name);
    actions.closeSkills();
  };

  const discoverSkills = searchQuery.trim().length >= 2
    ? (searchResults ?? [])
    : (catalogPage?.skills ?? []);

  return (
    <Dialog
      isOpen={state.skillsOpen}
      onOpenChange={(open) => {
        if (!open) actions.closeSkills();
      }}
      purpose="form"
      width={720}
    >
      <Layout
        height="auto"
        header={
          <DialogHeader
            title="Skills"
            subtitle="Manage installed skills or discover and install packages from skills.sh."
            onOpenChange={(open) => {
              if (!open) actions.closeSkills();
            }}
          />
        }
        content={
          <LayoutContent padding={4}>
            <VStack gap={3}>
              <TabList
                role="tablist"
                value={mainTab}
                onChange={(val) => {
                  setMainTab(val as MainSkillsTab);
                  setError(null);
                  setNotice(null);
                }}
                size="sm"
                hasDivider
              >
                <Tab
                  value="installed"
                  panelId={installedPanelId}
                  label={`Installed (${state.installedSkills.length})`}
                />
                <Tab
                  value="discover"
                  panelId={discoverPanelId}
                  label="Discover (skills.sh)"
                />
              </TabList>

              {error && <Banner status="error" title={error} />}
              {notice && <Banner status="success" title={notice} />}

              {mainTab === "installed" ? (
                <VStack id={installedPanelId} role="tabpanel" gap={2} height={320} isScrollable>
                  {state.installedSkills.length === 0 ? (
                    <EmptyState
                      isCompact
                      icon={<Icon icon={SparklesIcon} size="md" color="secondary" />}
                      title="No skills installed"
                      description="Switch to Discover to browse or install skills from skills.sh."
                    />
                  ) : (
                    state.installedSkills.map((skill) => {
                      const key = `${skill.scope}:${skill.name}`;
                      const isConfirming = pendingDeleteKey === key;
                      const isDeleting = deletingKey === key;
                      const isPinned = state.pinnedSkills.includes(skill.name);

                      return (
                        <Card key={key} padding={3}>
                          <VStack gap={2}>
                            <HStack hAlign="between" vAlign="center" gap={2} wrap="wrap">
                              <HStack gap={1.5} vAlign="center" wrap="wrap">
                                <Text type="label" weight="semibold">
                                  {skill.name}
                                </Text>
                                <Badge
                                  label={skill.scope}
                                  variant={scopeBadgeVariant(skill.scope)}
                                />
                                {!skill.deletable && (
                                  <Badge label="Protected" variant="neutral" />
                                )}
                                {isPinned && <Badge label="Pinned" variant="purple" />}
                              </HStack>

                              <HStack gap={1} vAlign="center">
                                <Button
                                  label="Preview"
                                  variant="ghost"
                                  size="sm"
                                  icon={<Icon icon={EyeIcon} size="sm" />}
                                  isLoading={isLoadingPreview}
                                  onClick={() => void handlePreviewInstalled(skill)}
                                />
                                <Button
                                  label={isPinned ? "Pinned" : "Pin"}
                                  variant={isPinned ? "secondary" : "primary"}
                                  size="sm"
                                  icon={<Icon icon={BookmarkIcon} size="sm" />}
                                  onClick={() => handlePinSkill(skill.name)}
                                />
                                {skill.deletable &&
                                  (isConfirming ? (
                                    <HStack gap={1} vAlign="center">
                                      <Button
                                        label="Confirm Delete"
                                        variant="destructive"
                                        size="sm"
                                        isLoading={isDeleting}
                                        onClick={() => void handleConfirmDelete(skill)}
                                      />
                                      <Button
                                        label="Cancel"
                                        variant="ghost"
                                        size="sm"
                                        isDisabled={isDeleting}
                                        onClick={() => setPendingDeleteKey(null)}
                                      />
                                    </HStack>
                                  ) : (
                                    <Button
                                      label="Delete"
                                      variant="ghost"
                                      size="sm"
                                      icon={<Icon icon={TrashIcon} size="sm" />}
                                      onClick={() => setPendingDeleteKey(key)}
                                    />
                                  ))}
                              </HStack>
                            </HStack>

                            {skill.description && (
                              <Text type="supporting" color="secondary">
                                {skill.description}
                              </Text>
                            )}
                          </VStack>
                        </Card>
                      );
                    })
                  )}
                </VStack>
              ) : (
                <VStack id={discoverPanelId} role="tabpanel" gap={3}>
                  <HStack hAlign="between" vAlign="center" gap={2} wrap="wrap">
                    <SegmentedControl
                      label="Catalog leaderboard"
                      size="sm"
                      value={catalogTab}
                      onChange={(val) => setCatalogTab(val as CatalogTab)}
                    >
                      <SegmentedControlItem value="all-time" label="All-Time" />
                      <SegmentedControlItem value="trending" label="Trending" />
                      <SegmentedControlItem value="hot" label="Hot" />
                    </SegmentedControl>

                    <SegmentedControl
                      label="Install scope"
                      size="sm"
                      value={targetScope}
                      onChange={(val) => setTargetScope(val as InstallTargetScope)}
                    >
                      <SegmentedControlItem value="global" label="Global" />
                      <SegmentedControlItem
                        value="workspace"
                        label="This Project"
                        isDisabled={!state.workspacePath}
                      />
                    </SegmentedControl>
                  </HStack>

                  <HStack gap={2} vAlign="end">
                    <TextInput
                      label="Search skills.sh"
                      isLabelHidden
                      value={searchQuery}
                      onChange={setSearchQuery}
                      onEnter={() => void runSearch(searchQuery)}
                      placeholder="Search skills.sh (min 2 characters)..."
                      startIcon={<Icon icon={MagnifyingGlassIcon} size="sm" />}
                      hasClear
                    />
                  </HStack>

                  <HStack gap={2} vAlign="end">
                    <TextInput
                      label="Direct GitHub install"
                      value={directSource}
                      onChange={setDirectSource}
                      onEnter={() => void handleDirectInstall()}
                      placeholder="owner/repo or owner/repo@skill-id"
                    />
                    <Button
                      label="Install"
                      variant="secondary"
                      size="md"
                      icon={<Icon icon={ArrowDownTrayIcon} size="sm" />}
                      isLoading={installingKey === "direct-install"}
                      isDisabled={!directSource.trim()}
                      onClick={() => void handleDirectInstall()}
                    />
                  </HStack>

                  <VStack gap={2} height={240} isScrollable>
                    {isLoadingDiscover && discoverSkills.length === 0 ? (
                      <Text type="supporting" color="secondary">
                        Loading skills from skills.sh...
                      </Text>
                    ) : discoverSkills.length === 0 ? (
                      <EmptyState
                        isCompact
                        title="No matching skills"
                        description="Try another query or install directly via owner/repo above."
                      />
                    ) : (
                      discoverSkills.map((skill) => {
                        const key = `${skill.source}:${skill.skill_id}`;
                        const installedInScope = isRemoteInstalledInTarget(skill);
                        const isInstalling = installingKey === key;

                        return (
                          <Card key={skill.id || key} padding={3}>
                            <HStack hAlign="between" vAlign="center" gap={2} wrap="wrap">
                              <VStack gap={0.5}>
                                <HStack gap={1.5} vAlign="center" wrap="wrap">
                                  <Text type="label" weight="semibold">
                                    {skill.name}
                                  </Text>
                                  {skill.is_official && (
                                    <Badge label="Official" variant="info" />
                                  )}
                                  <Badge
                                    label={`${formatInstalls(skill.installs)} installs`}
                                    variant="neutral"
                                  />
                                </HStack>
                                <Text type="supporting" color="secondary">
                                  {skill.source}
                                </Text>
                              </VStack>

                              <HStack gap={1} vAlign="center">
                                <Button
                                  label="Preview"
                                  variant="ghost"
                                  size="sm"
                                  icon={<Icon icon={EyeIcon} size="sm" />}
                                  isLoading={isLoadingPreview}
                                  onClick={() => void handlePreviewRemote(skill)}
                                />
                                {installedInScope ? (
                                  <Badge label="Installed" variant="success" />
                                ) : (
                                  <Button
                                    label="Install"
                                    variant="primary"
                                    size="sm"
                                    icon={<Icon icon={ArrowDownTrayIcon} size="sm" />}
                                    isLoading={isInstalling}
                                    onClick={() =>
                                      void handleInstallRemote(skill.source, skill.skill_id, key)
                                    }
                                  />
                                )}
                              </HStack>
                            </HStack>
                          </Card>
                        );
                      })
                    )}

                    {searchQuery.trim().length < 2 && catalogPage?.has_more && (
                      <HStack hAlign="center" paddingBlock={1}>
                        <Button
                          label="Load More"
                          variant="ghost"
                          size="sm"
                          isLoading={isLoadingDiscover}
                          onClick={() => void loadCatalog(catalogTab, catalogPage.page + 1, true)}
                        />
                      </HStack>
                    )}
                  </VStack>
                </VStack>
              )}

              {preview && (
                <>
                  <Divider />
                  <VStack gap={2}>
                    <HStack hAlign="between" vAlign="center">
                      <VStack gap={0.5}>
                        <Heading level={4}>{preview.title}</Heading>
                        <Text type="supporting" color="secondary">
                          {preview.subtitle}
                        </Text>
                      </VStack>
                      <Button
                        label="Close Preview"
                        variant="ghost"
                        size="sm"
                        onClick={() => setPreview(null)}
                      />
                    </HStack>
                    <CodeBlock
                      code={preview.content}
                      language="markdown"
                      title="SKILL.md"
                      width="100%"
                      maxHeight={220}
                      hasCopyButton
                    />
                  </VStack>
                </>
              )}
            </VStack>
          </LayoutContent>
        }
        footer={
          <LayoutFooter hasDivider>
            <HStack hAlign="between" vAlign="center" width="100%">
              <Text type="supporting" color="secondary">
                Tip: Pin a skill or type /skill-name at the start of a message.
              </Text>
              <Button label="Done" variant="secondary" onClick={actions.closeSkills} />
            </HStack>
          </LayoutFooter>
        }
      />
    </Dialog>
  );
}
