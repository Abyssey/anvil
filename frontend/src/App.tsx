import { SettingsDialog } from "./components/settings/SettingsDialog";
import { AddProjectDialog } from "./components/workspace/AddProjectDialog";
import { AppErrorBoundary } from "./components/workspace/AppErrorBoundary";
import { WorkspaceShell } from "./components/workspace/WorkspaceShell";
import { WorkspaceProvider } from "./state/workspaceContext";

export function App() {
  return (
    <AppErrorBoundary>
      <WorkspaceProvider>
        <WorkspaceShell />
        <SettingsDialog />
        <AddProjectDialog />
      </WorkspaceProvider>
    </AppErrorBoundary>
  );
}
