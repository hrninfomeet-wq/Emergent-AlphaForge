import { createContext, useContext, useEffect, useMemo, useState } from "react";

const THEME_STORAGE_KEY = "alphaforge-theme";
const THEME_VALUES = ["system", "black", "white"];

function getStoredTheme() {
  if (typeof window === "undefined") return "system";
  const stored = window.localStorage.getItem(THEME_STORAGE_KEY);
  return THEME_VALUES.includes(stored) ? stored : "system";
}

function getSystemTheme() {
  if (typeof window === "undefined" || !window.matchMedia) return "dark";
  return window.matchMedia("(prefers-color-scheme: light)").matches ? "light" : "dark";
}

function resolveTheme(theme) {
  if (theme === "white") return "light";
  if (theme === "black") return "dark";
  return getSystemTheme();
}

const ThemeContext = createContext({
  theme: "system",
  effectiveTheme: "dark",
  setTheme: () => {},
});

export function ThemeProvider({ children }) {
  const [theme, setThemeState] = useState(getStoredTheme);
  const [systemTheme, setSystemTheme] = useState(getSystemTheme);
  const effectiveTheme = theme === "system" ? systemTheme : resolveTheme(theme);

  useEffect(() => {
    if (typeof window === "undefined" || !window.matchMedia) return undefined;
    const query = window.matchMedia("(prefers-color-scheme: light)");
    const onChange = () => setSystemTheme(getSystemTheme());
    query.addEventListener("change", onChange);
    return () => query.removeEventListener("change", onChange);
  }, []);

  useEffect(() => {
    const root = document.documentElement;
    root.dataset.themeMode = theme;
    root.dataset.theme = effectiveTheme;
    root.classList.toggle("dark", effectiveTheme === "dark");
    root.classList.toggle("light", effectiveTheme === "light");
  }, [theme, effectiveTheme]);

  const setTheme = (nextTheme) => {
    const normalized = THEME_VALUES.includes(nextTheme) ? nextTheme : "system";
    window.localStorage.setItem(THEME_STORAGE_KEY, normalized);
    setThemeState(normalized);
  };

  const value = useMemo(() => ({ theme, effectiveTheme, setTheme }), [theme, effectiveTheme]);
  return <ThemeContext.Provider value={value}>{children}</ThemeContext.Provider>;
}

export function useTheme() {
  return useContext(ThemeContext);
}
