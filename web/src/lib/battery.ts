import { useEffect, useState } from "react";

/**
 * 2026-10-23: battery level for the exam top bar.
 * Uses the browser Battery API where it exists (Chromium: Windows WebView2, Chrome).
 * Mac (WKWebView) and iPad have no Battery API; the apps will report it through the bridge
 * later (window.fairplayBattery), until then the battery is simply not shown there.
 */
export interface BatteryState {
  /** 0..1 */
  level: number;
  charging: boolean;
}

export const LOW_BATTERY = 0.2;
export const CRITICAL_BATTERY = 0.1;

interface BatteryManagerLike extends EventTarget {
  level: number;
  charging: boolean;
}

type NativeBattery = { level: number; charging: boolean } | undefined;

function fromNative(): BatteryState | null {
  const b = (window as unknown as { fairplayBattery?: NativeBattery }).fairplayBattery;
  if (b && typeof b.level === "number" && b.level >= 0 && b.level <= 1) return { level: b.level, charging: !!b.charging };
  return null;
}

export function useBattery(): BatteryState | null {
  const [state, setState] = useState<BatteryState | null>(() => fromNative());
  useEffect(() => {
    let alive = true;
    let mgr: BatteryManagerLike | null = null;
    const update = () => {
      if (alive && mgr) setState({ level: mgr.level, charging: mgr.charging });
    };
    // Native apps push "fairplay-battery" events after setting window.fairplayBattery.
    const onNative = () => {
      const s = fromNative();
      if (alive && s) setState(s);
    };
    window.addEventListener("fairplay-battery", onNative);
    const nav = navigator as Navigator & { getBattery?: () => Promise<BatteryManagerLike> };
    if (!fromNative() && typeof nav.getBattery === "function") {
      nav.getBattery().then(
        (m) => {
          mgr = m;
          update();
          m.addEventListener("levelchange", update);
          m.addEventListener("chargingchange", update);
        },
        () => undefined,
      );
    }
    return () => {
      alive = false;
      window.removeEventListener("fairplay-battery", onNative);
      if (mgr) {
        mgr.removeEventListener("levelchange", update);
        mgr.removeEventListener("chargingchange", update);
      }
    };
  }, []);
  return state;
}

export function batteryTone(b: BatteryState): "ok" | "low" | "critical" {
  if (b.charging) return "ok";
  if (b.level < CRITICAL_BATTERY) return "critical";
  if (b.level < LOW_BATTERY) return "low";
  return "ok";
}
