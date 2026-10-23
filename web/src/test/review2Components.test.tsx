// 2026-10-23 review (second pass), student components: arrows don't un-cross options, the
// websites pane's blank-page hint, the teacher's "can't be shown" warning, PDF passwords
// and viewer errors, the calculator stepping out of the panel on resize, the start-code
// form after it is gone, and the exam lock while the pre-exam lock is on.
import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { useState } from "react";
import { MemoryRouter, Route, Routes, useLocation } from "react-router-dom";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { MaterialFile, StudentQuestion } from "../api/types";

vi.mock("../api/client", async (importOriginal) => {
  const actual = await importOriginal<typeof import("../api/client")>();
  return {
    ...actual,
    studentApi: { listExams: vi.fn(), start: vi.fn() },
    toolsApi: { siteCheck: vi.fn() },
    materialsApi: { list: vi.fn(), upload: vi.fn(), remove: vi.fn() },
  };
});
vi.mock("../lib/pdf", async (importOriginal) => {
  const actual = await importOriginal<typeof import("../lib/pdf")>();
  return { ...actual, openPdf: vi.fn(), pdfNeedsPassword: vi.fn() };
});

import { ApiError, NetworkError, materialsApi, studentApi, toolsApi } from "../api/client";
import { AnswerInput } from "../components/student/AnswerInput";
import { Calculator } from "../components/student/Calculator";
import { BLANK_HINT, WebsitesPaneContent } from "../components/student/ExamToolPanes";
import { MaterialViewer, PDF_PASSWORD, VIEWER_FAILED, VIEWER_OFFLINE } from "../components/student/MaterialViewer";
import { MaterialsUpload } from "../components/student/MaterialsUpload";
import { StartCodeForm } from "../components/student/StartCodeForm";
import { ExamToolsSettings, _resetSiteChecksForTests } from "../components/teacher/ExamToolsSettings";
import * as bridge from "../lib/bridge";
import * as pdf from "../lib/pdf";

const actualPdf = await vi.importActual<typeof import("../lib/pdf")>("../lib/pdf");
const send = (detail: unknown) => window.dispatchEvent(new CustomEvent("proctora-native", { detail }));

afterEach(() => {
  vi.clearAllMocks();
  delete (window as unknown as { webkit?: unknown }).webkit;
  bridge._resetBridgeForTests();
  _resetSiteChecksForTests();
});

describe("M4: arrow keys and crossed-out options", () => {
  const q = { id: "q1", type: "mcq", prompt: "?", points: 1, options: ["A", "B", "C", "D"] } as StudentQuestion;

  it("skips a crossed-out option and never un-crosses it", () => {
    const onChange = vi.fn();
    const onToggle = vi.fn();
    render(<AnswerInput question={q} value={0} onChange={onChange} labelledBy="p" cross={{ on: false, crossed: [1], onToggle }} />);
    fireEvent.keyDown(screen.getAllByRole("radio")[0]!, { key: "ArrowDown" });
    expect(onChange).toHaveBeenCalledWith(2);
    expect(onToggle).not.toHaveBeenCalled();
  });

  it("only a click on a crossed-out option brings it back", () => {
    const onChange = vi.fn();
    const onToggle = vi.fn();
    render(<AnswerInput question={q} value={0} onChange={onChange} labelledBy="p" cross={{ on: false, crossed: [1], onToggle }} />);
    fireEvent.click(screen.getAllByRole("radio")[1]!);
    expect(onToggle).toHaveBeenCalledWith(1);
    expect(onChange).toHaveBeenCalledWith(1);
  });

  it("with every other option crossed out the arrow stays put", () => {
    const onChange = vi.fn();
    const onToggle = vi.fn();
    render(<AnswerInput question={q} value={0} onChange={onChange} labelledBy="p" cross={{ on: false, crossed: [1, 2, 3], onToggle }} />);
    fireEvent.keyDown(screen.getAllByRole("radio")[0]!, { key: "ArrowUp" });
    expect(onChange).toHaveBeenCalledWith(0);
    expect(onToggle).not.toHaveBeenCalled();
  });
});

describe("H3: websites that refuse to be framed", () => {
  it("the pane always says what a blank page means", () => {
    render(<WebsitesPaneContent sites={["github.com"]} />);
    fireEvent.click(screen.getByRole("button", { name: "github.com" }));
    expect(screen.getByTestId("website-hint")).toHaveTextContent(BLANK_HINT);
  });

  it("the teacher sees a warning next to a site that can't be shown", async () => {
    const check = toolsApi.siteCheck as unknown as ReturnType<typeof vi.fn>;
    check.mockImplementation(async (host: string) =>
      host === "github.com"
        ? { host, frameable: false, reason: "The site doesn't allow being shown inside other pages (X-Frame-Options: DENY)." }
        : { host, frameable: true, reason: "" },
    );
    function Harness() {
      const [v, setV] = useState({ calculator: "off" as const, openBook: false, websites: ["docs.python.org"] });
      return <ExamToolsSettings value={v} onChange={(n) => setV(n as typeof v)} />;
    }
    render(<Harness />);
    await waitFor(() => expect(check).toHaveBeenCalledWith("docs.python.org"));
    expect(screen.queryByTestId("site-warn")).toBeNull();
    fireEvent.change(screen.getByLabelText("Website to allow"), { target: { value: "github.com" } });
    fireEvent.click(screen.getByRole("button", { name: "Add" }));
    const warn = await screen.findByTestId("site-warn");
    expect(warn).toHaveTextContent("Can't be shown inside the exam");
    expect(warn.closest("li")).toHaveTextContent("github.com");
    expect(screen.getAllByTestId("site-warn")).toHaveLength(1);
  });

  it("a failed check (offline, rate limited) shows nothing", async () => {
    const check = toolsApi.siteCheck as unknown as ReturnType<typeof vi.fn>;
    check.mockRejectedValue(new NetworkError());
    render(<ExamToolsSettings value={{ calculator: "off", openBook: false, websites: ["github.com"] }} onChange={() => undefined} />);
    await waitFor(() => expect(check).toHaveBeenCalled());
    expect(screen.queryByTestId("site-warn")).toBeNull();
  });
});

describe("L1 / L2: opening materials", () => {
  const file = (kind: MaterialFile["kind"]): MaterialFile => ({ id: "m1", name: `notes.${kind}`, kind, size: 10 }) as MaterialFile;

  it("a password-protected PDF says so", async () => {
    (pdf.openPdf as unknown as ReturnType<typeof vi.fn>).mockRejectedValue(Object.assign(new Error("No password given"), { name: "PasswordException" }));
    render(<MaterialViewer file={file("pdf")} load={async () => new Blob(["%PDF-1.7"])} />);
    expect(await screen.findByRole("alert")).toHaveTextContent(PDF_PASSWORD);
  });

  it("a damaged PDF keeps its own message", async () => {
    (pdf.openPdf as unknown as ReturnType<typeof vi.fn>).mockRejectedValue(new Error("Invalid PDF structure"));
    render(<MaterialViewer file={file("pdf")} load={async () => new Blob(["junk"])} />);
    expect(await screen.findByRole("alert")).toHaveTextContent("could not be opened. The file may be damaged.");
  });

  it("'needs the connection' only when it is the connection", async () => {
    const { unmount } = render(<MaterialViewer file={file("txt")} load={() => Promise.reject(new NetworkError())} />);
    expect(await screen.findByRole("alert")).toHaveTextContent(VIEWER_OFFLINE);
    unmount();
    render(<MaterialViewer file={file("txt")} load={() => Promise.reject(new ApiError(404, "Not found"))} />);
    expect(await screen.findByRole("alert")).toHaveTextContent(VIEWER_FAILED);
  });

  it("pdfNeedsPassword: no /Encrypt -> no pdf.js at all; /Encrypt -> pdf.js decides", async () => {
    const open = vi.fn();
    expect(await actualPdf.pdfNeedsPassword(new Blob(["%PDF-1.7\ntrailer<<>>"]), open)).toBe(false);
    expect(open).not.toHaveBeenCalled();
    const enc = new Blob(["%PDF-1.7\n", "x".repeat(100_000), "\ntrailer<</Encrypt 5 0 R>>"]);
    open.mockRejectedValueOnce(Object.assign(new Error("pw"), { name: "PasswordException" }));
    expect(await actualPdf.pdfNeedsPassword(enc, open)).toBe(true);
    // owner password only: opens fine, accepted
    open.mockResolvedValueOnce({ numPages: 1, getPage: vi.fn(), destroy: vi.fn(async () => undefined) });
    expect(await actualPdf.pdfNeedsPassword(enc, open)).toBe(false);
  });

  it("the upload refuses a PDF that needs a password before sending it", async () => {
    const m = materialsApi as unknown as Record<string, ReturnType<typeof vi.fn>>;
    m.list.mockResolvedValue({ files: [], used_bytes: 0, can_change: true, max_files: 5, max_bytes: 20 << 20 });
    (pdf.pdfNeedsPassword as unknown as ReturnType<typeof vi.fn>).mockResolvedValue(true);
    render(<MaterialsUpload examId="e_1" />);
    const input = await screen.findByTestId("materials-input");
    fireEvent.change(input, { target: { files: [new File(["%PDF /Encrypt"], "locked.pdf", { type: "application/pdf" })] } });
    expect(await screen.findByRole("alert")).toHaveTextContent("locked.pdf is password-protected. Upload a copy without a password.");
    expect(m.upload).not.toHaveBeenCalled();
  });
});

describe("M1: the calculator on a resized window", () => {
  it("steps out of the side panel again after a resize", async () => {
    Object.defineProperty(window, "innerWidth", { value: 1280, configurable: true });
    render(<Calculator mode="scientific" open onClose={() => undefined} paneWidth={400} />);
    const el = screen.getByRole("dialog", { name: "Calculator" });
    // jsdom has no layout: the panel counts as 336 px wide
    await waitFor(() => expect(el.style.left).toBe(`${1280 - 400 - 336 - 16}px`));
    Object.defineProperty(window, "innerWidth", { value: 1000, configurable: true });
    act(() => {
      window.dispatchEvent(new Event("resize"));
    });
    expect(el.style.left).toBe(`${1000 - 400 - 336 - 16}px`);
    Object.defineProperty(window, "innerWidth", { value: 1024, configurable: true });
  });
});

describe("H2: the start-code form after it is gone", () => {
  function Where() {
    return <p data-testid="where">{useLocation().pathname}</p>;
  }
  function Harness() {
    const [show, setShow] = useState(true);
    return (
      <>
        {show && <StartCodeForm examId="e_1" />}
        <button type="button" onClick={() => setShow(false)}>
          leave
        </button>
        <Where />
      </>
    );
  }

  it("does not open the exam when the start answer comes after the student left", async () => {
    const start = studentApi.start as unknown as ReturnType<typeof vi.fn>;
    let resolve: (v: unknown) => void = () => undefined;
    start.mockReturnValue(new Promise((r) => (resolve = r)));
    render(
      <MemoryRouter initialEntries={["/student/exams/e_1/start"]}>
        <Routes>
          <Route path="*" element={<Harness />} />
        </Routes>
      </MemoryRouter>,
    );
    screen.getAllByRole("textbox").forEach((b, i) => fireEvent.change(b, { target: { value: String(i + 1) } }));
    fireEvent.click(screen.getByRole("button", { name: "Start exam" }));
    await waitFor(() => expect(start).toHaveBeenCalled());
    fireEvent.click(screen.getByRole("button", { name: "leave" }));
    await act(async () => resolve({ attempt_id: "a_9" }));
    expect(screen.getByTestId("where")).toHaveTextContent("/student/exams/e_1/start");
  });

  it("reports the request in flight to the page", async () => {
    const start = studentApi.start as unknown as ReturnType<typeof vi.fn>;
    let reject: (e: unknown) => void = () => undefined;
    start.mockReturnValue(new Promise((_r, j) => (reject = j)));
    const onInFlight = vi.fn();
    render(
      <MemoryRouter>
        <StartCodeForm examId="e_1" onInFlight={onInFlight} />
      </MemoryRouter>,
    );
    screen.getAllByRole("textbox").forEach((b, i) => fireEvent.change(b, { target: { value: String(i + 1) } }));
    fireEvent.click(screen.getByRole("button", { name: "Start exam" }));
    await waitFor(() => expect(onInFlight).toHaveBeenLastCalledWith(true));
    await act(async () => reject(new ApiError(403, "Wrong start code")));
    expect(onInFlight).toHaveBeenLastCalledWith(false);
  });
});

describe("L5: the exam lock while the pre-exam lock is on", () => {
  it("a state with locked + prelock is not the exam lock; locked (no prelock) is", async () => {
    const postMessage = vi.fn();
    (window as unknown as { webkit: unknown }).webkit = { messageHandlers: { proctora: { postMessage } } };
    const done = vi.fn();
    void bridge.requestLock(10000, { attempt_id: "a_1", token: "t", session: "s" }).then(done);
    send({ type: "state", locked: true, prelock: true, trusted: true, version: "1" });
    send({ type: "locked", prelock: true });
    await Promise.resolve();
    expect(done).not.toHaveBeenCalled();
    send({ type: "state", locked: true, prelock: false, trusted: true, version: "1" });
    await waitFor(() => expect(done).toHaveBeenCalledWith({ ok: true }));
  });

  it("…or the plain locked answer", async () => {
    const postMessage = vi.fn();
    (window as unknown as { webkit: unknown }).webkit = { messageHandlers: { proctora: { postMessage } } };
    const p = bridge.requestLock(10000, { attempt_id: "a_1", token: "t", session: "s" });
    send({ type: "locked" });
    await expect(p).resolves.toEqual({ ok: true });
  });
});
