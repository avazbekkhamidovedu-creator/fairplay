// 2026-10-24: the early upload window ("Upcoming — upload your files"), allowed websites as
// full links (start pages, Duke Box) and the sites opened with them (companions).
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { useState } from "react";
import { MemoryRouter } from "react-router-dom";
import { afterEach, describe, expect, it, vi } from "vitest";
import { setToken } from "../api/client";
import type { Exam, MaterialsInfo, StudentExamSummary } from "../api/types";
import { UploadWindowCard } from "../components/student/ExamCard";
import { WebsitesPaneContent } from "../components/student/ExamToolPanes";
import { MaterialsUpload } from "../components/student/MaterialsUpload";
import { dateTime, timeLeft } from "../components/student/format";
import { ExamToolsSettings, _resetSiteChecksForTests, type ExamToolsValue } from "../components/teacher/ExamToolsSettings";
import { lockSites, parseWebsiteEntry, siteEntries } from "../lib/websites";
import { uploadWindowExams } from "../pages/student/useStudentExams";
import { draftFromExam, toExamInput, websiteEntries } from "../pages/teacher/examDraft";

const BOX = "https://duke.app.box.com/folder/0";

function json(body: unknown, status = 200) {
  return new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });
}

afterEach(() => {
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
  _resetSiteChecksForTests();
});

function summary(over: Partial<StudentExamSummary> = {}): StudentExamSummary {
  return {
    id: "e_1", title: "Midterm", duration_min: 90, question_count: null, status: "draft", course: "MATH 201",
    room: "", scheduled_at: "2026-10-26T14:00:00Z", attempt: null,
    upload_window: { opens_at: "2026-10-24T14:00:00Z", closes_at: "2026-10-26T14:00:00Z" }, ...over,
  };
}

describe("website links", () => {
  it("splits a link into its site and start page, with an optional title", () => {
    expect(parseWebsiteEntry(BOX)).toEqual({ ok: true, host: "duke.app.box.com", link: { url: BOX, title: null } });
    expect(parseWebsiteEntry("Course folder http://Duke.App.Box.com/folder/0/somewherenew/123")).toEqual({
      ok: true, host: "duke.app.box.com",
      link: { url: "https://duke.app.box.com/folder/0/somewherenew/123", title: "Course folder" },
    });
    expect(parseWebsiteEntry("https://docs.python.org/")).toEqual({ ok: true, host: "docs.python.org", link: null });
    expect(parseWebsiteEntry("https://*.box.com/x").ok).toBe(false);
    expect(parseWebsiteEntry("https://box.com:8443/x").ok).toBe(false);
    expect(parseWebsiteEntry("docs.python.org/3/library").ok).toBe(false);
  });

  it("lists the start pages in place of their site, and hands the lock the companions", () => {
    const rows = siteEntries(["duke.app.box.com", "docs.python.org"], [{ host: "duke.app.box.com", url: BOX, title: "Course folder" }]);
    expect(rows.map((r) => [r.label, r.url, r.detail])).toEqual([
      ["Course folder", BOX, "duke.app.box.com/folder/0"],
      ["docs.python.org", "https://docs.python.org/", undefined],
    ]);
    expect(lockSites({ websites: ["a.com"], allowed_sites: ["a.com", "*.boxcdn.net"] })).toEqual(["a.com", "*.boxcdn.net"]);
    expect(lockSites({ websites: ["a.com"] })).toEqual(["a.com"]);   // an older server
  });

  it("the editor round-trips links and the upload days", () => {
    const exam = {
      id: "e_1", title: "T", duration_min: 30, questions: [], status: "draft", open_book: true,
      websites: ["duke.app.box.com", "docs.python.org"], upload_opens_days: 2,
      website_links: [{ host: "duke.app.box.com", url: BOX, title: "Course folder" }],
    } as unknown as Exam;
    expect(websiteEntries(exam.websites ?? [], exam.website_links ?? [])).toEqual([`Course folder ${BOX}`, "docs.python.org"]);
    const d = draftFromExam(exam);
    expect(d.uploadOpensDays).toBe(2);
    const input = toExamInput(d);
    expect(input.websites).toEqual([`Course folder ${BOX}`, "docs.python.org"]);
    expect(input.upload_opens_days).toBe(2);
    expect(toExamInput({ ...d, openBook: false }).upload_opens_days).toBe(0);
  });

  it("the websites pane opens a start page", () => {
    const open = vi.fn();
    render(<WebsitesPaneContent sites={["duke.app.box.com"]} links={[{ host: "duke.app.box.com", url: BOX, title: "Course folder" }]} onOpenSite={open} />);
    fireEvent.click(screen.getByRole("button", { name: /Course folder/ }));
    expect(open).toHaveBeenCalledWith("duke.app.box.com", BOX);
  });
});

describe("teacher settings", () => {
  function Settings({ initial }: { initial?: Partial<ExamToolsValue> }) {
    const [v, setV] = useState<ExamToolsValue>({ calculator: "off", openBook: true, websites: [], uploadOpensDays: 0, ...initial });
    return (
      <>
        <ExamToolsSettings value={v} onChange={setV} />
        <output data-testid="v">{JSON.stringify(v)}</output>
      </>
    );
  }

  it("takes a Box link and names the sites opened with it", async () => {
    setToken("teacher", "T");
    vi.stubGlobal(
      "fetch",
      vi.fn(async () =>
        json({ host: "duke.app.box.com", frameable: true, reason: "",
          companions: { name: "Duke Box", sites: ["app.box.com", "*.boxcdn.net", "shib.oit.duke.edu"] } }),
      ),
    );
    render(<Settings />);
    fireEvent.click(screen.getByRole("checkbox", { name: /Allowed websites/ }));
    await userEvent.type(screen.getByLabelText("Website to allow"), `Course folder ${BOX}{Enter}`);
    expect(JSON.parse(screen.getByTestId("v").textContent!).websites).toEqual([`Course folder ${BOX}`]);
    expect(screen.getByText(/starts here; all of duke.app.box.com is allowed/)).toBeInTheDocument();
    expect(await screen.findByTestId("site-companions")).toHaveTextContent(
      "Also opened so Duke Box works: app.box.com, *.boxcdn.net, shib.oit.duke.edu",
    );
    // the same link twice is refused
    await userEvent.type(screen.getByLabelText("Website to allow"), `${BOX}{Enter}`);
    expect(screen.getByText(/already on the list/)).toBeInTheDocument();
  });

  it("sets how many days before the exam students can upload", () => {
    render(<Settings />);
    const select = screen.getByTestId("upload-days") as HTMLSelectElement;
    expect(select.value).toBe("0");
    fireEvent.change(select, { target: { value: "2" } });
    expect(JSON.parse(screen.getByTestId("v").textContent!).uploadOpensDays).toBe(2);
    expect(screen.getByText(/2 days before its date with only an upload area/)).toBeInTheDocument();
  });

  it("has no upload choice when the exam is not open book", () => {
    render(<Settings initial={{ openBook: false }} />);
    expect(screen.queryByTestId("upload-days")).toBeNull();
  });
});

describe("student: the upload window", () => {
  it("lists only drafts with an open window, soonest close first", () => {
    const a = summary({ id: "a", upload_window: { opens_at: "x", closes_at: "2026-10-27T10:00:00Z" } });
    const b = summary({ id: "b" });
    const live = summary({ id: "c", status: "live", upload_window: null, question_count: 3 });
    expect(uploadWindowExams([a, live, b]).map((e) => e.id)).toEqual(["b", "a"]);
  });

  it("the card offers only the upload and says when it closes", () => {
    render(
      <MemoryRouter>
        <UploadWindowCard exam={summary()} />
      </MemoryRouter>,
    );
    expect(screen.getByText("Upcoming — upload your files")).toBeInTheDocument();
    expect(screen.getByTestId("upload-closes")).toHaveTextContent(`Uploads close ${dateTime("2026-10-26T14:00:00Z")}`);
    expect(screen.getByRole("link", { name: "Upload files" })).toHaveAttribute("href", "/student/exams/e_1/upload");
    expect(screen.queryByText(/question/i)).toBeNull();
    expect(screen.queryByRole("button", { name: /start/i })).toBeNull();
  });

  it("time left reads plainly", () => {
    const now = Date.parse("2026-10-24T10:00:00Z");
    expect(timeLeft("2026-10-26T13:00:00Z", now)).toBe("2 days 3 h");
    expect(timeLeft("2026-10-24T15:10:00Z", now)).toBe("5 h 10 min");
    expect(timeLeft("2026-10-24T10:12:00Z", now)).toBe("12 min");
    expect(timeLeft("2026-10-24T09:00:00Z", now)).toBe("");
  });

  it("the upload area shows when uploads close", async () => {
    setToken("student", "S");
    const closes = new Date(Date.now() + 26 * 3600_000).toISOString();
    const info: MaterialsInfo = {
      open_book: true, can_change: true, reason: null, used_bytes: 0, max_files: 5, max_bytes: 20 * 1024 * 1024,
      types: ["pdf", "png", "jpg", "txt", "md"], upload_opens_at: new Date().toISOString(), upload_closes_at: closes,
      files: [{ id: "m_1", name: "notes.pdf", kind: "pdf", media_type: "application/pdf", size: 9, created_at: "" }],
    };
    vi.stubGlobal("fetch", vi.fn(async () => json(info)));
    render(<MaterialsUpload examId="e_1" />);
    const w = await screen.findByTestId("upload-window");
    expect(w).toHaveTextContent(`Uploads close ${dateTime(closes)}`);
    expect(w).toHaveTextContent(/in 1 day/);
    await waitFor(() => expect(screen.getByRole("button", { name: "Remove notes.pdf" })).toBeInTheDocument());
  });
});
