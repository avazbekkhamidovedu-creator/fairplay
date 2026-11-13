import { act, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import NativeHome from "../pages/student/NativeHome";
import * as bridge from "../lib/bridge";
import DeviceTestPage from "../pages/student/DeviceTestPage";

const send = (detail: unknown) => act(() => void window.dispatchEvent(new CustomEvent("proctora-native", { detail })));
let post: ReturnType<typeof vi.fn>;

function native() {
  post = vi.fn();
  (window as unknown as { webkit: unknown }).webkit = { messageHandlers: { proctora: { postMessage: post } } };
  bridge.hello();
}

function dialog() {
  vi.stubGlobal("fetch", vi.fn(async () => ({ ok: false })));
  return render(
    <MemoryRouter initialEntries={["/student/login"]}>
      <Routes>
        <Route path="/student/login" element={<NativeHome onUseAccount={() => {}} />} />
        <Route path="/student/device-test" element={<p>Preview page</p>} />
      </Routes>
    </MemoryRouter>,
  );
}

function page(state?: unknown, search = "", confirmed = true) {
  bridge.setDeviceTestConfirmed(confirmed);
  return render(
    <MemoryRouter initialEntries={[{ pathname: "/student/device-test", search, state }]}>
      <Routes>
        <Route path="/student/device-test" element={<DeviceTestPage />} />
        <Route path="/student/home" element={<p>App home</p>} />
        <Route path="/student/login" element={<p>App home</p>} />
      </Routes>
    </MemoryRouter>,
  );
}

const BUTTON = /^Start$/;
const ENTRY = { name: "Test my device" };

async function open() {
  await userEvent.click(screen.getByRole("button", ENTRY));
}

beforeEach(() => {
  native();
  vi.stubGlobal("fetch", vi.fn());
});

afterEach(() => {
  delete (window as unknown as { webkit?: unknown }).webkit;
  bridge._resetBridgeForTests();
  vi.unstubAllGlobals();
  vi.useRealTimers();
});

describe("device test start", () => {
  it("starts the test at once when the app reports deviceTest: no Start button, no apps to close", async () => {
    send({ type: "state", locked: false, trusted: true, version: "1", deviceTest: true });
    dialog();
    await open();
    expect(post).toHaveBeenCalledWith({ action: "deviceTestStart", seconds: 60 });
    expect(screen.queryByRole("button", { name: BUTTON })).toBeNull();
    expect(screen.queryByRole("dialog", { name: "Test my device" })).toBeNull();
    expect(screen.queryByLabelText("Device check results")).toBeNull();
    expect(post.mock.calls.some(([m]) => m.action === "quitApps" || m.action === "preflight")).toBe(false);
  });

  it("falls back to the device check dialog without deviceTest", async () => {
    send({ type: "state", locked: false, trusted: true, version: "1" });
    dialog();
    await open();
    expect(await screen.findByRole("dialog", { name: "Test my device" })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: BUTTON })).toBeNull();
    expect(post.mock.calls.some(([m]) => m.action === "quitApps")).toBe(false);
  });

  it("Cancel closes the dialog while it is starting", async () => {
    send({ type: "state", locked: false, trusted: true, version: "1", deviceTest: true });
    dialog();
    await open();
    await userEvent.click(screen.getByRole("button", { name: "Cancel" }));
    expect(screen.queryByRole("dialog")).toBeNull();
  });

  it("opens the page when the app says started", async () => {
    send({ type: "state", locked: false, trusted: true, version: "1", deviceTest: true });
    dialog();
    await open();
    send({ type: "deviceTest", state: "started", remaining: 60 });
    expect(screen.getByText("Preview page")).toBeInTheDocument();
  });

  it("shows the line and stays when no answer comes in 1.5 s", async () => {
    send({ type: "state", locked: false, trusted: true, version: "1", deviceTest: true });
    dialog();
    vi.useFakeTimers({ shouldAdvanceTime: true });
    await open();
    await act(async () => void vi.advanceTimersByTime(1600));
    expect(screen.getByText("The test can't start right now.")).toBeInTheDocument();
    expect(screen.queryByText("Preview page")).toBeNull();
    expect(post).toHaveBeenCalledWith({ action: "deviceTestLeave" });
  });

  it("leaves at once when a late started arrives after the timeout", async () => {
    send({ type: "state", locked: false, trusted: true, version: "1", deviceTest: true });
    dialog();
    vi.useFakeTimers({ shouldAdvanceTime: true });
    await open();
    await act(async () => void vi.advanceTimersByTime(1600));
    post.mockClear();
    send({ type: "deviceTest", state: "started", remaining: 59 });
    expect(post).toHaveBeenCalledWith({ action: "deviceTestLeave" });
    expect(screen.queryByText("Preview page")).toBeNull();
  });

  it("ignores an ended from an earlier test and still opens the page on started", async () => {
    send({ type: "state", locked: false, trusted: true, version: "1", deviceTest: true });
    dialog();
    await open();
    send({ type: "deviceTest", state: "ended", reason: "timeout", remaining: 0 });
    expect(screen.queryByText("The test can't start right now.")).toBeNull();
    send({ type: "deviceTest", state: "started", remaining: 60 });
    expect(screen.getByText("Preview page")).toBeInTheDocument();
  });

  it("shows one line and offers Try again when refused", async () => {
    send({ type: "state", locked: false, trusted: true, version: "1", deviceTest: true });
    dialog();
    await open();
    send({ type: "deviceTest", state: "ended", reason: "refused", remaining: 0 });
    expect(screen.getByText("The test can't start right now.")).toBeInTheDocument();
    expect(screen.queryByText("Preview page")).toBeNull();
    post.mockClear();
    await userEvent.click(screen.getByRole("button", { name: "Try again" }));
    expect(post).toHaveBeenCalledWith({ action: "deviceTestStart", seconds: 60 });
  });
});

describe("device test page", () => {
  it("native entry starts the existing preview once after the question page mounts", () => {
    page(undefined, "?nativeEntry=1");
    expect(post.mock.calls.filter(([message]) => message.action === "deviceTestStart")).toEqual([
      [{ action: "deviceTestStart", seconds: 60 }],
    ]);
    expect(screen.getByRole("button", { name: "Leave" })).toBeInTheDocument();
  });
  it("renders four sample questions and the banner with the app's remaining time", async () => {
    page({ remaining: 58 });
    expect(screen.getByText("Preview: nothing here is saved or graded")).toBeInTheDocument();
    expect(screen.getByText("Sample questions")).toBeInTheDocument();
    expect(screen.getByText("Leaves automatically in 0:58")).toBeInTheDocument();
    expect(screen.getByText("2 + 2 = ?")).toBeInTheDocument();
    for (const o of ["3", "4", "5", "22"]) expect(screen.getByText(o)).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Question 1 of 4" }));
    expect(screen.getAllByRole("button", { name: /^Question \d, / })).toHaveLength(4);
    await userEvent.click(screen.getByRole("button", { name: "Next" }));
    await userEvent.click(screen.getByRole("button", { name: "Next" }));
    await userEvent.click(screen.getByRole("button", { name: "Next" }));
    expect(screen.getByRole("textbox")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Next" })).toBeDisabled();
  });

  it("counts down from 60 when no remaining is known and follows the app's remaining", () => {
    page();
    expect(screen.getByText("Leaves automatically in 1:00")).toBeInTheDocument();
    send({ type: "deviceTest", state: "started", remaining: 42 });
    expect(screen.getByText("Leaves automatically in 0:42")).toBeInTheDocument();
  });

  it("Leave posts deviceTestLeave and goes to the login screen", async () => {
    page({ remaining: 30 });
    await userEvent.click(screen.getByRole("button", { name: "Leave" }));
    expect(post).toHaveBeenCalledWith({ action: "deviceTestLeave" });
    expect(screen.getByText("App home")).toBeInTheDocument();
  });

  it("goes back to login when the app says ended", () => {
    page({ remaining: 30 });
    send({ type: "deviceTest", state: "ended", reason: "timeout", remaining: 0 });
    expect(screen.getByText("App home")).toBeInTheDocument();
  });

  it("goes back to login when the countdown reaches 0", async () => {
    vi.useFakeTimers();
    page({ remaining: 2 });
    await act(async () => void vi.advanceTimersByTime(2100));
    expect(screen.getByText("App home")).toBeInTheDocument();
    expect(post).toHaveBeenCalledWith({ action: "deviceTestLeave" });
  });

  it("shows a block screen instead of a countdown on a direct visit or refresh", async () => {
    page({ remaining: 60 }, "", false);
    expect(screen.getByTestId("device-test-missing")).toBeInTheDocument();
    expect(screen.queryByTestId("device-test-countdown")).toBeNull();
    expect(post).not.toHaveBeenCalledWith({ action: "deviceTestStart", seconds: 60 });
    await userEvent.click(screen.getByRole("button", { name: "Back to home" }));
    expect(screen.getByText("App home")).toBeInTheDocument();
  });

  it("never calls fetch and keeps answers in memory", async () => {
    page({ remaining: 30 });
    await userEvent.click(screen.getByText("4"));
    await userEvent.click(screen.getByRole("button", { name: "Leave" }));
    expect(fetch).not.toHaveBeenCalled();
  });

  it("goes to the login screen without a bridge", () => {
    delete (window as unknown as { webkit?: unknown }).webkit;
    page({ remaining: 30 });
    expect(screen.getByText("App home")).toBeInTheDocument();
  });
});
