import { SettingsDialog } from "./components/settings/SettingsDialog";
import { SkillsDialog } from "./components/skills/SkillsDialog";
import { AddProjectDialog } from "./components/workspace/AddProjectDialog";
import { AppErrorBoundary } from "./components/workspace/AppErrorBoundary";
import { ProjectMemoryDialog } from "./components/workspace/ProjectMemoryDialog";
import { WorkspaceShell } from "./components/workspace/WorkspaceShell";
import { WorkspaceProvider } from "./state/workspaceContext";

export function App() {
  return (
    <AppErrorBoundary>
      <WorkspaceProvider>
        <WorkspaceShell />
        <SettingsDialog />
        <AddProjectDialog />
        <SkillsDialog />
        <ProjectMemoryDialog />
      </WorkspaceProvider>
    </AppErrorBoundary>
  );
}
