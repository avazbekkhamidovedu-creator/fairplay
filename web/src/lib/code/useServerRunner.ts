import { useEffect, useSyncExternalStore } from "react";
import { loadHealth } from "../features";
import { remoteLanguages, setServerRunnerLanguages, subscribeRunnerLanguages } from "./languages";

/** Reads /api/health once and stores which languages the server's runner offers (none on any failure). */
export function loadServerRunner(): Promise<void> {
  return loadHealth().then((h) => {
    const r = h?.code_runner;
    const list = r && typeof r === "object" ? (r as { languages?: unknown }).languages : null;
    setServerRunnerLanguages(Array.isArray(list) ? list : []);
  });
}

/** The languages the server's runner offers; asks the server on first use. */
export function useServerRunnerLanguages(): readonly string[] {
  useEffect(() => {
    void loadServerRunner();
  }, []);
  return useSyncExternalStore(subscribeRunnerLanguages, remoteLanguages, remoteLanguages);
}

/** Test helper. */
export function _resetServerRunnerForTests(): void {
  setServerRunnerLanguages([]);
}
