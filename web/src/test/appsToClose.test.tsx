// 2026-10-22: before the lock the app lists the apps it will close; the student saves their
// work and presses one button that closes them and starts.
import { afterEach, describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen } from "@testing-library/react";
import * as bridge from "../lib/bridge";
import { AppsToClose } from "../components/student/Preflight";

function native() {
  const postMessage = vi.fn();
  (window as unknown as { webkit: unknown }).webkit = { messageHandlers: { proctora: { postMessage } } };
  return postMessage;
}
const send = (detail: unknown) => window.dispatchEvent(new CustomEvent("proctora-native", { detail }));

describe("apps to close before the lock", () => {
  afterEach(() => {
    delete (window as unknown as { webkit?: unknown }).webkit;
    bridge._resetBridgeForTests();
  });

  it("preflight sends the allowed apps and reads apps_to_close defensively", async () => {
    const post = native();
    const p = bridge.preflightReport(3000, ["excel", "com.microsoft.Excel"]);
    expect(post).toHaveBeenCalledWith({ action: "preflight", allowed_apps: ["excel", "com.microsoft.Excel"] });
    send({ type: "preflight", problems: [], apps_to_close: ["Safari", "Word", "Safari", 7, " ", "x".repeat(200)] });
    const r = await p;
    expect(r?.apps_to_close).toEqual(["Safari", "Word", "x".repeat(80)]);
  });

  it("older apps without apps_to_close give an empty list", async () => {
    native();
    const p = bridge.preflightReport();
    send({ type: "preflight", problems: ["Turn off Screen Sharing."] });
    expect((await p)?.apps_to_close).toEqual([]);
  });

  it("quitApps asks the app and resolves with what is still open", async () => {
    const post = native();
    const p = bridge.quitApps(["calc"]);
    expect(post).toHaveBeenCalledWith({ action: "quitApps", allowed_apps: ["calc"] });
    send({ type: "appsQuit", still_running: ["Word"] });
    await expect(p).resolves.toEqual(["Word"]);
  });

  it("quitApps resolves [] outside the app and when the app never answers", async () => {
    await expect(bridge.quitApps()).resolves.toEqual([]);
    vi.useFakeTimers();
    native();
    const p = bridge.quitApps([], 9000);
    vi.advanceTimersByTime(9001);
    await expect(p).resolves.toEqual([]);
    vi.useRealTimers();
  });

  it("the screen lists the apps and starts with one button", () => {
    const go = vi.fn();
    const { rerender } = render(<AppsToClose apps={["Safari", "Telegram"]} busy={false} onContinue={go} />);
    expect(screen.getByText("Safari")).toBeTruthy();
    expect(screen.getByText("Telegram")).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "Close them and start" }));
    expect(go).toHaveBeenCalledTimes(1);
    rerender(<AppsToClose apps={["Safari"]} busy={true} onContinue={go} />);
    expect((screen.getByRole("button", { name: "Closing apps..." }) as HTMLButtonElement).disabled).toBe(true);
  });
});
