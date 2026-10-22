// 2026-10-22: a pre-exam problem ("Turn off Remote Login...") gets a button that opens the
// right System Settings pane on the Mac, and the screen checks again by itself.
import { afterEach, describe, expect, it, vi } from "vitest";
import { act, fireEvent, render, screen } from "@testing-library/react";
import * as bridge from "../lib/bridge";
import { PREFLIGHT_RECHECK_MS, PreflightProblems } from "../components/student/Preflight";

function mac() {
  const postMessage = vi.fn();
  (window as unknown as { webkit: unknown }).webkit = { messageHandlers: { proctora: { postMessage } } };
  return postMessage;
}

describe("fixing a pre-exam problem", () => {
  afterEach(() => {
    delete (window as unknown as { webkit?: unknown }).webkit;
    bridge._resetBridgeForTests();
    vi.useRealTimers();
  });

  it("maps the Mac app's problem texts to a settings pane", () => {
    expect(bridge.settingsPaneFor("Turn off Remote Login (SSH) in System Settings > General > Sharing.")).toBe("sharing");
    expect(bridge.settingsPaneFor("Set your Mac's date and time to automatic (System Settings > General > Date & Time).")).toBe("datetime");
    expect(bridge.settingsPaneFor("Only one screen is allowed. Unplug other displays and turn off mirroring, AirPlay and Sidecar.")).toBe("displays");
    expect(bridge.settingsPaneFor("Switch to an Apple keyboard (e.g. ABC or Pinyin - Simplified); x is not allowed.")).toBe("keyboard");
    expect(bridge.settingsPaneFor("This Mac is being used remotely. The exam only works at the Mac itself.")).toBeNull();
  });

  it("the button opens Sharing settings in the Mac app", () => {
    const post = mac();
    expect(bridge.nativePlatform()).toBe("mac");
    render(<PreflightProblems problems={["Turn off Remote Login (SSH) in System Settings > General > Sharing."]} onRetry={() => undefined} />);
    fireEvent.click(screen.getByRole("button", { name: "Open Sharing settings" }));
    expect(post).toHaveBeenCalledWith({ action: "openSettings", pane: "sharing" });
  });

  it("no settings button outside the Mac app", () => {
    render(<PreflightProblems problems={["Turn off Remote Login (SSH) in System Settings > General > Sharing."]} onRetry={() => undefined} />);
    expect(screen.queryByRole("button", { name: "Open Sharing settings" })).toBeNull();
  });

  it("checks again by itself, one check at a time", async () => {
    vi.useFakeTimers();
    let release: () => void = () => undefined;
    const retry = vi.fn(() => new Promise<void>((r) => (release = r)));
    render(<PreflightProblems problems={["x"]} onRetry={retry} />);
    await act(async () => void vi.advanceTimersByTime(PREFLIGHT_RECHECK_MS));
    expect(retry).toHaveBeenCalledTimes(1);
    await act(async () => void vi.advanceTimersByTime(PREFLIGHT_RECHECK_MS * 3));
    expect(retry).toHaveBeenCalledTimes(1); // still running
    await act(async () => release());
    await act(async () => void vi.advanceTimersByTime(PREFLIGHT_RECHECK_MS));
    expect(retry).toHaveBeenCalledTimes(2);
  });
});
