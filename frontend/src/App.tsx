import { SettingsDialog } from "./components/settings/SettingsDialog";
import { AppErrorBoundary } from "./components/workspace/AppErrorBoundary";
import { WorkspaceShell } from "./components/workspace/WorkspaceShell";
import { WorkspaceProvider } from "./state/workspaceContext";

export function App() {
  return (
    <AppErrorBoundary>
      <WorkspaceProvider>
        <WorkspaceShell />
        <SettingsDialog />
      </WorkspaceProvider>
    </AppErrorBoundary>
  );
}
