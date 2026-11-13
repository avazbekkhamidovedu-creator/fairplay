import { act, render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { setToken } from "../api/client";
import { _resetUpdatePromptForTests } from "../components/code-signin/UpdatePrompt";
import * as bridge from "../lib/bridge";
import NativeHome from "../pages/student/NativeHome";
import StudentHome from "../pages/student/StudentHome";

const send = (detail: unknown) => act(() => void window.dispatchEvent(new CustomEvent("proctora-native", { detail })));

let postMessage: ReturnType<typeof vi.fn>;
function tick(ms: number) {
  for (let t = 0; t < ms; t += 1000) act(() => void vi.advanceTimersByTime(1000));
}
const installs = () => postMessage.mock.calls.filter((c) => (c[0] as { action: string }).action === "installUpdate").length;

function native(update?: { version: string }) {
  bridge.onNative(() => {});
  postMessage = vi.fn();
  (window as unknown as { webkit: unknown }).webkit = { messageHandlers: { proctora: { postMessage } } };
  send({ type: "state", locked: false, trusted: true, version: "1", ...(update ? { update } : {}) });
}

const renderSignIn = () =>
  render(
    <MemoryRouter>
      <NativeHome onUseAccount={() => {}} />
    </MemoryRouter>,
  );

beforeEach(() => {
  vi.useFakeTimers();
  _resetUpdatePromptForTests();
  vi.stubGlobal("fetch", vi.fn(async () => new Response("[]", { status: 200 })));
});

afterEach(() => {
  vi.useRealTimers();
  delete (window as unknown as { webkit?: unknown }).webkit;
  bridge._resetBridgeForTests();
  localStorage.clear();
  vi.unstubAllGlobals();
});

describe("update prompt on the app home", () => {
  it("counts down 5 s, then installs exactly once", () => {
    native({ version: "0.7.6" });
    renderSignIn();
    expect(screen.getByTestId("update-notice")).toHaveTextContent("FairPlay 0.7.6 is available. Updating in 5 s…");
    tick(2000);
    expect(screen.getByTestId("update-notice")).toHaveTextContent("Updating in 3 s");
    expect(installs()).toBe(0);
    tick(3000);
    expect(installs()).toBe(1);
    expect(screen.getByTestId("update-notice")).toHaveTextContent("Updating FairPlay… the app will reopen");
    expect(screen.getByRole("button", { name: "Sign in with username and password" })).toBeDisabled();
    tick(10000);
    expect(installs()).toBe(1);
  });

  it("Update now installs at once", () => {
    native({ version: "0.7.6" });
    renderSignIn();
    act(() => screen.getByRole("button", { name: "Update now" }).click());
    expect(installs()).toBe(1);
    tick(6000);
    expect(installs()).toBe(1);
  });

  it("Not now stops it and hides the notice", () => {
    native({ version: "0.7.6" });
    renderSignIn();
    act(() => screen.getByRole("button", { name: "Not now" }).click());
    tick(10000);
    expect(installs()).toBe(0);
    expect(screen.queryByTestId("update-notice")).not.toBeInTheDocument();
  });

  it("updateFailed shows the message and a manual button, sign-in button stays usable", () => {
    native({ version: "0.7.6" });
    renderSignIn();
    tick(5000);
    send({ type: "updateFailed", reason: "not_installed" });
    const n = screen.getByTestId("update-notice");
    expect(n).toHaveTextContent("The update could not be installed. You can keep using this version.");
    expect(n).toHaveTextContent("not installed with the installer");
    expect(screen.getByRole("button", { name: "Sign in with username and password" })).toBeEnabled();
    act(() => screen.getByRole("button", { name: "Update now" }).click());
    expect(installs()).toBe(2);
  });

  it("no answer within 60 s counts as a failure", () => {
    native({ version: "0.7.6" });
    renderSignIn();
    tick(5000);
    send({ type: "updateProgress", stage: "downloading" });
    tick(60000);
    expect(screen.getByTestId("update-notice")).toHaveTextContent("could not be installed");
    expect(screen.getByRole("button", { name: "Sign in with username and password" })).toBeEnabled();
  });

  it("shows nothing without an update, and nothing in a plain browser", () => {
    native();
    const { unmount } = renderSignIn();
    expect(screen.queryByTestId("update-notice")).not.toBeInTheDocument();
    unmount();
    delete (window as unknown as { webkit?: unknown }).webkit;
    renderSignIn();
    expect(screen.queryByTestId("update-notice")).not.toBeInTheDocument();
  });

  it("the Test my device panel has the notice with a button and no countdown", () => {
    native({ version: "0.7.6" });
    renderSignIn();
    act(() => screen.getByRole("button", { name: "Not now" }).click());
    act(() => screen.getByRole("button", { name: "Test my device" }).click());
    const n = screen.getByTestId("update-notice");
    expect(n).toHaveTextContent("FairPlay 0.7.6 is available.");
    expect(n).not.toHaveTextContent("Updating in");
    act(() => screen.getByRole("button", { name: "Update now" }).click());
    expect(installs()).toBe(1);
  });

  it("a signed-in dashboard never offers or triggers an update", () => {
    setToken("student", "TOK");
    native({ version: "0.7.6" });
    render(
      <MemoryRouter initialEntries={["/student"]}>
        <StudentHome />
      </MemoryRouter>,
    );
    tick(10000);
    expect(screen.queryByTestId("update-notice")).not.toBeInTheDocument();
    expect(installs()).toBe(0);
  });
});
