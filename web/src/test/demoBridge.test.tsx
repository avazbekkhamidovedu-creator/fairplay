import { render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import App from "../App";
import * as bridge from "../lib/bridge";
import { isBrowserDemo } from "../lib/demoBridge";

const loaded = vi.hoisted(() => ({ routes: 0 }));

vi.mock("../pages/student/routes", () => {
  loaded.routes += 1;
  return { default: () => <p>student screens</p> };
});

function health(body: unknown) {
  vi.stubGlobal("fetch", vi.fn(async () => new Response(JSON.stringify(body), { status: 200 })));
}

const hasBridge = () => !!(window as unknown as { webkit?: unknown }).webkit;

function renderAt(path: string) {
  return render(
    <MemoryRouter initialEntries={[path]}>
      <App />
    </MemoryRouter>,
  );
}

beforeEach(() => {
  loaded.routes = 0;
});

afterEach(() => {
  delete (window as unknown as { webkit?: unknown }).webkit;
  bridge._resetBridgeForTests();
  vi.unstubAllGlobals();
});

describe("browser demo", () => {
  it("only a health answer with browser_demo exactly true counts", () => {
    expect(isBrowserDemo({ ok: true, browser_demo: true })).toBe(true);
    for (const h of [null, undefined, {}, { ok: true }, { browser_demo: false }, { browser_demo: "true" }, { browser_demo: 1 }, "x"])
      expect(isBrowserDemo(h)).toBe(false);
  });

  it("a server in demo mode shows the student screens in a plain browser", async () => {
    health({ ok: true, demo_logins: true, features: [], browser_demo: true });
    renderAt("/student/login");
    expect(await screen.findByText("student screens")).toBeInTheDocument();
    expect(bridge.isNative()).toBe(true);
  });

  it.each([
    ["a production health answer", { ok: true, demo_logins: false, features: [] }],
    ["an answer that says false", { ok: true, browser_demo: false }],
    ["an answer with the flag as text", { ok: true, browser_demo: "true" }],
  ])("%s never renders the student routes or installs the stand-in", async (_n, body) => {
    health(body);
    const { container } = renderAt("/student/login");
    await waitFor(() => expect(fetch).toHaveBeenCalledWith("/api/health"));
    await new Promise((r) => setTimeout(r, 30));
    expect(container.innerHTML).toBe("");
    expect(loaded.routes).toBe(0);
    expect(hasBridge()).toBe(false);
    expect(bridge.isNative()).toBe(false);
  });

  it("a failing health call leaves the browser empty", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => { throw new Error("down"); }));
    const { container } = renderAt("/student");
    await new Promise((r) => setTimeout(r, 30));
    expect(container.innerHTML).toBe("");
    expect(hasBridge()).toBe(false);
  });

  it("never replaces a real bridge", async () => {
    const real = { messageHandlers: { proctora: { postMessage: vi.fn() } } };
    (window as unknown as { webkit: unknown }).webkit = real;
    health({ ok: true, browser_demo: true });
    renderAt("/student/login");
    await screen.findByText("student screens");
    expect((window as unknown as { webkit: unknown }).webkit).toBe(real);
  });

  it("the stand-in reports its version and platform and answers the device check", async () => {
    health({ ok: true, browser_demo: true });
    renderAt("/student/login");
    await screen.findByText("student screens");
    await waitFor(() => expect(bridge.getNativeState()?.app_version).toBe("0.7.4-demo"));
    expect(bridge.getNativeState()).toMatchObject({ platform: "mac", trusted: true, locked: false });
    expect(bridge.nativePlatform()).toBe("mac");
    const report = await bridge.preflightReport(1000);
    expect(report).toMatchObject({ problems: [], platform: "mac", app_version: "0.7.4-demo", apps_to_close: [] });
    expect(report!.checks.length).toBeGreaterThanOrEqual(4);
    expect(report!.checks.filter((c) => !c.ok)).toEqual([expect.objectContaining({ info: true })]);
  });

  it("the stand-in signs a start challenge with a device id and locks and unlocks on request", async () => {
    health({ ok: true, browser_demo: true });
    renderAt("/student/login");
    await screen.findByText("student screens");
    const proof = await bridge.attest("NONCE-12345678");
    expect(proof).toMatchObject({ nonce: "NONCE-12345678", device_id: "DEMO-BROWSER-0001" });
    expect(proof!.signature).toMatch(/^[0-9a-f]{64}$/);
    expect(await bridge.requestLock(1000, { attempt_id: "a", session: "s", token: "t" })).toEqual({ ok: true });
    expect(bridge.isLocked()).toBe(true);
    bridge.unlock();
    await waitFor(() => expect(bridge.isLocked()).toBe(false));
  });
});
