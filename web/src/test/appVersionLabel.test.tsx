import { act, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { MemoryRouter } from "react-router-dom";
import { AppVersion } from "../components/AppVersion";
import NativeHome from "../pages/student/NativeHome";
import * as bridge from "../lib/bridge";

const send = (detail: unknown) => act(() => void window.dispatchEvent(new CustomEvent("proctora-native", { detail })));

function native() {
  (window as unknown as { webkit: unknown }).webkit = { messageHandlers: { proctora: { postMessage: vi.fn() } } };
}

describe("AppVersion", () => {
  afterEach(() => {
    delete (window as unknown as { webkit?: unknown }).webkit;
    bridge._resetBridgeForTests();
  });

  it("shows nothing in a normal browser", () => {
    const { container } = render(<AppVersion />);
    expect(container).toBeEmptyDOMElement();
  });

  it("shows nothing when the app's state has no version", () => {
    native();
    const { container } = render(<AppVersion />);
    send({ type: "state", locked: false, trusted: true, version: "0.5 (12)" });
    expect(container).toBeEmptyDOMElement();
  });

  it("shows version and platform, and updates when the state arrives after load", () => {
    native();
    render(<AppVersion />);
    expect(screen.queryByTestId("app-version")).toBeNull();
    send({ type: "state", locked: false, trusted: true, version: "0.7", app_version: "0.7.2", platform: "mac" });
    expect(screen.getByTestId("app-version")).toHaveTextContent("FairPlay 0.7.2 · Mac");
    send({ type: "state", locked: false, trusted: true, version: "0.7", app_version: "0.7.3", platform: "windows" });
    expect(screen.getByTestId("app-version")).toHaveTextContent("FairPlay 0.7.3 · Windows");
  });

  it("code sign-in shows a short version under the logo, once the state arrives, and nothing without one", () => {
    render(
      <MemoryRouter>
        <NativeHome onUseAccount={() => {}} />
      </MemoryRouter>,
    );
    expect(screen.queryByTestId("app-version")).toBeNull();
    native();
    send({ type: "state", locked: false, trusted: true, version: "0.7" });
    expect(screen.queryByTestId("app-version")).toBeNull();
    send({ type: "state", locked: false, trusted: true, version: "0.7", app_version: "0.7.6", platform: "mac" });
    const label = screen.getByTestId("app-version");
    expect(label).toHaveTextContent("v0.7.6 · Mac");
    expect(label.closest(".cs-brand-col")).not.toBeNull();
  });
});
