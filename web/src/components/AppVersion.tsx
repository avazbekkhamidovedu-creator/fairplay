import { useEffect, useState } from "react";
import * as bridge from "../lib/bridge";

const PLATFORM_NAME: Record<string, string> = { mac: "Mac", windows: "Windows", ipad: "iPad" };

/** True when the page is served from this computer or a private network address: a local test server. */
export function isLocalServer(host: string = typeof window === "undefined" ? "" : window.location.hostname): boolean {
  const h = host.toLowerCase();
  if (h === "localhost" || h === "::1" || h === "[::1]" || h.endsWith(".local") || h.endsWith(".localhost")) return true;
  const m = /^(\d{1,3})\.(\d{1,3})\.\d{1,3}\.\d{1,3}$/.exec(h);
  if (!m) return false;
  const a = Number(m[1]);
  const b = Number(m[2]);
  return a === 127 || a === 10 || (a === 192 && b === 168) || (a === 172 && b >= 16 && b <= 31);
}

export function appVersionLabel(state: bridge.NativeState | null, short = false, local = isLocalServer()): string | null {
  if (!state?.app_version) return null;
  const platform = state.platform ? PLATFORM_NAME[state.platform] ?? state.platform : "";
  const name = short ? `v${state.app_version}` : `FairPlay ${state.app_version}`;
  const base = platform ? `${name} · ${platform}` : name;
  return local ? `${base} · local app` : base;
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
