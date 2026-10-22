// 2026-10-23: the laptop battery travels with the heartbeat; the live room warns under 20 %.
import { afterEach, describe, expect, it, vi } from "vitest";
import { studentApi } from "../api/client";
import { lowBatteryBadge } from "../pages/teacher/LiveRoom";

describe("low battery in the live room", () => {
  it("warns only for an online, not charging laptop under 20 %", () => {
    expect(lowBatteryBadge({ battery: 0.14, charging: false, online: true })).toBe("Battery 14% · not charging");
    expect(lowBatteryBadge({ battery: 0.14, charging: true, online: true })).toBeNull();
    expect(lowBatteryBadge({ battery: 0.5, charging: false, online: true })).toBeNull();
    expect(lowBatteryBadge({ battery: 0.14, charging: false, online: false })).toBeNull();
    expect(lowBatteryBadge({ battery: null, charging: null, online: true })).toBeNull();
    expect(lowBatteryBadge({ online: true })).toBeNull();
  });
});

describe("heartbeat body", () => {
  afterEach(() => vi.unstubAllGlobals());
  it("adds the battery only when it is known", async () => {
    const bodies: unknown[] = [];
    vi.stubGlobal(
      "fetch",
      vi.fn(async (_url: string, init: RequestInit) => {
        bodies.push(JSON.parse(String(init.body)));
        return new Response(JSON.stringify({ ok: true, status: "in_progress" }), { status: 200, headers: { "content-type": "application/json" } });
      }),
    );
    await studentApi.heartbeat("a_1", 3, true, { level: 0.1449, charging: false });
    await studentApi.heartbeat("a_1", 3, true, null);
    await studentApi.heartbeat("a_1", 3, true, { level: 7, charging: false });
    expect(bodies[0]).toEqual({ current_question: 3, locked: true, battery: 0.14, charging: false });
    expect(bodies[1]).toEqual({ current_question: 3, locked: true });
    expect(bodies[2]).toEqual({ current_question: 3, locked: true });
  });
});
