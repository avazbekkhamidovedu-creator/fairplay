// S4 (2026-10-20): start inside the app turns 409 swap_needed into SwapNeededError with the
// app's device id (from the signed start proof).
import { afterEach, describe, expect, it, vi } from "vitest";
import { SwapNeededError, setToken, studentApi } from "../api/client";
import * as bridge from "../lib/bridge";

const send = (detail: unknown) => window.dispatchEvent(new CustomEvent("proctora-native", { detail }));

describe("start on a second computer", () => {
  afterEach(() => {
    delete (window as unknown as { webkit?: unknown }).webkit;
    bridge._resetBridgeForTests();
    vi.unstubAllGlobals();
  });

  it("throws SwapNeededError carrying the device id", async () => {
    const postMessage = vi.fn((m: { action: string; nonce?: string }) => {
      if (m.action === "attest")
        setTimeout(() => send({ type: "attestation", nonce: m.nonce, device_id: "hw-bbbb2222", signature: "s", version: "0.5", build: "1" }), 0);
    });
    (window as unknown as { webkit: unknown }).webkit = { messageHandlers: { proctora: { postMessage } } };
    setToken("student", "S");
    vi.stubGlobal("fetch", vi.fn(async (url: string) =>
      url.endsWith("/challenge")
        ? new Response(JSON.stringify({ nonce: "n-12345678" }), { status: 200 })
        : new Response(JSON.stringify({ detail: { code: "swap_needed", message: "open on another computer" } }), { status: 409 }),
    ));
    const err = await studentApi.start("e_1", "123456").catch((e: unknown) => e);
    expect(err).toBeInstanceOf(SwapNeededError);
    expect((err as SwapNeededError).deviceId).toBe("hw-bbbb2222");
    expect((err as SwapNeededError).detail).toBe("open on another computer");
  });
});
