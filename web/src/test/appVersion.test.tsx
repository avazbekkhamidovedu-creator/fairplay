// S2 (2026-10-20): app version/platform headers from the bridge state, 426 update screen.
import { act, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { ApiError, UPGRADE_EVENT, setUnauthorizedHandler, studentApi } from "../api/client";
import { UpdateRequired, useUpgradeRequired } from "../components/UpdateRequired";
import * as bridge from "../lib/bridge";

function native() {
  const postMessage = vi.fn();
  (window as unknown as { webkit: unknown }).webkit = { messageHandlers: { proctora: { postMessage } } };
  return postMessage;
}
const send = (detail: unknown) => window.dispatchEvent(new CustomEvent("proctora-native", { detail }));

function mockFetch(status: number, body: unknown) {
  const fn = vi.fn(async () => new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } }));
  vi.stubGlobal("fetch", fn);
  return fn;
}
const headersOf = (fn: ReturnType<typeof mockFetch>) =>
  (fn.mock.calls[0] as unknown as [string, RequestInit])[1].headers as Record<string, string>;

describe("app version headers (S2)", () => {
  afterEach(() => {
    delete (window as unknown as { webkit?: unknown }).webkit;
    bridge._resetBridgeForTests();
    vi.unstubAllGlobals();
  });

  it("sends no app headers in a normal browser", async () => {
    const f = mockFetch(200, []);
    await studentApi.listExams();
    expect(headersOf(f)["X-FairPlay-Version"]).toBeUndefined();
    expect(bridge.appHeaders()).toEqual({});
  });

  it("adds version and platform once the app's state reported both", async () => {
    native();
    bridge.hello();
    send({ type: "state", locked: false, trusted: true, version: "0.5 (12)" });
    expect(bridge.appHeaders()).toEqual({});     // older app: no app_version / platform yet
    send({ type: "state", locked: false, trusted: true, version: "0.5", app_version: "0.5", platform: "mac" });
    expect(bridge.getNativeState()).toMatchObject({ app_version: "0.5", platform: "mac" });
    const f = mockFetch(200, []);
    await studentApi.listExams();
    expect(headersOf(f)).toMatchObject({ "X-FairPlay-Version": "0.5", "X-FairPlay-Platform": "mac" });
  });

  it("a 426 carries the minimum and announces the update screen", async () => {
    setUnauthorizedHandler(vi.fn());
    mockFetch(426, { detail: "Update FairPlay to continue", min_app_version: "0.6" });
    const seen = vi.fn();
    window.addEventListener(UPGRADE_EVENT, seen);
    const err = await studentApi.listExams().catch((e: unknown) => e);
    window.removeEventListener(UPGRADE_EVENT, seen);
    expect(err).toBeInstanceOf(ApiError);
    expect((err as ApiError).status).toBe(426);
    expect((err as ApiError).minAppVersion).toBe("0.6");
    expect((err as ApiError).detail).toBe("Update FairPlay to continue");
    expect(seen).toHaveBeenCalledTimes(1);
  });

  it("shows the full-page update screen with the minimum version", () => {
    function Host() {
      const min = useUpgradeRequired();
      return min === null ? <p>exam page</p> : <UpdateRequired minVersion={min} />;
    }
    render(<Host />);
    expect(screen.getByText("exam page")).toBeInTheDocument();
    act(() => {
      window.dispatchEvent(new CustomEvent(UPGRADE_EVENT, { detail: { min_app_version: "0.7" } }));
    });
    expect(screen.getByTestId("update-required")).toHaveTextContent("Update FairPlay");
    expect(screen.getByTestId("update-required")).toHaveTextContent("0.7 or newer");
  });
});
