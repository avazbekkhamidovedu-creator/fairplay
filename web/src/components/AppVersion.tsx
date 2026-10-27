import { useEffect, useState } from "react";
import * as bridge from "../lib/bridge";

const PLATFORM_NAME: Record<string, string> = { mac: "Mac", windows: "Windows", ipad: "iPad" };

export function appVersionLabel(state: bridge.NativeState | null): string | null {
  if (!state?.app_version) return null;
  const platform = state.platform ? PLATFORM_NAME[state.platform] ?? state.platform : "";
  return platform ? `FairPlay ${state.app_version} · ${platform}` : `FairPlay ${state.app_version}`;
}

export function useAppVersionLabel(): string | null {
  const [label, setLabel] = useState(() => appVersionLabel(bridge.getNativeState()));
  useEffect(() => {
    setLabel(appVersionLabel(bridge.getNativeState()));
    return bridge.onNative((msg) => {
      if (msg.type === "state") setLabel(appVersionLabel(bridge.getNativeState()));
    });
  }, []);
  return label;
}

export function AppVersion({ className = "" }: { className?: string }) {
  const label = useAppVersionLabel();
  if (!label) return null;
  return (
    <span className={`app-version ${className}`.trim()} data-testid="app-version">
      {label}
    </span>
  );
}
