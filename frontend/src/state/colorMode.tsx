import { createContext, useCallback, useContext, useEffect, useMemo, useState, type ReactNode } from "react";
import { Theme } from "@astryxdesign/core/theme";
import { defaultThemeSlug, themes } from "../astryx-themes";

export type ColorMode = "system" | "light" | "dark";

const STORAGE_KEY = "abyss.color_mode";

interface ColorModeContextValue {
  colorMode: ColorMode;
  effectiveDark: boolean;
  toggleColorMode: () => void;
}

const ColorModeContext = createContext<ColorModeContextValue>({
  colorMode: "system",
  effectiveDark: false,
  toggleColorMode: () => undefined,
});

function readStoredColorMode(): ColorMode {
  try {
    const raw = window.localStorage.getItem(STORAGE_KEY);
    if (raw === "light" || raw === "dark" || raw === "system") {
      return raw;
    }
  } catch {
    // Ignore storage access errors in restricted environments
  }
  return "system";
}

function readSystemPrefersDark(): boolean {
  if (typeof window === "undefined" || typeof window.matchMedia !== "function") {
    return false;
  }
  return window.matchMedia("(prefers-color-scheme: dark)").matches;
}

export function ColorModeProvider({ children }: { children: ReactNode }) {
  const [colorMode, setColorMode] = useState<ColorMode>(readStoredColorMode);
  const [systemDark, setSystemDark] = useState<boolean>(readSystemPrefersDark);

  useEffect(() => {
    if (typeof window === "undefined" || typeof window.matchMedia !== "function") {
      return;
    }
    const mql = window.matchMedia("(prefers-color-scheme: dark)");
    const onChange = (e: MediaQueryListEvent) => setSystemDark(e.matches);
    setSystemDark(mql.matches);
    mql.addEventListener("change", onChange);
    return () => mql.removeEventListener("change", onChange);
  }, []);

  const effectiveDark = colorMode === "dark" || (colorMode === "system" && systemDark);

  const toggleColorMode = useCallback(() => {
    setColorMode((prev) => {
      const currentlyDark = prev === "dark" || (prev === "system" && readSystemPrefersDark());
      const next: ColorMode = currentlyDark ? "light" : "dark";
      try {
        window.localStorage.setItem(STORAGE_KEY, next);
      } catch {
        // Ignore storage errors
      }
      return next;
    });
  }, []);

  const value = useMemo(
    () => ({ colorMode, effectiveDark, toggleColorMode }),
    [colorMode, effectiveDark, toggleColorMode],
  );

  return (
    <ColorModeContext value={value}>
      <Theme theme={themes[defaultThemeSlug]} mode={colorMode}>
        {children}
      </Theme>
    </ColorModeContext>
  );
}

export function useColorMode(): ColorModeContextValue {
  return useContext(ColorModeContext);
}
