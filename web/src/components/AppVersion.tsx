import { useEffect, useState } from "react";
import * as bridge from "../lib/bridge";

const PLATFORM_NAME: Record<string, string> = { mac: "Mac", windows: "Windows", ipad: "iPad" };

export function appVersionLabel(state: bridge.NativeState | null, short = false): string | null {
  if (!state?.app_version) return null;
  const platform = state.platform ? PLATFORM_NAME[state.platform] ?? state.platform : "";
  const name = short ? `v${state.app_version}` : `FairPlay ${state.app_version}`;
  return platform ? `${name} · ${platform}` : name;
}

export function useAppVersionLabel(short = false): string | null {
  const [label, setLabel] = useState(() => appVersionLabel(bridge.getNativeState(), short));
  useEffect(() => {
    setLabel(appVersionLabel(bridge.getNativeState(), short));
    return bridge.onNative((msg) => {
      if (msg.type === "state") setLabel(appVersionLabel(bridge.getNativeState(), short));
    });
  }, [short]);
  return label;
}

export function AppVersion({ className = "", short = false }: { className?: string; short?: boolean }) {
  const label = useAppVersionLabel(short);
  if (!label) return null;
  return (
    <span className={`app-version ${className}`.trim()} data-testid="app-version">
      {label}
    </span>
  );
}
