import { AppShell } from "@astryxdesign/core/AppShell";
import { Badge } from "@astryxdesign/core/Badge";
import { Button } from "@astryxdesign/core/Button";
import { Icon } from "@astryxdesign/core/Icon";
import { Layout, LayoutContent, LayoutPanel } from "@astryxdesign/core/Layout";
import { ResizeHandle, useResizable } from "@astryxdesign/core/Resizable";
import {
  SideNavHeading,
  SideNavItem,
  SideNavSection,
} from "@astryxdesign/core/SideNav";
import { HStack, VStack } from "@astryxdesign/core/Stack";
import { StatusDot, type StatusDotVariant } from "@astryxdesign/core/StatusDot";
import { Text } from "@astryxdesign/core/Text";
import { Toolbar } from "@astryxdesign/core/Toolbar";
import {
  Cog6ToothIcon,
  FolderPlusIcon,
  PlusIcon,
  TrashIcon,
  XMarkIcon,
} from "@heroicons/react/24/outline";
import type { Project, Session, SessionStatus } from "../../api/types";
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

function sessionsForProject(sessions: Session[], project: Project): Session[] {
  return sessions.filter(
    (s) => s.project_id === project.id || (!s.project_id && s.workspace_path === project.path),
  );
}

export function WorkspaceShell() {
  const { state, actions, meta } = useWorkspace();
  const sidebarResizable = useResizable({
    defaultSize: 280,
    minSize: 220,
    maxSize: 520,
    autoSaveId: "abyss.sidebar.width",
  });
  const drawerResizable = useResizable({
    defaultSize: 460,
    minSize: 340,
    maxSize: 720,
    autoSaveId: "abyss.drawer.width",
  });

  const dirtyCount = state.diff?.files.length ?? 0;

  const toggleDrawer = (tab: Exclude<RightDrawerTab, null>) => {
    actions.openRightDrawer(state.rightDrawerTab === tab ? null : tab);
  };

  const displayProjects: Project[] =
    state.projects.length > 0
      ? state.projects
      : state.workspacePath
        ? [
            {
              id: state.activeProjectId ?? "default",
              name: state.workspaceInfo?.name ?? state.workspacePath.split("/").pop() ?? "Workspace",
              path: state.workspacePath,
              is_git_repo: state.workspaceInfo?.is_git_repo ?? false,
              branch: state.workspaceInfo?.branch ?? "",
              created_at: "",
              updated_at: "",
            },
          ]
        : [];

  const sidebarPanel = (
    <>
      <LayoutPanel
        resizable={sidebarResizable}
        padding={0}
        isScrollable={false}
        role="navigation"
        label="Side navigation"
      >
        <Layout
          height="fill"
          padding={0}
          header={
            <VStack gap={2} padding={3}>
              <SideNavHeading heading="Abyss" subheading="DeepAgents v2" />
              <Button
                label="Add Project"
                variant="primary"
                width="100%"
                icon={<Icon icon={FolderPlusIcon} size="sm" />}
                onClick={actions.openAddProject}
              />
              {state.workspaceError && (
                <Text type="supporting" color="secondary">
                  {state.workspaceError}
                </Text>
              )}
            </VStack>
          }
          content={
            <LayoutContent padding={2} isScrollable>
              {displayProjects.map((project) => {
                const projectSessions = sessionsForProject(state.sessions, project);
                const isCurrentProject =
                  project.id === state.activeProjectId || project.path === state.workspacePath;
                return (
                  <SideNavSection
                    key={project.id}
                    title={project.name}
                    subtitle={project.is_git_repo ? `git: ${project.branch}` : project.path}
                    endContent={
                      <HStack gap={0.5} vAlign="center">
                        <Button
                          label={`New session in ${project.name}`}
                          variant="ghost"
                          size="sm"
                          isIconOnly
                          icon={<Icon icon={PlusIcon} size="sm" />}
                          onClick={() => void actions.createNewSession(project.id)}
                        />
                        {state.projects.length > 1 && (
                          <Button
                            label={`Remove project ${project.name}`}
                            variant="ghost"
                            size="sm"
                            isIconOnly
                            icon={<Icon icon={TrashIcon} size="sm" />}
                            onClick={() => void actions.removeProject(project.id)}
                          />
                        )}
                      </HStack>
                    }
                  >
                    {projectSessions.length > 0 ? (
                      projectSessions.map((session) => (
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
                      ))
                    ) : (
                      <SideNavItem
                        label={`Open ${project.name}`}
                        isSelected={isCurrentProject}
                        onClick={() => void actions.selectProject(project.id)}
                      />
                    )}
                  </SideNavSection>
                );
              })}
            </LayoutContent>
          }
          footer={
            <VStack padding={3}>
              <Button
                label={meta.configured ? state.settings.model : "Configure LLM"}
                variant={meta.configured ? "secondary" : "primary"}
                width="100%"
                icon={<Icon icon={Cog6ToothIcon} size="sm" />}
                onClick={actions.openSettings}
              />
            </VStack>
          }
        />
      </LayoutPanel>
      <ResizeHandle
        resizable={sidebarResizable}
        direction="horizontal"
        hasDivider
        isAlwaysVisible
        label="Resize projects sidebar"
      />
    </>
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
    <AppShell height="fill">
      <Layout
        height="fill"
        padding={0}
        start={sidebarPanel}
        content={
          <LayoutContent padding={0} isScrollable={false}>
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
                      {state.workspaceInfo?.is_git_repo && state.workspaceInfo.branch && (
                        <Badge label={state.workspaceInfo.branch} variant="neutral" />
                      )}
                    </HStack>
                  }
                  endContent={
                    <HStack gap={1} vAlign="center">
                      <Button
                        label="Skills"
                        variant={state.skillsOpen ? "primary" : "ghost"}
                        size="sm"
                        endContent={
                          state.pinnedSkills.length > 0 ? (
                            <Badge label={String(state.pinnedSkills.length)} variant="purple" />
                          ) : undefined
                        }
                        onClick={actions.openSkills}
                      />
                      <Button
                        label="Memory"
                        variant={state.memoryOpen ? "primary" : "ghost"}
                        size="sm"
                        onClick={actions.openMemory}
                      />
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
                        endContent={
                          dirtyCount > 0 ? (
                            <Badge label={String(dirtyCount)} variant="info" />
                          ) : undefined
                        }
                        onClick={() => toggleDrawer("diff")}
                      />
                    </HStack>
                  }
                />
              }
              content={
                <LayoutContent padding={0} isScrollable={false}>
                  <VStack height="100%" minHeight={0}>
                    <MessageStream />
                  </VStack>
                </LayoutContent>
              }
              end={
                state.rightDrawerTab ? (
                  <>
                    <ResizeHandle
                      resizable={drawerResizable}
                      isReversed
                      hasDivider
                      isAlwaysVisible
                      label="Resize inspector drawer"
                    />
                    <LayoutPanel
                      resizable={drawerResizable}
                      padding={0}
                      isScrollable
                      label="Inspector drawer"
                    >
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
                  </>
                ) : undefined
              }
            />
          </LayoutContent>
        }
      />
    </AppShell>
  );
}
