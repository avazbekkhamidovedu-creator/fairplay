// 2026-10-23 (exam tools): calculator panel and graph helpers, top bar tool entries, side
// pane, websites pane, materials (pane, upload), teacher settings, bridge allowed_sites.
import { act, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { setToken } from "../api/client";
import type { Exam, MaterialsInfo } from "../api/types";
import { Calculator, clampPosition, insertAt, niceStep, samplePlot, zoomAt } from "../components/student/Calculator";
import { FRAME_TIMEOUT_MS, MaterialsPaneContent, OFFLINE_MATERIALS, WebsitesPaneContent } from "../components/student/ExamToolPanes";
import { LockedBar } from "../components/student/LockedBar";
import { MaterialsUpload } from "../components/student/MaterialsUpload";
import { nextZoom } from "../components/student/MaterialViewer";
import { ToolPane, clampPaneWidth } from "../components/student/ToolPane";
import { toolsLine } from "../components/student/format";
import { ExamToolsSettings, type ExamToolsValue } from "../components/teacher/ExamToolsSettings";
import * as bridge from "../lib/bridge";
import {
  BLOB_CACHE_BYTES,
  _materialsMemoryForTests,
  _resetMaterialsForTests,
  checkFile,
  forgetAttemptMaterials,
  formatBytes,
  loadAttemptMaterials,
  materialBlob,
} from "../lib/materials";
import { pagesMatching } from "../lib/pdf";
import { hostAllowed, normalizeWebsite } from "../lib/websites";
import { ExamEditor } from "../pages/teacher/ExamEditor";
import { draftFromExam, toExamInput } from "../pages/teacher/examDraft";
import { useState } from "react";

function json(body: unknown, status = 200) {
  return new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });
}

beforeEach(() => {
  // jsdom has no canvas: the graph and PDF pages just don't draw
  vi.spyOn(HTMLCanvasElement.prototype, "getContext").mockReturnValue(null);
});

afterEach(() => {
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
  vi.useRealTimers();
  _resetMaterialsForTests();
  delete (window as unknown as { webkit?: unknown }).webkit;
  bridge._resetBridgeForTests();
});

// ---------------------------------------------------------------- calculator
describe("calculator panel", () => {
  it("works with the keys: order of operations, AC, backspace", async () => {
    render(<Calculator mode="scientific" open onClose={vi.fn()} />);
    const dialog = screen.getByRole("dialog", { name: "Calculator" });
    const key = (name: string) => fireEvent.click(within(dialog).getByRole("button", { name }));
    for (const k of ["2", "Plus", "3", "Multiply", "4"]) key(k);
    const input = screen.getByLabelText("Expression") as HTMLInputElement;
    expect(input.value).toBe("2+3×4");
    expect(screen.getByTestId("calc-result").textContent).toBe("= 14");      // live preview
    key("Equals");
    expect(screen.getByTestId("calc-result").textContent).toBe("14");
    expect(screen.getByText("2+3×4 =")).toBeInTheDocument();
    // an operator right after "=" goes on from Ans
    key("Plus");
    expect(input.value).toBe("ans+");
    key("1");
    key("Equals");
    expect(screen.getByTestId("calc-result").textContent).toBe("15");
    // a digit after "=" starts over
    key("7");
    expect(input.value).toBe("7");
    key("Backspace");
    expect(input.value).toBe("");
    key("9");
    key("Clear");
    expect(input.value).toBe("");
  });

  it("takes typing, Enter, shows mistakes, and switches degrees / radians", async () => {
    render(<Calculator mode="scientific" open onClose={vi.fn()} />);
    const input = screen.getByLabelText("Expression") as HTMLInputElement;
    fireEvent.change(input, { target: { value: "sin(30)" } });
    fireEvent.keyDown(input, { key: "Enter" });
    expect(screen.getByTestId("calc-result").textContent).toBe("0.5");
    fireEvent.change(input, { target: { value: "2+" } });
    fireEvent.keyDown(input, { key: "Enter" });
    expect(screen.getByTestId("calc-result").textContent).toMatch(/Finish/);
    fireEvent.click(screen.getByRole("button", { name: /Angles in degrees/ }));
    expect(screen.getByRole("button", { name: /Angles in radians/ })).toHaveTextContent("RAD");
    fireEvent.change(input, { target: { value: "sin(π/2)" } });
    fireEvent.keyDown(input, { key: "Enter" });
    expect(screen.getByTestId("calc-result").textContent).toBe("1");
  });

  it("closes with ✕ or Esc and is hidden (not removed) when closed", () => {
    const onClose = vi.fn();
    const { rerender } = render(<Calculator mode="scientific" open onClose={onClose} />);
    fireEvent.click(screen.getByRole("button", { name: "Close calculator" }));
    fireEvent.keyDown(screen.getByLabelText("Expression"), { key: "Escape" });
    expect(onClose).toHaveBeenCalledTimes(2);
    const input = screen.getByLabelText("Expression") as HTMLInputElement;
    fireEvent.change(input, { target: { value: "12" } });
    rerender(<Calculator mode="scientific" open={false} onClose={onClose} />);
    const panel = document.querySelector(".stu-calc") as HTMLElement;
    expect(panel).toHaveAttribute("aria-hidden", "true");
    expect(panel).toHaveAttribute("inert");
    rerender(<Calculator mode="scientific" open onClose={onClose} />);
    expect((screen.getByLabelText("Expression") as HTMLInputElement).value).toBe("12");   // state kept
  });

  it("graphing mode has a Graph tab with three functions and errors per line", () => {
    render(<Calculator mode="graphing" open onClose={vi.fn()} />);
    expect(screen.getByRole("tab", { name: "Calculator" })).toHaveAttribute("aria-selected", "true");
    fireEvent.click(screen.getByRole("tab", { name: "Graph" }));
    expect(screen.getByRole("tab", { name: "Graph" })).toHaveAttribute("aria-selected", "true");
    const y1 = screen.getByLabelText(/y\s*1\s*=/);
    expect(screen.getByLabelText(/y\s*3\s*=/)).toBeInTheDocument();
    fireEvent.change(y1, { target: { value: "x^2+" } });
    expect(screen.getByRole("alert")).toHaveTextContent(/Finish/);
    fireEvent.change(y1, { target: { value: "x^2" } });
    expect(screen.queryByRole("alert")).toBeNull();
    expect(screen.getByRole("img", { name: /Graph/ })).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Zoom in" }));
    fireEvent.click(screen.getByRole("button", { name: "Back to the start view" }));
  });

  it("keeps the panel inside the window", () => {
    expect(clampPosition(-50, -20, 300, 400, 1000, 800)).toEqual({ x: 8, y: 8 });
    expect(clampPosition(2000, 2000, 300, 400, 1000, 800)).toEqual({ x: 692, y: 392 });
    expect(clampPosition(100, 100, 300, 400, 1000, 800)).toEqual({ x: 100, y: 100 });
  });

  it("inserts at the caret", () => {
    expect(insertAt("2+4", 2, 2, "3")).toEqual({ expr: "2+34", caret: 3 });
    expect(insertAt("2+4", 0, 3, "sin(")).toEqual({ expr: "sin(", caret: 4 });
  });

  it("graph helpers: grid steps, zoom around the cursor, broken curves", () => {
    expect(niceStep(32)).toBe(2);
    expect(niceStep(48)).toBe(1);
    expect(niceStep(4)).toBe(10);
    const v = { cx: 0, cy: 0, scale: 10 };
    const z = zoomAt(v, 2, 150, 50, 200, 100);            // the point under the cursor stays put
    const before = { x: v.cx + (150 - 100) / v.scale, y: v.cy - (50 - 50) / v.scale };
    const after = { x: z.cx + (150 - 100) / z.scale, y: z.cy - (50 - 50) / z.scale };
    expect(after.x).toBeCloseTo(before.x, 9);
    expect(after.y).toBeCloseTo(before.y, 9);
    expect(z.scale).toBe(20);
    const runs = samplePlot((x) => 1 / x, { cx: 0, cy: 0, scale: 20 }, 200, 100);
    expect(runs.length).toBe(2);                           // split at x = 0
    expect(samplePlot(() => NaN, v, 50, 50)).toEqual([]);
    expect(samplePlot((x) => x, v, 50, 50).length).toBe(1);
  });
});

// ---------------------------------------------------------------- top bar
describe("top bar tools", () => {
  const base = { title: "Quiz", locked: false, sync: "synced" as const, remaining: 600_000 };

  it("shows Calculator only when allowed, before More", () => {
    const { rerender } = render(<LockedBar {...base} onTextSize={vi.fn()} />);
    expect(screen.queryByRole("button", { name: "Calculator" })).toBeNull();
    const onToggle = vi.fn();
    rerender(<LockedBar {...base} onTextSize={vi.fn()} calculator={{ open: false, onToggle }} />);
    const btn = screen.getByRole("button", { name: "Calculator" });
    const more = screen.getByRole("button", { name: "More" });
    expect(btn.compareDocumentPosition(more) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
    fireEvent.click(btn);
    expect(onToggle).toHaveBeenCalled();
    rerender(<LockedBar {...base} onTextSize={vi.fn()} calculator={{ open: true, onToggle }} />);
    expect(screen.getByRole("button", { name: "Calculator" })).toHaveAttribute("aria-pressed", "true");
  });

  it("More lists Materials and Websites (with counts) above Text size", () => {
    const openMat = vi.fn();
    const openWeb = vi.fn();
    render(
      <LockedBar
        {...base}
        onTextSize={vi.fn()}
        materials={{ count: 3, onOpen: openMat }}
        websites={{ count: 1, onOpen: openWeb }}
      />,
    );
    fireEvent.click(screen.getByRole("button", { name: "More" }));
    const items = screen.getAllByRole("menuitem").map((i) => i.textContent);
    expect(items[0]).toBe("Materials3 files");
    expect(items[1]).toBe("Websites1 site");
    expect(items[2]).toMatch(/^Text size/);
    fireEvent.click(screen.getByRole("menuitem", { name: /Materials/ }));
    expect(openMat).toHaveBeenCalled();
    expect(screen.queryByRole("menu")).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: "More" }));
    fireEvent.click(screen.getByRole("menuitem", { name: /Websites/ }));
    expect(openWeb).toHaveBeenCalled();
  });

  it("has no Materials / Websites entries when the exam has none", () => {
    render(<LockedBar {...base} onTextSize={vi.fn()} />);
    fireEvent.click(screen.getByRole("button", { name: "More" }));
    expect(screen.queryByRole("menuitem", { name: /Materials|Websites/ })).toBeNull();
  });

  it("summarises the tools for Before you start", () => {
    expect(toolsLine({ calculator: "graphing", open_book: true, websites: ["a.com", "b.org"] })).toBe(
      "Calculator (graphing) · Materials (open book) · 2 websites",
    );
    expect(toolsLine({ calculator: "off", open_book: false, websites: [] })).toBe("");
    expect(toolsLine(undefined)).toBe("");
  });
});

// ---------------------------------------------------------------- side pane
describe("side pane", () => {
  it("clamps its width and resizes with the keyboard; closes with ✕", () => {
    expect(clampPaneWidth(100, 1400)).toBe(320);
    expect(clampPaneWidth(2000, 1400)).toBe(960);
    expect(clampPaneWidth(900, 1000)).toBe(640);
    function Pane({ onClose }: { onClose: () => void }) {
      const [w, setW] = useState(520);
      return (
        <ToolPane title="Materials" labelId="p-h" width={w} onWidth={setW} onClose={onClose}>
          <p>body</p>
        </ToolPane>
      );
    }
    const onClose = vi.fn();
    render(<Pane onClose={onClose} />);
    const sep = screen.getByRole("separator", { name: "Resize the side panel" });
    fireEvent.keyDown(sep, { key: "ArrowLeft" });
    expect(sep).toHaveAttribute("aria-valuenow", "544");
    fireEvent.keyDown(sep, { key: "ArrowRight", shiftKey: true });
    expect(sep).toHaveAttribute("aria-valuenow", "464");
    fireEvent.click(screen.getByRole("button", { name: "Close panel" }));
    expect(onClose).toHaveBeenCalled();
    expect(screen.getByRole("complementary", { name: "Materials" })).toBeInTheDocument();
  });
});

// ---------------------------------------------------------------- websites
describe("websites pane", () => {
  it("lists the sites, opens one in a sandboxed frame, keeps its own history", () => {
    render(<WebsitesPaneContent sites={["docs.python.org", "en.cppreference.com"]} />);
    expect(document.querySelector("iframe")).toBeNull();      // nothing loads until clicked
    fireEvent.click(screen.getByRole("button", { name: "docs.python.org" }));
    const frame = document.querySelector("iframe") as HTMLIFrameElement;
    expect(frame.getAttribute("src")).toBe("https://docs.python.org/");
    expect(frame.getAttribute("sandbox")).toBe("allow-scripts allow-same-origin allow-forms");
    expect(frame.getAttribute("sandbox")).not.toMatch(/popups|top-navigation/);
    expect(frame.getAttribute("referrerpolicy")).toBe("no-referrer");
    expect(screen.getByTestId("website-host").textContent).toBe("docs.python.org");
    expect(screen.queryByRole("textbox")).toBeNull();         // the address is not editable
    expect(screen.getByRole("button", { name: "Back" })).toBeDisabled();
    fireEvent.click(screen.getByRole("button", { name: "All sites" }));
    fireEvent.click(screen.getByRole("button", { name: "en.cppreference.com" }));
    expect(screen.getByTestId("website-host").textContent).toBe("en.cppreference.com");
    fireEvent.click(screen.getByRole("button", { name: "Back" }));
    expect(screen.getByTestId("website-host").textContent).toBe("docs.python.org");
    fireEvent.click(screen.getByRole("button", { name: "Forward" }));
    expect(screen.getByTestId("website-host").textContent).toBe("en.cppreference.com");
    expect(screen.getByRole("button", { name: "Forward" })).toBeDisabled();
  });

  it("says when a site does not load inside the exam", () => {
    vi.useFakeTimers();
    render(<WebsitesPaneContent sites={["blocked.example.com", "ok.example.com"]} />);
    fireEvent.click(screen.getByRole("button", { name: "blocked.example.com" }));
    act(() => {
      vi.advanceTimersByTime(FRAME_TIMEOUT_MS + 10);
    });
    expect(screen.getByTestId("website-blocked")).toHaveTextContent("This site can't be shown inside the exam");
    // a site that loads in time: no message
    fireEvent.click(screen.getByRole("button", { name: "All sites" }));
    fireEvent.click(screen.getByRole("button", { name: "ok.example.com" }));
    fireEvent.load(document.querySelector("iframe") as HTMLIFrameElement);
    act(() => {
      vi.advanceTimersByTime(FRAME_TIMEOUT_MS + 10);
    });
    expect(screen.queryByTestId("website-blocked")).toBeNull();
  });

  it("normalises website names like the server", () => {
    expect(normalizeWebsite("HTTPS://Docs.Python.org/")).toEqual({ ok: true, host: "docs.python.org" });
    for (const bad of ["docs.python.org/3/", "*.com", "localhost", "ftp://x.org", "10.0.0.1", "a.com:80", ""])
      expect(normalizeWebsite(bad).ok).toBe(false);
    // 2026-10-23 (review): exact entries; "*." for subdomains (websitesWildcard.test.tsx)
    expect(hostAllowed("docs.python.org", ["python.org"])).toBe(false);
    expect(hostAllowed("docs.python.org", ["*.python.org"])).toBe(true);
    expect(hostAllowed("evilpython.org", ["*.python.org"])).toBe(false);
  });
});

// ---------------------------------------------------------------- materials in the exam
describe("materials pane", () => {
  const FILES = [
    { id: "m_1", name: "notes.txt", kind: "txt", media_type: "text/plain", size: 12, created_at: "" },
    { id: "m_2", name: "Chapter 1.pdf", kind: "pdf", media_type: "application/pdf", size: 2_500_000, created_at: "" },
  ];

  it("lists the files and opens a text file inside FairPlay (no download)", async () => {
    setToken("student", "S");
    const fetchFn = vi.fn(async (url: string) => {
      if (url.endsWith("/materials")) return json({ files: FILES });
      if (url.endsWith("/materials/m_1")) return new Response("E = mc^2", { status: 200, headers: { "Content-Type": "text/plain" } });
      return new Response("%PDF-1.4", { status: 200 });
    });
    vi.stubGlobal("fetch", fetchFn);
    render(<MaterialsPaneContent attemptId="a_1" />);
    expect(await screen.findByRole("button", { name: /notes\.txt/ })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /Chapter 1\.pdf/ })).toHaveTextContent("2.4 MB");
    fireEvent.click(screen.getByRole("button", { name: /notes\.txt/ }));
    expect(await screen.findByText("E = mc^2")).toBeInTheDocument();
    const viewer = screen.getByTestId("material-viewer");
    expect(within(viewer).queryByRole("button", { name: /download|print|open/i })).toBeNull();
    expect(within(viewer).queryByRole("link")).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: "All files" }));
    expect(screen.getByRole("list", { name: "Your materials" })).toBeInTheDocument();
    // 2026-10-23 (review): only the opened file was fetched (no prefetch of the others)
    const urls = () => fetchFn.mock.calls.map((c) => c[0]);
    expect(urls()).toContain("/api/student/attempts/a_1/materials");
    expect(urls()).toContain("/api/student/attempts/a_1/materials/m_1");
    await new Promise((r) => setTimeout(r, 20));
    expect(urls()).not.toContain("/api/student/attempts/a_1/materials/m_2");
    // re-opening comes from memory
    fireEvent.click(screen.getByRole("button", { name: /notes\.txt/ }));
    expect(await screen.findByText("E = mc^2")).toBeInTheDocument();
    expect(urls().filter((u) => u.endsWith("/materials/m_1"))).toHaveLength(1);
  });

  it("keeps opened files in memory up to the cap, least recently opened dropped first", async () => {
    setToken("student", "S");
    const MB = 1024 * 1024;
    const sizes: Record<string, number> = { m_a: 25 * MB, m_b: 25 * MB, m_c: 20 * MB, m_d: 1 };
    const fetchFn = vi.fn(async (url: string) => {
      const id = url.split("/").pop() as string;
      return new Response(new Uint8Array(sizes[id]), { status: 200 });
    });
    vi.stubGlobal("fetch", fetchFn);
    await materialBlob("a_1", "m_a");
    await materialBlob("a_1", "m_b");
    await materialBlob("a_1", "m_a");                  // m_a is now the most recent
    expect(_materialsMemoryForTests()).toEqual({ bytes: 50 * MB, keys: ["a_1/m_b", "a_1/m_a"] });
    await materialBlob("a_1", "m_c");                  // 70 MB > 60 MB: m_b goes
    expect(_materialsMemoryForTests()).toEqual({ bytes: 45 * MB, keys: ["a_1/m_a", "a_1/m_c"] });
    expect(BLOB_CACHE_BYTES).toBe(60 * MB);
    expect(fetchFn).toHaveBeenCalledTimes(3);
    await materialBlob("a_1", "m_b");                  // fetched again
    expect(fetchFn).toHaveBeenCalledTimes(4);
    await materialBlob("a_2", "m_d");
    forgetAttemptMaterials("a_1");
    expect(_materialsMemoryForTests()).toEqual({ bytes: 1, keys: ["a_2/m_d"] });
  });

  it("loading the list fetches no file", async () => {
    setToken("student", "S");
    const fetchFn = vi.fn(async () => json({ files: FILES }));
    vi.stubGlobal("fetch", fetchFn);
    expect(await loadAttemptMaterials("a_1")).toHaveLength(2);
    await new Promise((r) => setTimeout(r, 20));
    expect(fetchFn).toHaveBeenCalledTimes(1);
  });

  it("needs the connection when the attempt is offline", async () => {
    render(<MaterialsPaneContent attemptId={null} />);
    expect(screen.getByTestId("materials-offline")).toHaveTextContent(OFFLINE_MATERIALS);
  });

  it("says so when the list can't be fetched offline", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => Promise.reject(new TypeError("offline"))));
    render(<MaterialsPaneContent attemptId="a_9" />);
    expect(await screen.findByTestId("materials-offline")).toHaveTextContent("Materials need the connection");
  });

  it("viewer helpers: zoom steps, page search", () => {
    expect(nextZoom(1, 1)).toBe(1.25);
    expect(nextZoom(1, -1)).toBe(0.75);
    expect(nextZoom(4, 1)).toBe(4);
    expect(nextZoom(0.5, -1)).toBe(0.5);
    expect(pagesMatching(["Intro", "The Chain rule", "chain again"], "CHAIN")).toEqual([2, 3]);
    expect(pagesMatching(["a"], "  ")).toEqual([]);
  });
});

// ---------------------------------------------------------------- upload before the exam
describe("materials upload", () => {
  const info = (over: Partial<MaterialsInfo> = {}): MaterialsInfo => ({
    open_book: true, can_change: true, reason: null, files: [], used_bytes: 0,
    max_files: 5, max_bytes: 20 * 1024 * 1024, types: ["pdf", "png", "jpg", "txt", "md"], ...over,
  });

  it("uploads chosen files, checks them first, and removes them", async () => {
    setToken("student", "S");
    let files: MaterialsInfo["files"] = [];
    const fetchFn = vi.fn(async (url: string, init?: RequestInit) => {
      if (init?.method === "POST") {
        const f = (init.body as FormData).get("file") as File;
        const row = { id: `m_${files.length + 1}`, name: f.name, kind: "pdf", media_type: "application/pdf", size: f.size, created_at: "" } as const;
        files = [...files, row];
        return json(row, 201);
      }
      if (init?.method === "DELETE") {
        files = files.filter((x) => !url.endsWith(x.id));
        return new Response(null, { status: 204 });
      }
      return json(info({ files, used_bytes: files.reduce((s, f) => s + f.size, 0) }));
    });
    vi.stubGlobal("fetch", fetchFn);
    render(<MaterialsUpload examId="e_1" />);
    const input = (await screen.findByTestId("materials-input")) as HTMLInputElement;
    expect(screen.getByText(/up to 5 files/)).toBeInTheDocument();
    const pdf = new File(["%PDF-1.4"], "book.pdf", { type: "application/pdf" });
    const exe = new File(["MZ"], "game.exe");
    fireEvent.change(input, { target: { files: [pdf, exe] } });
    expect(await screen.findByText("book.pdf")).toBeInTheDocument();
    expect(screen.getByRole("alert")).toHaveTextContent("game.exe: use a PDF, PNG, JPEG, TXT or MD file.");
    expect(fetchFn.mock.calls.filter((c) => (c[1] as RequestInit | undefined)?.method === "POST")).toHaveLength(1);
    fireEvent.click(screen.getByRole("button", { name: "Remove book.pdf" }));
    await waitFor(() => expect(screen.queryByText("book.pdf")).toBeNull());
  });

  it("drag and drop works too, and server errors are shown", async () => {
    setToken("student", "S");
    vi.stubGlobal(
      "fetch",
      vi.fn(async (_u: string, init?: RequestInit) =>
        init?.method === "POST" ? json({ detail: "Use a PDF, PNG, JPEG, TXT or MD file" }, 422) : json(info()),
      ),
    );
    render(<MaterialsUpload examId="e_1" />);
    const drop = await screen.findByTestId("materials-drop");
    const fake = new File(["<svg/>"], "fake.pdf");
    fireEvent.drop(drop, { dataTransfer: { files: [fake] } });
    expect(await screen.findByRole("alert")).toHaveTextContent("fake.pdf: Use a PDF, PNG, JPEG, TXT or MD file");
  });

  it("is read only once the exam has started", async () => {
    setToken("student", "S");
    vi.stubGlobal(
      "fetch",
      vi.fn(async () =>
        json(info({ can_change: false, reason: "Materials can't be changed once you have started the exam",
          files: [{ id: "m_1", name: "a.txt", kind: "txt", media_type: "text/plain", size: 5, created_at: "" }], used_bytes: 5 })),
      ),
    );
    render(<MaterialsUpload examId="e_1" />);
    expect(await screen.findByText(/can't be changed once you have started/)).toBeInTheDocument();
    expect(screen.queryByTestId("materials-drop")).toBeNull();
    expect(screen.queryByRole("button", { name: /Remove/ })).toBeNull();
  });

  it("checks count, size and type before uploading", () => {
    expect(checkFile({ name: "a.PDF", size: 10 }, 0, 0)).toBeNull();
    expect(checkFile({ name: "a.md", size: 10 }, 0, 0)).toBeNull();
    expect(checkFile({ name: "a.docx", size: 10 }, 0, 0)).toMatch(/use a PDF/);
    expect(checkFile({ name: "a.pdf", size: 0 }, 0, 0)).toMatch(/empty/);
    expect(checkFile({ name: "a.pdf", size: 10 }, 5, 0)).toMatch(/at most 5/);
    expect(checkFile({ name: "a.pdf", size: 2 }, 1, 20 * 1024 * 1024 - 1)).toMatch(/20 MB/);
    expect(checkFile({ name: "a.pdf", size: 2 }, 1, 20 * 1024 * 1024 - 2)).toBeNull();
    expect(formatBytes(900)).toBe("900 B");
    expect(formatBytes(2048)).toBe("2 KB");
    expect(formatBytes(15 * 1024 * 1024)).toBe("15 MB");
  });
});

// ---------------------------------------------------------------- teacher settings
describe("teacher: tools for students", () => {
  function Settings({ initial }: { initial: ExamToolsValue }) {
    const [v, setV] = useState(initial);
    return (
      <>
        <ExamToolsSettings value={v} onChange={setV} />
        <output data-testid="v">{JSON.stringify(v)}</output>
      </>
    );
  }
  const value = () => JSON.parse(screen.getByTestId("v").textContent ?? "{}") as ExamToolsValue;

  it("ticks a calculator, open book and websites", async () => {
    render(<Settings initial={{ calculator: "off", openBook: false, websites: [] }} />);
    fireEvent.click(screen.getByRole("checkbox", { name: /Calculator/ }));
    expect(value().calculator).toBe("scientific");
    fireEvent.click(screen.getByRole("radio", { name: /Graphing/ }));
    expect(value().calculator).toBe("graphing");
    fireEvent.click(screen.getByRole("checkbox", { name: /Open book/ }));
    expect(value().openBook).toBe(true);
    fireEvent.click(screen.getByRole("checkbox", { name: /Allowed websites/ }));
    const site = screen.getByLabelText("Website to allow");
    await userEvent.type(site, "docs.python.org/3/library{Enter}");
    expect(screen.getByText(/Use only the site name/)).toBeInTheDocument();
    await userEvent.clear(site);
    await userEvent.type(site, "https://Docs.Python.org/");
    fireEvent.click(screen.getByRole("button", { name: "Add" }));
    expect(value().websites).toEqual(["docs.python.org"]);
    await userEvent.type(site, "docs.python.org{Enter}");
    expect(screen.getByText(/already on the list/)).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Remove docs.python.org" }));
    expect(value().websites).toEqual([]);
    fireEvent.click(screen.getByRole("checkbox", { name: /Calculator/ }));
    expect(value().calculator).toBe("off");
  });

  it("draft round trip; allowed apps always [] and no tools on paper / website exams", () => {
    const d = draftFromExam({
      title: "x", duration_min: 5, questions: [], calculator: "graphing", open_book: true,
      websites: ["a.com"], allowed_apps: ["excel"],
    } as unknown as Exam);
    expect([d.calculator, d.openBook, d.websites]).toEqual(["graphing", true, ["a.com"]]);
    const input = toExamInput(d);
    expect(input).toMatchObject({ calculator: "graphing", open_book: true, websites: ["a.com"], allowed_apps: [] });
    expect(toExamInput({ ...d, delivery: "paper" })).toMatchObject({ calculator: "off", open_book: false, websites: [] });
    const fresh = draftFromExam();
    expect(toExamInput(fresh)).toMatchObject({ calculator: "off", open_book: false, websites: [] });
  });

  it("the editor has Tools for students and no apps list", async () => {
    setToken("teacher", "T");
    vi.stubGlobal("fetch", vi.fn(async () => json([])));
    render(
      <MemoryRouter>
        <ExamEditor onSaved={vi.fn()} cancelTo="/teacher" />
      </MemoryRouter>,
    );
    expect(screen.getByRole("heading", { name: "Tools for students" })).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: /More settings/ }));
    expect(screen.queryByText("Apps students may use")).toBeNull();
  });
});

// ---------------------------------------------------------------- bridge
describe("bridge: allowed_sites", () => {
  it("goes with lock and session when the page gives it", async () => {
    const postMessage = vi.fn();
    (window as unknown as { webkit: unknown }).webkit = { messageHandlers: { proctora: { postMessage } } };
    void bridge.requestLock(50, { attempt_id: "a_1", token: "t", session: "s", allowed_apps: [], allowed_sites: ["docs.python.org"] });
    expect(postMessage).toHaveBeenCalledWith({
      action: "lock", attempt_id: "a_1", token: "t", session: "s", allowed_apps: [], allowed_sites: ["docs.python.org"],
    });
    void bridge.requestLock(50, { allowed_apps: [], allowed_sites: [] });
    expect(postMessage).toHaveBeenLastCalledWith({ action: "lock", offline: true, allowed_apps: [], allowed_sites: [] });
    bridge.setExamSession({ attempt_id: "a_1", token: "t", session: "s", allowed_sites: ["a.com"] });
    expect(postMessage).toHaveBeenLastCalledWith({
      action: "session", attempt_id: "a_1", token: "t", session: "s", allowed_apps: [], allowed_sites: ["a.com"],
    });
  });
});
