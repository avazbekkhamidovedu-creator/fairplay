// 0.6.2 review: the server's "busy" 503 (admission line full) and a proxy's 504 are "try
// again later", like no network, so every caller's offline path handles them; the pack's
// server URL carries the pack version (immutable on the server).
import { afterEach, describe, expect, it, vi } from "vitest";
import { ApiError, NetworkError, request } from "../api/client";
import { EXPECTED_ASSETS_VERSION, SERVER_ASSET_BASE } from "../lib/appAssets";

afterEach(() => vi.unstubAllGlobals());

function reply(status: number, body: unknown) {
  vi.stubGlobal("fetch", vi.fn(async () => new Response(JSON.stringify(body), { status })));
}

describe("busy server", () => {
  it("503 and 504 become NetworkError", async () => {
    reply(503, { detail: "The server is busy, trying again" });
    await expect(request(null, "GET", "/api/x")).rejects.toBeInstanceOf(NetworkError);
    reply(504, {});
    await expect(request(null, "GET", "/api/x")).rejects.toBeInstanceOf(NetworkError);
  });

  it("other errors stay ApiError with their message", async () => {
    reply(502, { detail: "Canvas did not accept the access token" });
    const e = await request(null, "GET", "/api/x").catch((x) => x);
    expect(e).toBeInstanceOf(ApiError);
    expect((e as ApiError).message).toContain("Canvas");
  });
});

describe("server pack URL", () => {
  it("is versioned", () => {
    expect(SERVER_ASSET_BASE).toBe(`/app-assets/v/${EXPECTED_ASSETS_VERSION}/`);
  });
});
