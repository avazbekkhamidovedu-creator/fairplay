import { act, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { ExamBox } from "../api/types";
import { DukeBoxPane, useDukeBox } from "../components/student/DukeBox";
import { eventDetailText, eventLabel } from "../components/teacher/util";
import * as bridge from "../lib/bridge";

const TOKEN = "AbCdEf1234567890";
const BOX: ExamBox = {
  start_url: `https://duke.app.box.com/s/${TOKEN}`,
  app_hosts: ["duke.app.box.com", "duke.box.com"],
  path_prefix: `/s/${TOKEN}`,
  sign_in_hosts: [{ host: "duke.account.box.com", paths: ["/login*"] }, { host: "sso.services.box.net", paths: null }],
  asset_hosts: ["cdn01.boxcdn.net"],
  blocked_requests: [{ host: "*", path: "/upload*" }, { host: "*", path: "/api/2.0/folders", methods: ["POST"] }],
};

let posted: { action: string; [k: string]: unknown }[];
const send = (detail: unknown) => act(() => void window.dispatchEvent(new CustomEvent("proctora-native", { detail })));

function native(boxSupported: boolean | undefined) {
  const postMessage = vi.fn((msg: { action: string }) => void posted.push(msg));
  (window as unknown as { webkit: unknown }).webkit = { messageHandlers: { proctora: { postMessage } } };
  bridge.onNative(() => undefined);
  send({ type: "state", locked: true, trusted: true, version: "1", ...(boxSupported === undefined ? {} : { boxSupported }) });
}

function Harness({ box }: { box?: ExamBox }) {
  const s = useDukeBox(box);
  return box ? <DukeBoxPane box={s} /> : <p>no box</p>;
}

const sent = (a: string) => posted.filter((m) => m.action === a);

beforeEach(() => {
  posted = [];
  bridge._resetBridgeForTests();
});
afterEach(() => {
  delete (window as unknown as { webkit?: unknown }).webkit;
});

describe("Duke Box on the exam page", () => {
  it("has no button without tools.box", () => {
    native(true);
    render(<Harness />);
    expect(screen.queryByRole("button", { name: "Open Duke Box" })).toBeNull();
  });

  it("says the app is needed in a browser and in an app without Box support", () => {
    const a = render(<Harness box={BOX} />);
    expect(screen.getByText("Duke Box needs the FairPlay app.")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Open Duke Box" })).toBeNull();
    a.unmount();
    native(undefined);
    render(<Harness box={BOX} />);
    expect(screen.getByText("Duke Box needs the FairPlay app.")).toBeInTheDocument();
    expect(sent("open_box")).toHaveLength(0);
  });

  it("sends open_box with exactly the tools.box object and shows the states", async () => {
    native(true);
    render(<Harness box={BOX} />);
    await userEvent.click(screen.getByRole("button", { name: "Open Duke Box" }));
    expect(sent("open_box")).toEqual([{ action: "open_box", box: BOX }]);
    expect(screen.getByText("Opening…")).toBeInTheDocument();

    await userEvent.click(screen.getByRole("button", { name: "Open Duke Box" }));
    expect(sent("open_box")).toHaveLength(1);

    send({ type: "boxResult", state: "opened" });
    expect(screen.getByText("Open in the Box window. Close it to return to your exam.")).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Open Duke Box" }));
    expect(sent("open_box")).toHaveLength(1);

    send({ type: "boxResult", state: "closed" });
    expect(screen.queryByTestId("box-status")).toBeNull();
    await userEvent.click(screen.getByRole("button", { name: "Open Duke Box" }));
    expect(sent("open_box")).toHaveLength(2);
  });

  it("shows the app's error message without the link", async () => {
    native(true);
    render(<Harness box={BOX} />);
    await userEvent.click(screen.getByRole("button", { name: "Open Duke Box" }));
    send({ type: "boxResult", state: "error", error: `Could not load ${BOX.start_url}` });
    const status = screen.getByTestId("box-status");
    expect(status.textContent).toContain("Could not load");
    expect(status.textContent).not.toContain(TOKEN);
    await userEvent.click(screen.getByRole("button", { name: "Open Duke Box" }));
    expect(sent("open_box")).toHaveLength(2);
  });

  it("closes the Box window when the page unmounts", () => {
    native(true);
    const v = render(<Harness box={BOX} />);
    expect(sent("close_box")).toHaveLength(0);
    v.unmount();
    expect(sent("close_box")).toEqual([{ action: "close_box" }]);
  });

  it("never puts the link in the page", async () => {
    native(true);
    const v = render(<Harness box={BOX} />);
    await userEvent.click(screen.getByRole("button", { name: "Open Duke Box" }));
    send({ type: "boxResult", state: "opened" });
    expect(v.container.innerHTML).not.toContain(TOKEN);
    expect(v.container.innerHTML).not.toContain("duke.app.box.com/s");
  });
});

describe("Duke Box for the teacher", () => {
  it("labels the events", () => {
    expect(eventLabel("box_opened")).toBe("Opened Duke Box");
    expect(eventLabel("blocked_navigation")).toBe("Tried to open a blocked website");
    expect(eventDetailText("blocked_navigation", "box cdn.example.com upload")).toBe("Duke Box: tried upload on cdn.example.com");
    expect(eventDetailText("blocked_navigation", "docs.python.org")).toBe("docs.python.org");
    expect(eventDetailText("box_opened", "duke.app.box.com")).toBe("duke.app.box.com");
    expect(eventDetailText("blocked_navigation", null)).toBe("");
  });

  it("a changed Box link closes the open window and opens the new link; a removed Box closes it", async () => {
    native(true);
    const NEW: ExamBox = { ...BOX, start_url: `https://duke.app.box.com/s/ZzZzZz9876543210`, path_prefix: "/s/ZzZzZz9876543210" };
    const r = render(<Harness box={BOX} />);
    await userEvent.click(screen.getByRole("button", { name: "Open Duke Box" }));
    send({ type: "boxResult", state: "opened" });
    expect(sent("open_box")).toHaveLength(1);
    r.rerender(<Harness box={NEW} />);
    expect(sent("close_box").length).toBeGreaterThanOrEqual(1);
    expect(sent("open_box")).toHaveLength(2);
    expect((sent("open_box")[1] as { box?: ExamBox }).box?.start_url ?? JSON.stringify(sent("open_box")[1])).toContain("ZzZzZz9876543210");
    send({ type: "boxResult", state: "opened" });
    const closed = sent("close_box").length;
    r.rerender(<Harness />);
    expect(sent("close_box").length).toBeGreaterThan(closed);
    expect(sent("open_box")).toHaveLength(2);
  });

  it("a Box window that was not open stays closed when the link changes", () => {
    native(true);
    const NEW: ExamBox = { ...BOX, start_url: `https://duke.app.box.com/s/ZzZzZz9876543210`, path_prefix: "/s/ZzZzZz9876543210" };
    const r = render(<Harness box={BOX} />);
    r.rerender(<Harness box={NEW} />);
    expect(sent("open_box")).toHaveLength(0);
  });
});
