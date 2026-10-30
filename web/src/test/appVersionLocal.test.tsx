import { describe, expect, it } from "vitest";
import { appVersionLabel, isLocalServer } from "../components/AppVersion";
import type { NativeState } from "../lib/bridge";

const state = { locked: false, trusted: true, version: "0.7.8", app_version: "0.7.8", platform: "mac" } as NativeState;

describe("local app label", () => {
  it.each([
    ["localhost", true],
    ["127.0.0.1", true],
    ["127.8.8.8", true],
    ["10.1.2.3", true],
    ["192.168.1.20", true],
    ["172.16.0.5", true],
    ["172.31.255.1", true],
    ["my-mac.local", true],
    ["dku.speakupai.uz", false],
    ["exam.example.edu", false],
    ["172.32.0.1", false],
    ["172.15.0.1", false],
    ["8.8.8.8", false],
    ["192.169.1.1", false],
    ["localhost.evil.com", false],
  ])("isLocalServer(%s) is %s", (host, expected) => {
    expect(isLocalServer(host)).toBe(expected);
  });

  it("adds 'local app' next to the version on a local server", () => {
    expect(appVersionLabel(state, true, true)).toBe("v0.7.8 · Mac · local app");
    expect(appVersionLabel(state, false, true)).toBe("FairPlay 0.7.8 · Mac · local app");
  });

  it("leaves the label alone on a real server", () => {
    expect(appVersionLabel(state, true, false)).toBe("v0.7.8 · Mac");
    expect(appVersionLabel(state, false, false)).toBe("FairPlay 0.7.8 · Mac");
  });

  it("still shows nothing without a version", () => {
    expect(appVersionLabel({ ...state, app_version: undefined }, true, true)).toBeNull();
  });
});
