import "@astryxdesign/core/reset.css";
import "@astryxdesign/core/astryx.css";

import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { App } from "./App";
import { ColorModeProvider } from "./state/colorMode";

const rootEl = document.getElementById("root");
if (rootEl) {
  createRoot(rootEl).render(
    <StrictMode>
      <ColorModeProvider>
        <App />
      </ColorModeProvider>
    </StrictMode>,
  );
}
