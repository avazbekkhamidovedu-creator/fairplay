import { act, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { DeviceCheckDialog } from "../components/code-signin/DeviceCheckDialog";
import * as bridge from "../lib/bridge";
import DeviceTestPage from "../pages/student/DeviceTestPage";

const send = (detail: unknown) => act(() => void window.dispatchEvent(new CustomEvent("proctora-native", { detail })));
let post: ReturnType<typeof vi.fn>;

function native() {
  post = vi.fn();
  (window as unknown as { webkit: unknown }).webkit = { messageHandlers: { proctora: { postMessage: post } } };
  bridge.hello();
}

const REPORT = { problems: [], apps_to_close: [], checks: [], platform: "mac", app_version: "1", os_version: "15" };

function dialog() {
  return render(
    <MemoryRouter initialEntries={["/student/login"]}>
      <Routes>
        <Route
          path="/student/login"
          element={<DeviceCheckDialog state={{ kind: "done", report: REPORT }} onAgain={() => {}} onClose={() => {}} />}
        />
        <Route path="/student/device-test" element={<p>Preview page</p>} />
      </Routes>
    </MemoryRouter>,
  );
}

function page(state?: unknown) {
  return render(
    <MemoryRouter initialEntries={[{ pathname: "/student/device-test", state }]}>
      <Routes>
        <Route path="/student/device-test" element={<DeviceTestPage />} />
        <Route path="/student/login" element={<p>Login screen</p>} />
      </Routes>
    </MemoryRouter>,
  );
}

const BUTTON = /Try the exam screen/;

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

describe("device test button", () => {
  it("is hidden without deviceTest and shown with it", async () => {
    send({ type: "state", locked: false, trusted: true, version: "1" });
    const { unmount } = dialog();
    expect(screen.queryByRole("button", { name: BUTTON })).toBeNull();
    unmount();
    send({ type: "state", locked: false, trusted: true, version: "1", deviceTest: true });
    dialog();
    expect(screen.getByRole("button", { name: BUTTON })).toBeInTheDocument();
  });

  it("posts deviceTestStart(60) and opens the page when the app says started", async () => {
    send({ type: "state", locked: false, trusted: true, version: "1", deviceTest: true });
    dialog();
    await userEvent.click(screen.getByRole("button", { name: BUTTON }));
    expect(post).toHaveBeenCalledWith({ action: "deviceTestStart", seconds: 60 });
    send({ type: "deviceTest", state: "started", remaining: 60 });
    expect(screen.getByText("Preview page")).toBeInTheDocument();
  });

  it("opens the page after 1.5 s when no answer comes", async () => {
    send({ type: "state", locked: false, trusted: true, version: "1", deviceTest: true });
    dialog();
    vi.useFakeTimers({ shouldAdvanceTime: true });
    await userEvent.click(screen.getByRole("button", { name: BUTTON }));
    await act(async () => void vi.advanceTimersByTime(1600));
    expect(screen.getByText("Preview page")).toBeInTheDocument();
  });

  it("shows one line and stays when refused", async () => {
    send({ type: "state", locked: false, trusted: true, version: "1", deviceTest: true });
    dialog();
    await userEvent.click(screen.getByRole("button", { name: BUTTON }));
    send({ type: "deviceTest", state: "ended", reason: "refused", remaining: 0 });
    expect(screen.getByText("The test can't start right now.")).toBeInTheDocument();
    expect(screen.queryByText("Preview page")).toBeNull();
  });
});

describe("device test page", () => {
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
    expect(screen.getByText("Login screen")).toBeInTheDocument();
  });

  it("goes back to login when the app says ended", () => {
    page({ remaining: 30 });
    send({ type: "deviceTest", state: "ended", reason: "timeout", remaining: 0 });
    expect(screen.getByText("Login screen")).toBeInTheDocument();
  });

  it("goes back to login when the countdown reaches 0", async () => {
    vi.useFakeTimers();
    page({ remaining: 2 });
    await act(async () => void vi.advanceTimersByTime(2100));
    expect(screen.getByText("Login screen")).toBeInTheDocument();
  });

  it("never calls fetch and keeps answers in memory", async () => {
    page({ remaining: 30 });
    await userEvent.click(screen.getByText("4"));
    await userEvent.click(screen.getByRole("button", { name: "Leave" }));
    expect(fetch).not.toHaveBeenCalled();
  });

  it("renders nothing without a bridge", () => {
    delete (window as unknown as { webkit?: unknown }).webkit;
    const { container } = page({ remaining: 30 });
    expect(container).toBeEmptyDOMElement();
  });
});
