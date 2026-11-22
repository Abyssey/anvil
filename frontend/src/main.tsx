import "@astryxdesign/core/reset.css";
import "@astryxdesign/core/astryx.css";

import { Theme } from "@astryxdesign/core/theme";
import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { App } from "./App";
import { defaultThemeSlug, themes } from "./astryx-themes";

const rootEl = document.getElementById("root");
if (rootEl) {
  createRoot(rootEl).render(
    <StrictMode>
      <Theme theme={themes[defaultThemeSlug]}>
        <App />
      </Theme>
    </StrictMode>,
  );
}
