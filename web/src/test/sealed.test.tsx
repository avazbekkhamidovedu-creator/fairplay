// S5 (2026-10-20): seal at time-up, the three-button screen, sealedToken after sign-in.
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import { clearToken, setToken } from "../api/client";
import { TimeUpOffline } from "../components/student/TimeUpOffline";
import * as bridge from "../lib/bridge";

function native() {
  const postMessage = vi.fn();
  (window as unknown as { webkit: unknown }).webkit = { messageHandlers: { proctora: { postMessage } } };
  return postMessage;
}
const send = (detail: unknown) => window.dispatchEvent(new CustomEvent("proctora-native", { detail }));

describe("sealed offline submit (S5)", () => {
  afterEach(() => {
    delete (window as unknown as { webkit?: unknown }).webkit;
    bridge._resetBridgeForTests();
    clearToken("student");
  });

  it("seal posts the exact answers string and waits for the app", async () => {
    const post = native();
    const answers_json = JSON.stringify({ q_1: 2, q_2: "Paris" });
    const p = bridge.seal({ attempt_id: "a_1", answers_json, client_seq: 9 }, 1000);
    expect(post).toHaveBeenCalledWith({ action: "seal", attempt_id: "a_1", answers_json, client_seq: 9 });
    send({ type: "sealed", attempt_id: "a_other", ok: true });     // another attempt: ignored
    send({ type: "sealed", attempt_id: "a_1", ok: true });
    await expect(p).resolves.toBe(true);
  });

  it("seal is false outside the app, on ok:false and on timeout", async () => {
    await expect(bridge.seal({ attempt_id: "a_1", answers_json: "{}", client_seq: 0 })).resolves.toBe(false);
    native();
    const p = bridge.seal({ attempt_id: "a_1", answers_json: "{}", client_seq: 0 }, 1000);
    send({ type: "sealed", attempt_id: "a_1", ok: false });
    await expect(p).resolves.toBe(false);
    vi.useFakeTimers();
    const q = bridge.seal({ attempt_id: "a_1", answers_json: "{}", client_seq: 0 }, 50);
    vi.advanceTimersByTime(60);
    await expect(q).resolves.toBe(false);
    vi.useRealTimers();
  });

  it("hands the student token to an app with sealed files, once per page load", () => {
    const post = native();
    setToken("student", "tok-1");
    bridge.hello();
    send({ type: "state", locked: false, trusted: true, version: "0.5", sealed_pending: false });
    expect(post).not.toHaveBeenCalledWith(expect.objectContaining({ action: "sealedToken" }));
    send({ type: "state", locked: false, trusted: true, version: "0.5", sealed_pending: true });
    expect(post).toHaveBeenCalledWith({ action: "sealedToken", token: "tok-1" });
    send({ type: "state", locked: false, trusted: true, version: "0.5", sealed_pending: true });
    setToken("student", "tok-2");
    expect(post.mock.calls.filter(([m]) => (m as { action: string }).action === "sealedToken")).toHaveLength(1);
  });

  it("right after sign-in the new token goes to the app", () => {
    const post = native();
    bridge.hello();
    send({ type: "state", locked: false, trusted: true, version: "0.5", sealed_pending: true });
    expect(post).not.toHaveBeenCalledWith(expect.objectContaining({ action: "sealedToken" }));  // not signed in
    setToken("teacher", "teacher-token");
    expect(post).not.toHaveBeenCalledWith(expect.objectContaining({ action: "sealedToken" }));
    setToken("student", "fresh");
    expect(post).toHaveBeenCalledWith({ action: "sealedToken", token: "fresh" });
  });

  it("time-up screen: try again, submit later (sealed), ask for help", async () => {
    native();
    const onRetry = vi.fn();
    const onSeal = vi.fn(async () => true);
    render(<TimeUpOffline message="Cannot reach the server." onRetry={onRetry} onSeal={onSeal} />);
    await userEvent.click(screen.getByRole("button", { name: "Try again now" }));
    expect(onRetry).toHaveBeenCalledTimes(1);
    await userEvent.click(screen.getByRole("button", { name: "Ask the teacher for help" }));
    expect(screen.getByTestId("offline-exit-steps")).toHaveTextContent("Mac code");
    await userEvent.click(screen.getByRole("button", { name: "Submit later from this computer" }));
    expect(onSeal).toHaveBeenCalledTimes(1);
    expect(await screen.findByTestId("time-up-sealed")).toHaveTextContent(
      "Your answers are sealed on this computer",
    );
    expect(screen.getByText("Ask the invigilator to end the exam.")).toBeInTheDocument();
  });

  it("a failed seal says so and keeps the buttons", async () => {
    native();
    render(<TimeUpOffline message={null} onRetry={vi.fn()} onSeal={async () => false} />);
    await userEvent.click(screen.getByRole("button", { name: "Submit later from this computer" }));
    expect(await screen.findByText(/could not seal your answers/)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Try again now" })).toBeInTheDocument();
  });

  it("in a browser there is nothing to seal", () => {
    render(<TimeUpOffline message={null} onRetry={vi.fn()} onSeal={async () => false} />);
    expect(screen.queryByRole("button", { name: "Submit later from this computer" })).toBeNull();
  });
});
