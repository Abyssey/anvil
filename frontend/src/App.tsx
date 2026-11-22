import { SettingsDialog } from "./components/settings/SettingsDialog";
import { WorkspaceShell } from "./components/workspace/WorkspaceShell";
import { WorkspaceProvider } from "./state/workspaceContext";

export function App() {
  return (
    <WorkspaceProvider>
      <WorkspaceShell />
      <SettingsDialog />
    </WorkspaceProvider>
  );
}
