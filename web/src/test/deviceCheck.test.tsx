// S1 (2026-10-20): the app's full preflight answer, "Run check", the teacher's readiness list.
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import { setToken } from "../api/client";
import { DeviceCheckRun, DeviceStatus, ReadinessChecks } from "../components/student/Readiness";
import { DeviceReadiness } from "../pages/teacher/LiveRoom";
import * as bridge from "../lib/bridge";

function native() {
  const postMessage = vi.fn();
  (window as unknown as { webkit: unknown }).webkit = { messageHandlers: { proctora: { postMessage } } };
  return postMessage;
}
const send = (detail: unknown) => window.dispatchEvent(new CustomEvent("proctora-native", { detail }));

const ANSWER = {
  type: "preflight",
  problems: ["Unplug the second display"],
  checks: [
    { id: "macos_version", ok: true, message: "macOS 15.1" },
    { id: "displays", ok: false, message: "Unplug the second display" },
    { id: "vpn", ok: false, message: "A VPN is on", info: true },
    { id: "", ok: true, message: "no id: dropped" },
    "junk",
  ],
  platform: "mac",
  app_version: "0.5",
  os_version: "15.1",
};

describe("device check (S1)", () => {
  afterEach(() => {
    delete (window as unknown as { webkit?: unknown }).webkit;
    bridge._resetBridgeForTests();
    vi.unstubAllGlobals();
  });

  it("parses the preflight checks and keeps problems for older pages", async () => {
    const post = native();
    const p = bridge.preflightReport(1000);
    expect(post).toHaveBeenCalledWith({ action: "preflight" });
    send(ANSWER);
    const r = await p;
    expect(r).toEqual({
      problems: ["Unplug the second display"],
      apps_to_close: [],
      checks: [
        { id: "macos_version", ok: true, message: "macOS 15.1" },
        { id: "displays", ok: false, message: "Unplug the second display" },
        { id: "vpn", ok: false, message: "A VPN is on", info: true },
      ],
      platform: "mac",
      app_version: "0.5",
      os_version: "15.1",
    });
    // the old call still gives just the problems
    const old = bridge.preflight(1000);
    send(ANSWER);
    await expect(old).resolves.toEqual(["Unplug the second display"]);
  });

  it("an app that only sends problems gives no checks; outside the app null", async () => {
    await expect(bridge.preflightReport()).resolves.toBeNull();
    native();
    const p = bridge.preflightReport(1000);
    send({ type: "preflight", problems: ["VM"] });
    const r = await p;
    expect(r?.checks).toEqual([]);
    expect(r?.platform).toBe("mac");     // from the transport when the app doesn't say
  });

  it("Run check shows the rows and posts them to the server", async () => {
    native();
    setToken("student", "S");
    const fetchFn = vi.fn(async () => new Response(JSON.stringify({ ok: true, saved_at: "x" }), { status: 200 }));
    vi.stubGlobal("fetch", fetchFn);
    render(<DeviceCheckRun examId="e_1" />);
    await userEvent.click(screen.getByRole("button", { name: "Run check" }));
    send(ANSWER);
    expect(await screen.findByText("Your teacher can see this check.")).toBeInTheDocument();
    expect(screen.getByText("macOS 15.1")).toBeInTheDocument();
    expect(screen.getByText("A VPN is on")).toBeInTheDocument();
    const [url, init] = fetchFn.mock.calls[0] as unknown as [string, RequestInit];
    expect(url).toBe("/api/student/device-check");
    const body = JSON.parse(String(init.body));
    expect(body).toMatchObject({ platform: "mac", app_version: "0.5", os_version: "15.1", exam_id: "e_1" });
    expect(body.checks).toHaveLength(3);
  });

  it("Test my device: checks by itself and shows one line plus only the problems", async () => {
    native();
    setToken("student", "S");
    vi.stubGlobal("fetch", vi.fn(async () => new Response(JSON.stringify({ ok: true }), { status: 200 })));
    render(<DeviceStatus readiness={{ native: true, platform: "mac", trusted: true }} />);
    expect(screen.getByText("Checking this Mac…")).toBeInTheDocument();
    send({ ...ANSWER, apps_to_close: ["Safari", "Word"] });
    expect(await screen.findByText("1 thing to fix")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Open settings" })).toBeInTheDocument(); // Displays settings
    expect(await screen.findByText("1 thing to fix")).toBeInTheDocument();
    expect(screen.getByRole("list", { name: "Things to fix" }).textContent).toBe("Unplug the second display Open settings");
    expect(screen.getByText("A VPN is on")).toBeInTheDocument();
    expect(screen.getByText(/2 apps will close/)).toBeInTheDocument();
    expect(screen.queryByText("macOS 15.1")).toBeNull();            // passed rows stay folded
    await userEvent.click(screen.getByRole("button", { name: "Show all checks" }));
    expect(screen.getByText("macOS 15.1")).toBeInTheDocument();
  });

  it("Test my device: a Sharing problem gets Open settings", async () => {
    const post = native();
    setToken("student", "S");
    vi.stubGlobal("fetch", vi.fn(async () => new Response(JSON.stringify({ ok: true }), { status: 200 })));
    render(<DeviceStatus readiness={{ native: true, platform: "mac", trusted: true }} />);
    const msg = "Turn off Remote Login (SSH) in System Settings > General > Sharing.";
    send({ type: "preflight", problems: [msg], checks: [{ id: "blocked_apps", ok: false, message: msg }], platform: "mac" });
    await userEvent.click(await screen.findByRole("button", { name: "Open settings" }));
    expect(post).toHaveBeenCalledWith({ action: "openSettings", pane: "sharing" });
  });

  it("Test my device: all good is one green line", async () => {
    native();
    setToken("student", "S");
    vi.stubGlobal("fetch", vi.fn(async () => new Response(JSON.stringify({ ok: true }), { status: 200 })));
    render(<DeviceStatus readiness={{ native: true, platform: "mac", trusted: true }} />);
    send({ type: "preflight", problems: [], checks: [{ id: "a", ok: true, message: "A" }, { id: "b", ok: true, message: "B" }], platform: "mac" });
    expect(await screen.findByText("This Mac is ready")).toBeInTheDocument();
    expect(screen.getByText(/2 checks passed/)).toBeInTheDocument();
  });

  it("Test my device in a browser asks for the app", () => {
    render(<DeviceStatus readiness={{ native: false, platform: null, trusted: null }} />);
    expect(screen.getByText("Open the FairPlay app to test this computer.")).toBeInTheDocument();
  });

  it("names the platform the page runs in", () => {
    (window as unknown as { chrome: unknown }).chrome = { webview: { postMessage: vi.fn(), addEventListener: vi.fn() } };
    render(<ReadinessChecks readiness={{ native: true, platform: "windows", trusted: null }} />);
    expect(screen.getByText("Running in the FairPlay Windows app")).toBeInTheDocument();
    expect(screen.queryByText(/lock permission/i)).toBeNull();
    delete (window as unknown as { chrome?: unknown }).chrome;
  });

  it("teacher readiness list", async () => {
    setToken("teacher", "T");
    vi.stubGlobal("fetch", vi.fn(async () => new Response(JSON.stringify([
      { student_id: 1, name: "Alice", platform: "mac", app_version: "0.5", os_version: "15", ok: true, failed: [], checked_at: "2026-10-20T08:00:00.000Z" },
      { student_id: 2, name: "Bob", platform: "mac", app_version: "0.5", os_version: "15", ok: false, failed: ["displays"], checked_at: "2026-10-20T08:00:00.000Z" },
      { student_id: 3, name: "Carol", platform: null, app_version: null, os_version: null, ok: null, failed: null, checked_at: null },
    ]), { status: 200 })));
    render(<DeviceReadiness examId="e_1" />);
    await waitFor(() => expect(screen.getByText(/needs a fix: displays/)).toBeInTheDocument());
    expect(screen.getByText(/· not checked yet/)).toBeInTheDocument();
    expect(screen.getByText("2 students not ready or not checked yet.")).toBeInTheDocument();
  });
});
