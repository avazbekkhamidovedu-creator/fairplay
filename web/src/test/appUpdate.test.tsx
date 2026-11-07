// 2026-10-22: in-app update of the Mac app: the state's `update` field, the installUpdate
// action and the "Test my device" card line (available / updating / failed).
import { act, fireEvent, render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { afterEach, describe, expect, it, vi } from "vitest";

const api = vi.hoisted(() => ({
  trialInfo: vi.fn(),
  startTrial: vi.fn(),
  deviceCheck: vi.fn(),
}));
vi.mock("../api/client", async (importOriginal) => {
  const real = await importOriginal<typeof import("../api/client")>();
  return { ...real, studentApi: { ...real.studentApi, ...api } };
});

import { TrialCard } from "../components/student/TrialCard";
import * as bridge from "../lib/bridge";

const send = (detail: unknown) => window.dispatchEvent(new CustomEvent("proctora-native", { detail }));
const state = (extra: Record<string, unknown> = {}) => ({
  type: "state", locked: false, trusted: true, version: "0.5.3", app_version: "0.5.3", platform: "mac", ...extra,
});

/** A Mac app host that records what the page posts and answers hello / preflight. */
function macHost(update: unknown = { version: "0.5.4" }) {
  // null = the app has no update (no field in "state").
  const posted: Array<Record<string, unknown>> = [];
  const postMessage = vi.fn((msg: Record<string, unknown>) => {
    posted.push(msg);
    if (msg.action === "hello") setTimeout(() => send(state(update === null ? {} : { update })), 0);
    if (msg.action === "preflight") setTimeout(() => send({ type: "preflight", problems: [], checks: [], apps_to_close: [] }), 0);
  });
  (window as unknown as { webkit: unknown }).webkit = { messageHandlers: { proctora: { postMessage } } };
  return posted;
}

function card() {
  api.trialInfo.mockResolvedValue({ exam_id: "e_sample_exam", title: "Sample questions", duration_min: 10, question_count: 5, attempt: null });
  api.deviceCheck.mockResolvedValue({ ok: true });
  return render(
    <MemoryRouter>
      <TrialCard />
    </MemoryRouter>,
  );
}

afterEach(() => {
  vi.clearAllMocks();
  delete (window as unknown as { webkit?: unknown }).webkit;
  bridge._resetBridgeForTests();
});

describe("state.update parsing", () => {
  it("accepts only {version: X.Y.Z}", () => {
    expect(bridge.parseUpdate({ version: "0.5.4" })).toEqual({ version: "0.5.4" });
    expect(bridge.parseUpdate({ version: "0.8.1.1" })).toEqual({ version: "0.8.1.1" });
    expect(bridge.parseUpdate({ version: "10.0.12", extra: "x" })).toEqual({ version: "10.0.12" });
    for (const bad of [null, undefined, "0.5.4", 5, [], {}, { version: 5 }, { version: "0.5" }, { version: "v0.5.4" },
      { version: "0.5.4-beta" }, { version: "0.5.4\n" }, { version: "<b>1.2.3</b>" }, { version: "1".repeat(40) + ".0.0" }]) {
      expect(bridge.parseUpdate(bad)).toBeNull();
    }
  });

  it("puts a valid update into the native state and drops a bad one", () => {
    macHost();
    bridge.hello();
    send(state({ update: { version: "0.5.4" } }));
    expect(bridge.getNativeState()?.update).toEqual({ version: "0.5.4" });
    send(state({ update: { version: "0.5.4; rm -rf" } }));
    expect(bridge.getNativeState()?.update).toBeUndefined();
    send(state());
    expect(bridge.getNativeState()?.update).toBeUndefined();
  });

  it("failure reasons are a fixed list", () => {
    for (const r of ["download", "verify", "not_writable", "locked", "busy"]) expect(bridge.parseUpdateFailure(r)).toBe(r);
    expect(bridge.parseUpdateFailure("other")).toBe("unknown");
    expect(bridge.parseUpdateFailure(undefined)).toBe("unknown");
  });

  it("installUpdate and openDownloadPage post only inside the Mac app", () => {
    expect(bridge.installUpdate()).toBe(false);
    expect(bridge.openDownloadPage()).toBe(false);
    const posted = macHost();
    expect(bridge.installUpdate()).toBe(true);
    expect(bridge.openDownloadPage()).toBe(true);
    expect(posted).toEqual([{ action: "installUpdate" }, { action: "openDownloadPage" }]);
  });
});

describe("update line in the Test my device card", () => {
  it("is not shown without an update or in a browser", async () => {
    card();
    expect(await screen.findByRole("button", { name: "Try 5 sample questions" })).toBeTruthy();
    expect(screen.queryByTestId("update-notice")).toBeNull();
  });

  it("is not shown when the app has no update", async () => {
    macHost(null);
    card();
    expect(await screen.findByText(/This Mac is ready/)).toBeTruthy();
    expect(screen.queryByTestId("update-notice")).toBeNull();
  });

  it("offers Update now above the checks; the main button stays the sample questions", async () => {
    const posted = macHost();
    card();
    expect(await screen.findByText("FairPlay 0.5.4 is available")).toBeTruthy();
    const update = screen.getByRole("button", { name: "Update now" });
    expect(update.className).toBe("stu-btn sm");
    const main = screen.getByRole("button", { name: "Try 5 sample questions" });
    expect(main.className).toContain("primary");
    // The update line comes before the device status.
    const notice = screen.getByTestId("update-notice");
    const status = await screen.findByTestId("device-status");
    expect(notice.compareDocumentPosition(status) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();

    fireEvent.click(update);
    expect(posted.filter((m) => m.action === "installUpdate")).toHaveLength(1);
    expect(screen.getByText("Updating… FairPlay restarts by itself.")).toBeTruthy();
    expect(screen.queryByRole("button", { name: "Update now" })).toBeNull();
    act(() => send({ type: "updateProgress", stage: "downloading" }));
    expect(screen.getByText("Updating… FairPlay restarts by itself.")).toBeTruthy();
  });

  it("a failed download can be tried again", async () => {
    const posted = macHost();
    card();
    fireEvent.click(await screen.findByRole("button", { name: "Update now" }));
    act(() => send({ type: "updateFailed", reason: "download" }));
    expect(screen.getByRole("alert").textContent).toBe("Couldn't download the update. Check the connection and try again.");
    fireEvent.click(screen.getByRole("button", { name: "Update now" }));
    expect(posted.filter((m) => m.action === "installUpdate")).toHaveLength(2);
  });

  it("explains a refusal during an exam", async () => {
    macHost();
    card();
    fireEvent.click(await screen.findByRole("button", { name: "Update now" }));
    act(() => send({ type: "updateFailed", reason: "locked" }));
    expect(screen.getByRole("alert").textContent).toBe("Can't update during an exam.");
    expect(screen.queryByRole("button", { name: "Update now" })).toBeNull();
  });

  it("busy keeps showing the running update", async () => {
    macHost();
    card();
    fireEvent.click(await screen.findByRole("button", { name: "Update now" }));
    act(() => send({ type: "updateFailed", reason: "busy" }));
    expect(screen.getByText("Updating… FairPlay restarts by itself.")).toBeTruthy();
  });

  it.each(["not_writable", "verify", "something_new"])("%s points to the download page", async (reason) => {
    const posted = macHost();
    card();
    fireEvent.click(await screen.findByRole("button", { name: "Update now" }));
    act(() => send({ type: "updateFailed", reason }));
    expect(screen.getByRole("alert").textContent).toBe("Download the new version from the download page.");
    const link = screen.getByRole("link", { name: "download page" });
    expect(link.getAttribute("href")).toBe("https://github.com/avazbekkhamidovedu-creator/fairplay-download/releases/latest");
    fireEvent.click(link);
    expect(posted.some((m) => m.action === "openDownloadPage")).toBe(true);
  });
});

describe("Windows update answers", () => {
  it("maps the Windows reasons", async () => {
    const b = await import("../lib/bridge");
    expect(b.parseUpdateFailure("failed")).toBe("download");
    expect(b.parseUpdateFailure("not_installed")).toBe("not_writable");
    expect(b.parseUpdateFailure("no_update")).toBe("no_update");
    expect(b.parseUpdateFailure("weird")).toBe("unknown");
  });
});
