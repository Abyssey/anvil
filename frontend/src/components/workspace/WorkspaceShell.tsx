import { useEffect, useState } from "react";
import { AppShell } from "@astryxdesign/core/AppShell";
import { Badge } from "@astryxdesign/core/Badge";
import { Button } from "@astryxdesign/core/Button";
import { Icon } from "@astryxdesign/core/Icon";
import { Layout, LayoutContent, LayoutPanel } from "@astryxdesign/core/Layout";
import {
  SideNav,
  SideNavHeading,
  SideNavItem,
  SideNavSection,
} from "@astryxdesign/core/SideNav";
import { HStack, VStack } from "@astryxdesign/core/Stack";
import { StatusDot, type StatusDotVariant } from "@astryxdesign/core/StatusDot";
import { Text } from "@astryxdesign/core/Text";
import { TextInput } from "@astryxdesign/core/TextInput";
import { Toolbar } from "@astryxdesign/core/Toolbar";
import {
  Cog6ToothIcon,
  PlusIcon,
  TrashIcon,
  XMarkIcon,
} from "@heroicons/react/24/outline";
import type { SessionStatus } from "../../api/types";
import { useWorkspace, type RightDrawerTab } from "../../state/workspaceContext";
import { ArtifactDrawer } from "../artifacts/ArtifactDrawer";
import { MessageStream } from "../chat/MessageStream";
import { DiffDrawer } from "../diff/DiffDrawer";
import { FileExplorerDrawer } from "../explorer/FileExplorerDrawer";

function statusVariant(status: SessionStatus): StatusDotVariant {
  if (status === "awaiting_approval") return "warning";
  if (status === "running") return "accent";
  if (status === "error") return "error";
  return "neutral";
}

export function WorkspaceShell() {
  const { state, actions, meta } = useWorkspace();
  const [pathDraft, setPathDraft] = useState(state.workspacePath);

  useEffect(() => {
    setPathDraft(state.workspacePath);
  }, [state.workspacePath]);

  const dirtyCount = state.diff?.files.length ?? 0;

  const toggleDrawer = (tab: Exclude<RightDrawerTab, null>) => {
    actions.openRightDrawer(state.rightDrawerTab === tab ? null : tab);
  };

  const sideNav = (
    <SideNav
      collapsible
      resizable={{ defaultWidth: 280, minWidth: 240, maxWidth: 360 }}
      header={<SideNavHeading heading="Abyss" subheading="DeepAgents v2" />}
      topContent={
        <VStack gap={2}>
          <Button
            label="New Session"
            variant="primary"
            width="100%"
            icon={<Icon icon={PlusIcon} size="sm" />}
            onClick={() => void actions.createNewSession()}
          />
          <TextInput
            label="Workspace directory"
            isLabelHidden
            value={pathDraft}
            onChange={setPathDraft}
            onEnter={() => void actions.setWorkspacePath(pathDraft)}
            placeholder="/path/to/repository"
            status={state.workspaceError ? { type: "error", message: state.workspaceError } : undefined}
          />
          <HStack hAlign="between" vAlign="center">
            <Text type="supporting" color="secondary">
              {state.workspaceInfo?.is_git_repo
                ? `git: ${state.workspaceInfo.branch}`
                : "Local folder"}
            </Text>
            <Button
              label="Apply"
              variant="ghost"
              size="sm"
              onClick={() => void actions.setWorkspacePath(pathDraft)}
            />
          </HStack>
        </VStack>
      }
      footer={
        <Button
          label={meta.configured ? state.settings.model : "Configure LLM"}
          variant={meta.configured ? "secondary" : "primary"}
          width="100%"
          icon={<Icon icon={Cog6ToothIcon} size="sm" />}
          onClick={actions.openSettings}
        />
      }
    >
      <SideNavSection title="Sessions">
        {state.sessions.map((session) => (
          <SideNavItem
            key={session.id}
            label={session.title}
            isSelected={session.id === state.activeSession?.id}
            endContent={
              <StatusDot
                variant={statusVariant(session.status)}
                label={session.status}
              />
            }
            actions={
              <Button
                label={`Delete ${session.title}`}
                variant="ghost"
                size="sm"
                isIconOnly
                icon={<Icon icon={TrashIcon} size="sm" />}
                onClick={(e) => {
                  e.stopPropagation();
                  void actions.removeSession(session.id);
                }}
              />
            }
            onClick={() => void actions.selectSession(session.id)}
          />
        ))}
      </SideNavSection>
    </SideNav>
  );

  const drawerContent =
    state.rightDrawerTab === "artifacts" ? (
      <ArtifactDrawer />
    ) : state.rightDrawerTab === "files" ? (
      <FileExplorerDrawer />
    ) : state.rightDrawerTab === "diff" ? (
      <DiffDrawer />
    ) : null;

  return (
    <AppShell height="fill" sideNav={sideNav}>
      <Layout
        height="fill"
        header={
          <Toolbar
            label="Workspace header"
            dividers={["bottom"]}
            startContent={
              <HStack gap={2} vAlign="center">
                <Text type="label" weight="semibold">
                  {state.activeSession?.title ?? "New Session"}
                </Text>
                {state.workspaceInfo && (
                  <Text type="supporting" color="secondary">
                    {state.workspaceInfo.name}
                  </Text>
                )}
              </HStack>
            }
            endContent={
              <HStack gap={1} vAlign="center">
                <Button
                  label="Artifacts"
                  variant={state.rightDrawerTab === "artifacts" ? "primary" : "ghost"}
                  size="sm"
                  onClick={() => toggleDrawer("artifacts")}
                />
                <Button
                  label="Files"
                  variant={state.rightDrawerTab === "files" ? "primary" : "ghost"}
                  size="sm"
                  onClick={() => toggleDrawer("files")}
                />
                <Button
                  label="Diff"
                  variant={state.rightDrawerTab === "diff" ? "primary" : "ghost"}
                  size="sm"
                  endContent={dirtyCount > 0 ? <Badge label={String(dirtyCount)} variant="info" /> : undefined}
                  onClick={() => toggleDrawer("diff")}
                />
              </HStack>
            }
          />
        }
        content={
          <LayoutContent padding={0} isScrollable={false}>
            <MessageStream />
          </LayoutContent>
        }
        end={
          state.rightDrawerTab ? (
            <LayoutPanel width={460} hasDivider isScrollable label="Inspector drawer">
              <Toolbar
                label="Drawer header"
                dividers={["bottom"]}
                startContent={
                  <Text type="label" weight="semibold">
                    {state.rightDrawerTab === "artifacts"
                      ? "Artifacts"
                      : state.rightDrawerTab === "files"
                        ? "Files"
                        : "Diff"}
                  </Text>
                }
                endContent={
                  <Button
                    label="Close drawer"
                    variant="ghost"
                    size="sm"
                    isIconOnly
                    icon={<Icon icon={XMarkIcon} size="sm" />}
                    onClick={() => actions.openRightDrawer(null)}
                  />
                }
              />
              {drawerContent}
            </LayoutPanel>
          ) : undefined
        }
      />
    </AppShell>
  );
}
