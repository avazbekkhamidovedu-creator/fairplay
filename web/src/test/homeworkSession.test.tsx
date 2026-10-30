import { act, render, screen } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import * as bridge from "../lib/bridge";
import { getHomeworkToken } from "../lib/homework";
import {
  LEGACY_HOMEWORK_TOKEN_KEY,
  MSG_CAP_WARNING,
  MSG_IDLE_WARNING,
  MSG_SIGNED_OUT,
  clearHomeworkSession,
  setHomeworkSession,
  takeSignedOutNotice,
} from "../lib/homeworkSession";
import HomeworkPage from "../pages/student/HomeworkPage";
import StudentLogin from "../pages/student/StudentLogin";

const MIN = 60_000;
let renewStatus: number;
let renews: string[];
let seq: number;
let capIso: string | null;

const iso = (ms: number) => new Date(Date.now() + ms).toISOString();
const session = (idleMin: number, capMin: number) => setHomeworkSession("T0", { expires_at: iso(idleMin * MIN), absolute_expires_at: iso(capMin * MIN) });
const advance = (ms: number) => act(async () => void (await vi.advanceTimersByTimeAsync(ms)));
const touch = () => act(() => void window.dispatchEvent(new Event("pointerdown")));

function native() {
  (window as unknown as { webkit: unknown }).webkit = { messageHandlers: { proctora: { postMessage: vi.fn() } } };
  bridge.onNative(() => undefined);
  window.dispatchEvent(new CustomEvent("proctora-native", { detail: { type: "state", locked: false, trusted: true, version: "1" } }));
}

function renderPage() {
  native();
  return render(
    <MemoryRouter initialEntries={["/student/homework"]}>
      <Routes>
        <Route path="/student/homework" element={<HomeworkPage />} />
        <Route path="/student/login" element={<StudentLogin />} />
      </Routes>
    </MemoryRouter>,
  );
}

beforeEach(() => {
  vi.useFakeTimers();
  renewStatus = 200;
  renews = [];
  seq = 0;
  capIso = null;
  bridge._resetBridgeForTests();
  clearHomeworkSession();
  takeSignedOutNotice();
  vi.stubGlobal(
    "fetch",
    vi.fn(async (url: string, init?: RequestInit) => {
      if (url === "/api/homework/me") return new Response(JSON.stringify({ name: "Ava K", netid: "ak1" }), { status: 200 });
      if (url === "/api/homework/session/renew") {
        renews.push((init?.headers as Record<string, string>).Authorization);
        if (renewStatus !== 200) return new Response("{}", { status: renewStatus });
        seq += 1;
        return new Response(JSON.stringify({ token: `T${seq}`, expires_at: iso(30 * MIN), absolute_expires_at: capIso ?? iso(200 * MIN) }), { status: 200 });
      }
      return new Response("{}", { status: 404 });
    }),
  );
});

afterEach(() => {
  delete (window as unknown as { webkit?: unknown }).webkit;
  vi.useRealTimers();
  vi.unstubAllGlobals();
});

describe("homework token storage", () => {
  it("keeps the token out of localStorage and sessionStorage", () => {
    setHomeworkSession("SECRET-TOK");
    expect(getHomeworkToken()).toBe("SECRET-TOK");
    for (const store of [localStorage, sessionStorage]) {
      expect(Object.keys(store).filter((k) => store.getItem(k)?.includes("SECRET-TOK"))).toEqual([]);
    }
  });

  it("deletes the old localStorage entry on load", async () => {
    localStorage.setItem(LEGACY_HOMEWORK_TOKEN_KEY, "OLD");
    vi.resetModules();
    await import("../lib/homeworkSession");
    expect(localStorage.getItem(LEGACY_HOMEWORK_TOKEN_KEY)).toBeNull();
  });
});

describe("renew timing", () => {
  it("does not renew while there is more than 20 minutes left", async () => {
    session(29, 240);
    renderPage();
    await advance(1000);
    touch();
    await advance(MIN);
    expect(renews).toEqual([]);
  });

  it("renews once active with under 20 minutes left and swaps the token", async () => {
    session(30, 240);
    renderPage();
    await advance(11 * MIN);
    expect(renews).toEqual([]);
    touch();
    await advance(2000);
    expect(renews).toEqual(["Bearer T0"]);
    expect(getHomeworkToken()).toBe("T1");
  });

  it("does not renew an idle student, and not twice within 5 minutes", async () => {
    session(30, 240);
    renderPage();
    await advance(15 * MIN);
    expect(renews).toEqual([]);
    touch();
    await advance(2000);
    expect(renews).toHaveLength(1);
    await advance(20 * MIN);
    touch();
    await advance(2000);
    expect(renews).toHaveLength(2);
    touch();
    await advance(2000);
    expect(renews).toHaveLength(2);
  });
});

describe("warnings and sign-out", () => {
  it("warns 2 minutes before the idle expiry and activity clears it", async () => {
    session(30, 240);
    renderPage();
    await advance(27 * MIN);
    expect(screen.queryByTestId("session-warning")).toBeNull();
    await advance(MIN + 1000);
    expect(screen.getByTestId("session-warning").textContent).toBe(MSG_IDLE_WARNING);
    touch();
    await advance(2000);
    expect(renews).toHaveLength(1);
    expect(screen.queryByTestId("session-warning")).toBeNull();
  });

  it("signs out at the idle expiry and explains it on the code screen", async () => {
    session(30, 240);
    renderPage();
    await advance(31 * MIN);
    expect(getHomeworkToken()).toBeNull();
    expect(screen.getByText(MSG_SIGNED_OUT)).toBeTruthy();
  });

  it("signs out when the renew answers 401", async () => {
    renewStatus = 401;
    session(30, 240);
    renderPage();
    await advance(11 * MIN);
    touch();
    await advance(2000);
    expect(getHomeworkToken()).toBeNull();
    expect(screen.getByText(MSG_SIGNED_OUT)).toBeTruthy();
  });

  it("warns 5 minutes before the 4-hour cap and signs out at it", async () => {
    session(30, 20);
    capIso = iso(20 * MIN);
    renderPage();
    await advance(14 * MIN);
    touch();
    await advance(2000);
    expect(getHomeworkToken()).toBe("T1");
    await advance(MIN);
    expect(screen.getByTestId("session-warning").textContent).toBe(MSG_CAP_WARNING);
    await advance(6 * MIN);
    expect(getHomeworkToken()).toBeNull();
  });
});
