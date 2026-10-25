import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { useState } from "react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { AttemptView, Exam } from "../api/types";
import { CanvasExamInfo } from "../components/teacher/CanvasExamInfo";
import { ExternalExamSettings } from "../components/teacher/ExternalExamSettings";
import {
  defaultFinishRegex,
  detectCanvasQuizType,
  effectiveCodeSource,
  externalExamInput,
  externalSettingsFrom,
  validateExternalSettings,
  validateFinishRegex,
  isAutoCanvasHost,
  canvasLinkKind,
  CANVAS_NO_SCOPE_HINT,
  type ExternalSettingsValue,
} from "../components/teacher/externalValidation";
import { errorText, eventLabel } from "../components/teacher/util";
import { startErrorMessage } from "../components/student/StartCodeForm";
import { ApiError } from "../api/client";
import { setToken } from "../api/client";
import { setAttemptSession } from "../lib/appSession";
import * as bridge from "../lib/bridge";
import Page from "../pages/student/ExternalExamPage";

const CLASSIC = "https://canvas.dku.edu/courses/12/quizzes/34";

function canvasValue(patch: Partial<ExternalSettingsValue> = {}): ExternalSettingsValue {
  return {
    mode: "external",
    external_url: CLASSIC,
    allowed_domains: [],
    finish_url_contains: null,
    external_kind: "canvas",
    canvas_code_source: "managed",
    ...patch,
  };
}

function Harness({ initial, linked }: { initial: ExternalSettingsValue; linked?: boolean | null }) {
  const [v, setV] = useState(initial);
  return (
    <>
      <ExternalExamSettings value={v} onChange={setV} canvasLinked={linked} />
      <output data-testid="out">{JSON.stringify(externalExamInput(v))}</output>
    </>
  );
}
const out = () => JSON.parse(screen.getByTestId("out").textContent ?? "{}") as Record<string, unknown>;

describe("canvas helpers", () => {
  it("detects the quiz type from the link", () => {
    expect(detectCanvasQuizType(CLASSIC)).toBe("classic");
    expect(detectCanvasQuizType("https://canvas.dku.edu/courses/12/assignments/9")).toBe("new");
    expect(detectCanvasQuizType("https://canvas.dku.edu/courses/12")).toBeNull();
    expect(detectCanvasQuizType("nope")).toBeNull();
  });

  it("forces a typed code for New Quizzes", () => {
    expect(effectiveCodeSource(canvasValue({ canvas_quiz_type: "new", canvas_code_source: "managed" }))).toBe("manual");
    expect(effectiveCodeSource(canvasValue())).toBe("managed");
  });

  it("builds the request with a write-only code only for manual", () => {
    expect(externalExamInput(canvasValue()).canvas).toEqual({ quiz_type: "classic", access_code_source: "managed", open_book: false, removed_hosts: [] });
    const manual = externalExamInput(canvasValue({ canvas_code_source: "manual", canvas_access_code: " abc123 " }));
    expect(manual).toMatchObject({
      external_kind: "canvas",
      canvas: { quiz_type: "classic", access_code_source: "manual", access_code: " abc123 " },
    });
    expect("finish_url_regex" in manual).toBe(false);
    const custom = externalExamInput(canvasValue({ finish_url_regex: "done$" }));
    expect(custom.finish_url_regex).toBe("done$");
  });

  it("leaves other website exams unchanged", () => {
    const plain = externalExamInput({ mode: "external", external_url: "https://forms.example.org/x", allowed_domains: [], finish_url_contains: null });
    expect(plain).toEqual({ mode: "external", external_url: "https://forms.example.org/x", allowed_domains: [], finish_url_contains: null });
  });

  it("reads a saved Canvas exam without the code", () => {
    const v = externalSettingsFrom({
      mode: "external",
      external_url: CLASSIC,
      allowed_domains: ["canvas.dku.edu"],
      external_kind: "canvas",
      canvas: { quiz_type: "classic", access_code_source: "manual", code_status: "set" },
      finish_url_regex: defaultFinishRegex("classic"),
    });
    expect(v).toMatchObject({ external_kind: "canvas", canvas_code_source: "manual", canvas_access_code: "", canvas_code_stored: true, finish_url_regex: null });
    expect(validateExternalSettings(v).canvas_access_code).toBeUndefined();
  });

  it("validates the typed code and the finish pattern", () => {
    const manual = canvasValue({ canvas_code_source: "manual" });
    expect(validateExternalSettings(manual).canvas_access_code).toMatch(/Type the access code/);
    expect(validateExternalSettings({ ...manual, canvas_access_code: "abc" }).canvas_access_code).toMatch(/4 to 64/);
    expect(validateExternalSettings({ ...manual, canvas_access_code: "abcd" }).canvas_access_code).toBeUndefined();
    expect(validateExternalSettings(canvasValue({ external_url: "http://canvas.dku.edu/courses/1/quizzes/2" })).external_url).toMatch(/https/);
    expect(validateFinishRegex("(")).toMatch(/not closed/);
    expect(validateFinishRegex("(?<=a)b")).toMatch(/Look-behind/);
    expect(validateFinishRegex("(a)\\1")).toMatch(/Back-references/);
    expect(validateFinishRegex("(a")).toMatch(/not closed/);
    expect(validateFinishRegex("x".repeat(301))).toMatch(/300/);
    expect(validateFinishRegex("")).toBeNull();
  });

  it("names the Canvas events", () => {
    expect(eventLabel("canvas_code_issued")).toBe("Canvas access code typed by the app");
    expect(eventLabel("canvas_code_failed")).toMatch(/did not work/);
    expect(eventLabel("client_canvas_upload_unsupported")).toMatch(/Canvas file upload/);
    expect(eventLabel("canvas_sso_popup")).toMatch(/sign-in/);
  });
});

describe("Canvas settings form", () => {
  it("picks Canvas, detects the type and shows the automatic code help", () => {
    render(<Harness initial={canvasValue({ external_kind: null })} linked={false} />);
    fireEvent.click(screen.getByLabelText(/Canvas quiz/));
    expect(screen.getByTestId("canvas-type-hint")).toHaveTextContent("Detected from the link: Classic Quiz");
    expect(screen.getByTestId("canvas-link-warning")).toHaveTextContent(/Link your Canvas account first/);
    expect(screen.getByTestId("canvas-chips")).toHaveTextContent("canvas.dku.edu");
    expect(screen.getByTestId("canvas-finish")).toHaveTextContent(defaultFinishRegex("classic"));
    expect(out()).toMatchObject({ external_kind: "canvas", canvas: { quiz_type: "classic", access_code_source: "managed" } });
  });

  it("hides the link warning once an account is linked", () => {
    render(<Harness initial={canvasValue()} linked />);
    expect(screen.queryByTestId("canvas-link-warning")).toBeNull();
  });

  it("warns on New Quizzes and asks for the code", () => {
    render(<Harness initial={canvasValue()} linked />);
    fireEvent.change(screen.getByLabelText("Quiz type"), { target: { value: "new" } });
    expect(screen.getByTestId("canvas-new-warning")).toHaveTextContent(/cannot be set up automatically/);
    expect(screen.getByLabelText(/FairPlay sets and changes it/)).toBeDisabled();
    fireEvent.change(screen.getByLabelText("Canvas access code"), { target: { value: "secret99" } });
    expect(out().canvas).toEqual({ quiz_type: "new", access_code_source: "manual", access_code: "secret99", open_book: false, removed_hosts: [] });
  });

  it("types the code in a hidden field and sends an override pattern", () => {
    render(<Harness initial={canvasValue()} linked />);
    fireEvent.click(screen.getByLabelText(/I will type the code/));
    const code = screen.getByLabelText("Canvas access code");
    expect(code).toHaveAttribute("type", "password");
    fireEvent.change(screen.getByLabelText(/Finish pattern/), { target: { value: "thanks$" } });
    expect(out().finish_url_regex).toBe("thanks$");
  });

  it("shows nothing about Canvas for any other website", () => {
    render(<Harness initial={{ mode: "external", external_url: "https://forms.example.org/x", allowed_domains: [], finish_url_contains: null }} />);
    expect(screen.queryByTestId("canvas-settings")).toBeNull();
    expect(screen.getByLabelText("Exam website address")).toBeInTheDocument();
  });
});

describe("finish pattern rules", () => {
  it("accepts the default patterns", () => {
    for (const t of ["classic", "new", "assignment"] as const) expect(validateFinishRegex(defaultFinishRegex(t))).toBeNull();
    expect(validateFinishRegex("done|thanks")).toBeNull();
    expect(validateFinishRegex("(?:ab)+c")).toBeNull();
    expect(validateFinishRegex("a{1,100}")).toBeNull();
  });

  it.each([
    ["a(?=b)", /Look-ahead/],
    ["a(?!b)", /Look-ahead/],
    ["(?<!a)b", /Look-behind/],
    ["(a)\\1", /Back-references/],
    ["(a)\\k<x>", /Back-references/],
    ["(?P<n>a)", /Named groups/],
    ["(?<n>a)", /Named groups/],
    ["a{,3}", /\{,n\}/],
    ["(a|b)+", /repeated/],
    ["(a+)+", /repeated/],
    ["((a|b))*", /repeated/],
    ["a{101}", /at most 100/],
    ["a{1,101}", /at most 100/],
    ["a**", /follow another repeat/],
    ["a*+", /Possessive/],
    ["a(?i)", /plain/],
    ["((a))*", /repeated/],
    ["(?:(?:a)b){2,}", /repeated/],
    ["(a|b){1}", /repeated/],
    ["[a", /not closed/],
    ["a)", /no matching/],
    ["a\\", /backslash/],
  ])("refuses %s", (p, msg) => {
    expect(validateFinishRegex(p)).toMatch(msg);
  });

  it("accepts what the server accepts: a leading (?i) and groups that are not repeated", () => {
    for (const ok of ["(?i)thanks", "(?is)a", "(?:(?:a)b)?c", "(ab){2}", "((a)b)c", "/quizzes/\\d+/(submissions/\\d+|history)"]) {
      expect(validateFinishRegex(ok)).toBeNull();
    }
  });

  it("limits open-ended repeats to two", () => {
    expect(validateFinishRegex("a*b*")).toBeNull();
    expect(validateFinishRegex("a*b*c*")).toMatch(/at most 2/);
    expect(validateFinishRegex(".*".repeat(8) + "x")).not.toBeNull();
    expect(validateFinishRegex("a+x{2,}y{3,}")).toMatch(/at most 2/);
  });

  it("refuses neighbouring repeats that can match the same characters", () => {
    for (const bad of [".*.*x", "\\w+\\w+", ".+.*", "a+a+", "x+X+", "(.*).*x", "\\d+\\d*x", "[ab]+[bc]*", "\\D+\\w+"])
      expect(validateFinishRegex(bad), bad).toMatch(/Two repeats in a row/);
    for (const ok of ["a+b+", "\\d+\\s+", ".*x.*", "a+\\d*", "\\w+\\s*done", "\\d+\\.\\d+"])
      expect(validateFinishRegex(ok), ok).toBeNull();
  });

  it("does not use the JS engine to decide", () => {
    expect(validateFinishRegex("(?<n>a)")).not.toBeNull();
    expect(validateFinishRegex("a{,3}")).not.toBeNull();
    expect(validateFinishRegex("[[:alpha:]]+")).toBeNull();
  });
});

describe("Canvas open book", () => {
  it("is off by default, has a name, and lists what students can open", () => {
    render(<Harness initial={canvasValue()} linked />);
    const box = screen.getByRole("checkbox", { name: /Open book: students may also open this course's pages, modules and files/ });
    expect(box).not.toBeChecked();
    expect(screen.getByTestId("canvas-access-line")).toHaveTextContent(
      "Students can sign in to Canvas and open the dashboard, the course home and the quiz and assignment lists, then this quiz. Once it starts they can only open that quiz.",
    );
    expect(out()).toMatchObject({ canvas: { open_book: false } });
  });

  it("adds the open-book items and sends open_book when ticked", () => {
    render(<Harness initial={canvasValue()} linked />);
    fireEvent.click(screen.getByRole("checkbox", { name: /Open book/ }));
    expect(screen.getByTestId("canvas-access-line")).toHaveTextContent(/only open that quiz, plus this course's pages, modules and files \(view only; downloads stay blocked\)\./);
    expect(out()).toMatchObject({ canvas: { open_book: true } });
    fireEvent.click(screen.getByRole("checkbox", { name: /Open book/ }));
    expect(out()).toMatchObject({ canvas: { open_book: false } });
  });

  it("reads the saved choice and does not show the box for other websites", () => {
    const v = externalSettingsFrom({
      mode: "external",
      external_url: CLASSIC,
      external_kind: "canvas",
      canvas: { quiz_type: "classic", access_code_source: "manual", open_book: true },
    });
    expect(v.canvas_open_book).toBe(true);
    expect(externalExamInput(v).canvas).toMatchObject({ open_book: true });
    render(<Harness initial={v} linked />);
    expect(screen.getByRole("checkbox", { name: /Open book/ })).toBeChecked();
    expect(out()).toMatchObject({ canvas: { open_book: true } });
    expect(externalSettingsFrom({ mode: "external", external_url: CLASSIC, external_kind: "canvas" }).canvas_open_book).toBe(false);
  });

  it("says assignment for an assignment link", () => {
    render(<Harness initial={canvasValue({ external_url: "https://canvas.dku.edu/courses/12/assignments/9" })} linked />);
    expect(screen.getByTestId("canvas-access-line")).toHaveTextContent(/then this assignment\. Once it starts they can only open that assignment\./);
  });

  it("shows the link hint and hides open book when the link has no quiz", () => {
    render(<Harness initial={canvasValue({ external_url: "https://canvas.dku.edu/courses/12" })} linked />);
    expect(screen.getByTestId("canvas-no-scope")).toHaveTextContent(CANVAS_NO_SCOPE_HINT);
    expect(screen.queryByRole("checkbox", { name: /Open book/ })).toBeNull();
    expect(screen.queryByTestId("canvas-access-line")).toBeNull();
    expect(validateExternalSettings(canvasValue({ external_url: "https://canvas.dku.edu/courses/12" })).external_url).toBe(CANVAS_NO_SCOPE_HINT);
    expect(validateExternalSettings(canvasValue({ external_url: "https://canvas.dku.edu/courses/12/quizzes/3/edit" })).external_url).toBeUndefined();
  });

  it("reads the link kind like the server", () => {
    expect(canvasLinkKind("https://c.test/courses/1/quizzes/2/take")).toBe("quizzes");
    expect(canvasLinkKind("https://c.test/courses/1/assignments/2;x=1")).toBe("assignments");
    expect(canvasLinkKind("https://c.test/courses/1/quizzes/2x")).toBeNull();
    expect(canvasLinkKind("https://c.test/courses/1")).toBeNull();
    expect(canvasLinkKind("nope")).toBeNull();
  });

  it("always sends removed_hosts, even when the last host is added back", () => {
    render(<Harness initial={canvasValue({ canvas_removed_hosts: ["login.microsoft.com"] })} linked />);
    expect((out().canvas as { removed_hosts: string[] }).removed_hosts).toEqual(["login.microsoft.com"]);
    fireEvent.click(screen.getByRole("button", { name: "Add login.microsoft.com back" }));
    expect((out().canvas as { removed_hosts: string[] }).removed_hosts).toEqual([]);
  });

  it("loads the removed hosts of a saved exam so they show as removed", () => {
    const v = externalSettingsFrom({
      mode: "external",
      external_url: CLASSIC,
      external_kind: "canvas",
      allowed_domains: ["canvas.dku.edu", "login.microsoftonline.com"],
      canvas: { quiz_type: "classic", access_code_source: "manual", removed_hosts: ["login.microsoft.com"] },
    });
    expect(v.canvas_removed_hosts).toEqual(["login.microsoft.com"]);
    render(<Harness initial={v} linked />);
    expect(screen.getByRole("button", { name: "Add login.microsoft.com back" })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Remove login.microsoft.com" })).toBeNull();
    expect((out().canvas as { removed_hosts: string[] }).removed_hosts).toEqual(["login.microsoft.com"]);
  });

  it("uses the sign-in sites the server reports", () => {
    const v = externalSettingsFrom({
      mode: "external",
      external_url: CLASSIC,
      external_kind: "canvas",
      allowed_domains: ["canvas.dku.edu", "sso.school.edu"],
      canvas: { quiz_type: "classic", access_code_source: "manual", sign_in_hosts: ["sso.school.edu"] },
    });
    expect(v.allowed_domains).toEqual([]);
    render(<Harness initial={v} linked />);
    expect(screen.getByRole("button", { name: "Remove sso.school.edu" })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Remove login.microsoft.com" })).toBeNull();
  });

  it("forces a typed code for assignments too", () => {
    const link = "https://canvas.dku.edu/courses/12/assignments/9";
    expect(effectiveCodeSource(canvasValue({ external_url: link, canvas_quiz_type: "assignment" }))).toBe("manual");
    expect(effectiveCodeSource(canvasValue({ external_url: link, canvas_quiz_type: "assignment", canvas_code_source: "managed" }))).toBe("manual");
    expect(effectiveCodeSource(canvasValue())).toBe("managed");
    expect(validateExternalSettings(canvasValue({ external_url: link, canvas_quiz_type: "assignment" })).canvas_access_code).toMatch(/Type the access code/);
    render(<Harness initial={canvasValue({ external_url: link, canvas_quiz_type: "assignment" })} linked />);
    expect(screen.getByLabelText(/FairPlay sets and changes it/)).toBeDisabled();
    expect(screen.getByLabelText("Canvas access code")).toBeInTheDocument();
  });

  it("refuses control characters in the typed code", () => {
    const v = canvasValue({ canvas_code_source: "manual", canvas_access_code: "ab\u0007cd" });
    expect(validateExternalSettings(v).canvas_access_code).toMatch(/letters, numbers/);
    expect(validateExternalSettings({ ...v, canvas_access_code: "abc d" }).canvas_access_code).toBeUndefined();
  });

  it("is absent from a plain website exam", () => {
    render(<Harness initial={canvasValue({ external_kind: null })} />);
    expect(screen.queryByRole("checkbox", { name: /Open book/ })).toBeNull();
    expect("canvas" in out()).toBe(false);
  });

  it("shows the access line in the teacher exam view", () => {
    const base = { id: "e1", mode: "external", external_kind: "canvas" } as unknown as Exam;
    const canvas = { quiz_type: "classic" as const, access_code_source: "manual" as const, code_status: "set" as const };
    const SCOPE = { course_id: "1", item_kind: "quizzes" as const, item_id: "2" };
    const { rerender } = render(<CanvasExamInfo exam={{ ...base, canvas: { ...canvas, scope: SCOPE } }} />);
    expect(screen.getByTestId("canvas-access-line")).toHaveTextContent(
      "Students can sign in to Canvas and open the dashboard, the course home and the quiz and assignment lists, then this quiz. Once it starts they can only open that quiz.",
    );
    rerender(<CanvasExamInfo exam={{ ...base, canvas: { ...canvas, scope: { course_id: "1", item_kind: "assignments", item_id: "2" } } }} />);
    expect(screen.getByTestId("canvas-access-line")).toHaveTextContent(/then this assignment/);
    rerender(<CanvasExamInfo exam={{ ...base, canvas: { ...canvas, scope: null } }} />);
    expect(screen.queryByTestId("canvas-access-line")).toBeNull();
    rerender(<CanvasExamInfo exam={{ ...base, canvas: { ...canvas, open_book: true, scope: SCOPE } }} />);
    expect(screen.getByTestId("canvas-access-line")).toHaveTextContent(/plus this course's pages, modules and files/);
  });
});

describe("Canvas form details", () => {
  it("gives the code group and the code input different names", () => {
    render(<Harness initial={canvasValue({ canvas_code_source: "manual" })} linked />);
    expect(screen.getByRole("group", { name: "How the access code is set" })).toBeInTheDocument();
    expect(screen.getByLabelText("Canvas access code")).toHaveAttribute("type", "password");
    expect(screen.queryByLabelText("Access code")).toBeNull();
    expect(screen.getByLabelText(/I will type the code/)).toBeChecked();
  });

  it("shows and hides the code, turns autocomplete off and keeps the value as typed", () => {
    render(<Harness initial={canvasValue({ canvas_code_source: "manual" })} linked />);
    const input = screen.getByLabelText("Canvas access code");
    expect(input).toHaveAttribute("autocomplete", "new-password");
    fireEvent.change(input, { target: { value: " ab cd " } });
    expect(screen.getByTestId("canvas-code-space-warning")).toHaveTextContent(/exactly as you entered/);
    expect((out().canvas as { access_code: string }).access_code).toBe(" ab cd ");
    fireEvent.click(screen.getByRole("button", { name: "Show access code" }));
    expect(input).toHaveAttribute("type", "text");
    fireEvent.click(screen.getByRole("button", { name: "Hide access code" }));
    expect(input).toHaveAttribute("type", "password");
    fireEvent.change(input, { target: { value: "abcd" } });
    expect(screen.queryByTestId("canvas-code-space-warning")).toBeNull();
  });

  it("does not accept a code made only of spaces", () => {
    const v = canvasValue({ canvas_code_source: "manual", canvas_access_code: "      " });
    expect(validateExternalSettings(v).canvas_access_code).toMatch(/Type the access code/);
    expect("access_code" in (externalExamInput(v).canvas ?? {})).toBe(false);
  });

  it("explains the finish pattern rule under the field", () => {
    render(<Harness initial={canvasValue()} linked />);
    const field = screen.getByLabelText(/Finish pattern/);
    expect(field).toHaveAccessibleDescription(/no look-ahead, look-behind, back-references or named groups/);
  });

  it("hides the address-contains field for Canvas and sends none", () => {
    render(<Harness initial={canvasValue({ finish_url_contains: "formResponse" })} linked />);
    expect(screen.queryByLabelText(/Finish automatically when the page address contains/)).toBeNull();
    expect(screen.getByTestId("canvas-finish")).toHaveTextContent(/Only this finish pattern is used/);
    expect(out().finish_url_contains).toBeNull();
  });

  it("keeps the address-contains field for other websites", () => {
    render(<Harness initial={{ mode: "external", external_url: "https://forms.example.org/x", allowed_domains: [], finish_url_contains: null }} />);
    expect(screen.getByLabelText(/Finish automatically when the page address contains/)).toBeInTheDocument();
  });

  it("shows sign-in sites as removable chips and locks the Canvas site", () => {
    render(<Harness initial={canvasValue({ allowed_domains: ["extra.example.org"] })} linked />);
    const chips = screen.getByTestId("canvas-chips");
    expect(chips).toHaveTextContent("canvas.dku.edu");
    expect(screen.queryByRole("button", { name: "Remove canvas.dku.edu" })).toBeNull();
    expect(chips).toHaveTextContent("login.microsoftonline.com");
    fireEvent.click(screen.getByRole("button", { name: "Remove login.microsoftonline.com" }));
    expect(chips).not.toHaveTextContent("login.microsoftonline.com");
    expect((out().canvas as { removed_hosts: string[] }).removed_hosts).toEqual(["login.microsoftonline.com"]);
    fireEvent.click(screen.getByRole("button", { name: "Add login.microsoftonline.com back" }));
    expect(chips).toHaveTextContent("login.microsoftonline.com");
    expect((out().canvas as { removed_hosts: string[] }).removed_hosts).toEqual([]);
    expect(chips).toHaveFocus();
    fireEvent.click(screen.getByRole("button", { name: "Remove extra.example.org" }));
    expect(out().allowed_domains).toEqual([]);
  });

  it("keeps automatic sites out of the teacher list when loading a saved exam", () => {
    const v = externalSettingsFrom({
      mode: "external",
      external_url: CLASSIC,
      external_kind: "canvas",
      canvas: { quiz_type: "classic", access_code_source: "managed" },
      allowed_domains: ["canvas.dku.edu", "*.duosecurity.com", "shib.oit.duke.edu", "extra.example.org"],
    });
    expect(v.allowed_domains).toEqual(["extra.example.org"]);
    expect(isAutoCanvasHost("*.quiz-lti-iad-prod.instructure.com", CLASSIC)).toBe(true);
    expect(isAutoCanvasHost("extra.example.org", CLASSIC)).toBe(false);
  });
});

describe("Canvas messages", () => {
  const base = { id: "e1", mode: "external", external_kind: "canvas" } as unknown as Exam;

  it("shows the canvas_notice from the teacher view", () => {
    render(<CanvasExamInfo exam={{ ...base, canvas: { quiz_type: "new", access_code_source: "manual", code_status: "not_set" }, canvas_notice: "Set the code in Canvas and enter it here." }} />);
    expect(screen.getByTestId("canvas-notice")).toHaveTextContent("Set the code in Canvas and enter it here.");
  });

  it("asks to remove the code in Canvas after close only when the server says so", () => {
    const canvas = { quiz_type: "classic" as const, access_code_source: "managed" as const, code_status: "not_set" as const };
    const { rerender } = render(<CanvasExamInfo exam={{ ...base, status: "closed", canvas: { ...canvas, remove_code_in_canvas: true } }} />);
    expect(screen.getByTestId("canvas-remove-code")).toHaveTextContent(/Remove the access code in Canvas/);
    rerender(<CanvasExamInfo exam={{ ...base, status: "closed", canvas }} />);
    expect(screen.queryByTestId("canvas-remove-code")).toBeNull();
    rerender(<CanvasExamInfo exam={{ ...base, status: "live", canvas: { ...canvas, remove_code_in_canvas: true } }} />);
    expect(screen.queryByTestId("canvas-remove-code")).toBeNull();
  });

  it("shows the canvas_code_failed message where the teacher opens an exam", () => {
    const msg = "Connect your Canvas account, or switch to a code you type.";
    expect(errorText(new ApiError(409, msg, "canvas_code_failed"))).toBe(msg);
    expect(errorText(new ApiError(409, "", "canvas_code_failed"))).toMatch(/could not set the Canvas access code/);
  });

  it("shows a Canvas refusal in plain words where a student starts", () => {
    const msg = "Canvas exams are not available on this device yet.";
    expect(startErrorMessage(new ApiError(409, msg))).toBe(msg);
    expect(startErrorMessage(new ApiError(409, "Exam is closed"))).toMatch(/not open right now/);
  });
});

describe("teacher exam view", () => {
  const base = { id: "e1", mode: "external", external_kind: "canvas" } as unknown as Exam;

  it("shows the Canvas exam and the code status", () => {
    const { rerender } = render(
      <CanvasExamInfo exam={{ ...base, canvas: { quiz_type: "classic", access_code_source: "managed", code_status: "set" } }} />,
    );
    expect(screen.getByText("Canvas exam")).toBeInTheDocument();
    expect(screen.getByTestId("canvas-code-status")).toHaveTextContent("Code set");
    rerender(<CanvasExamInfo exam={{ ...base, canvas: { quiz_type: "new", access_code_source: "manual", code_status: "not_set" } }} />);
    expect(screen.getByTestId("canvas-code-status")).toHaveTextContent("Code not set");
    rerender(<CanvasExamInfo exam={{ ...base, canvas: { quiz_type: "classic", access_code_source: "managed", code_status: "failed" } }} />);
    expect(screen.getByTestId("canvas-code-status")).toHaveTextContent("Code failed");
    expect(screen.getByRole("alert")).toHaveTextContent(/type the code/);
  });

  it("renders nothing for other exams", () => {
    const { container } = render(<CanvasExamInfo exam={{ id: "e2", mode: "external" } as unknown as Exam} />);
    expect(container).toBeEmptyDOMElement();
  });
});

describe("bridge openExternal for Canvas", () => {
  afterEach(() => {
    delete (window as unknown as { webkit?: unknown }).webkit;
    bridge._resetBridgeForTests();
  });
  const params = {
    attempt_id: "a_1",
    url: CLASSIC,
    allowed_domains: ["canvas.dku.edu"],
    finish_url_contains: null,
    deadline_at: "2026-10-25T10:30:00Z",
    server_time: "2026-10-25T10:00:00Z",
    title: "Quiz",
    token: "tok",
    session: "sess",
  };

  it("adds kind and finish_url_regex only for canvas", () => {
    const post = vi.fn();
    (window as unknown as { webkit: unknown }).webkit = { messageHandlers: { proctora: { postMessage: post } } };
    bridge.openExternal({ ...params, kind: "canvas", finish_url_regex: "x$" });
    expect(post).toHaveBeenLastCalledWith(expect.objectContaining({ action: "openExternal", kind: "canvas", finish_url_regex: "x$" }));
    bridge.openExternal(params);
    const plain = post.mock.calls[post.mock.calls.length - 1]![0] as Record<string, unknown>;
    expect("kind" in plain).toBe(false);
    expect("finish_url_regex" in plain).toBe(false);
    expect("canvas_scope" in plain).toBe(false);
  });

  it("passes canvas_scope for canvas only when it is present", () => {
    const post = vi.fn();
    (window as unknown as { webkit: unknown }).webkit = { messageHandlers: { proctora: { postMessage: post } } };
    const scope = { course_id: "12", item_kind: "quizzes" as const, item_id: "34", open_book: true };
    bridge.openExternal({ ...params, kind: "canvas", finish_url_regex: "x$", canvas_scope: scope });
    expect(post).toHaveBeenLastCalledWith(expect.objectContaining({ kind: "canvas", canvas_scope: scope }));
    bridge.openExternal({ ...params, kind: "canvas", finish_url_regex: "x$", canvas_scope: null });
    const none = post.mock.calls[post.mock.calls.length - 1]![0] as Record<string, unknown>;
    expect("canvas_scope" in none).toBe(false);
    bridge.openExternal({ ...params, canvas_scope: scope });
    const plain = post.mock.calls[post.mock.calls.length - 1]![0] as Record<string, unknown>;
    expect("canvas_scope" in plain).toBe(false);
  });
});

const attemptMock = vi.hoisted(() => vi.fn());
vi.mock("../api/client", async (importOriginal) => {
  const actual = await importOriginal<typeof import("../api/client")>();
  return { ...actual, studentApi: { ...actual.studentApi, getAttempt: attemptMock } };
});

describe("student ExternalExamPage for Canvas", () => {
  afterEach(() => vi.restoreAllMocks());

  async function run(extra: Partial<AttemptView>) {
    setToken("student", "T");
    setAttemptSession("a_1", "sess-1");
    const spy = vi.spyOn(bridge, "openExternal").mockReturnValue(true);
    vi.spyOn(bridge, "isNative").mockReturnValue(true);
    vi.spyOn(bridge, "preflightReport").mockResolvedValue({ problems: [], checks: [], apps_to_close: [] } as never);
    vi.spyOn(bridge, "requestLock").mockResolvedValue({ ok: true });
    vi.spyOn(bridge, "onNative").mockReturnValue(() => undefined);
    attemptMock.mockResolvedValue({
      attempt_id: "a_1",
      exam_id: "e1",
      title: "Quiz",
      status: "in_progress",
      mode: "external",
      external_url: CLASSIC,
      allowed_domains: ["canvas.dku.edu"],
      finish_url_contains: null,
      deadline_at: new Date(Date.now() + 3600_000).toISOString(),
      server_time: new Date().toISOString(),
      ...extra,
    });
    render(
      <MemoryRouter initialEntries={["/student/external/a_1"]}>
        <Routes>
          <Route path="/student/external/:attemptId" element={<Page />} />
        </Routes>
      </MemoryRouter>,
    );
    return spy;
  }

  it("passes kind and the finish regex to the app and shows the note", async () => {
    const spy = await run({ external_kind: "canvas", finish_url_regex: "submission-screen" });
    await waitFor(() => expect(spy).toHaveBeenCalled());
    expect(spy.mock.calls[0]![0]).toMatchObject({ kind: "canvas", finish_url_regex: "submission-screen", url: CLASSIC });
    expect(screen.getByTestId("canvas-note")).toHaveTextContent(/opens inside FairPlay/);
    expect(document.body.textContent).not.toMatch(/canvas-code|access_code/);
  });

  it("sends canvas_scope to the app when the attempt has one", async () => {
    const scope = { course_id: "12", item_kind: "quizzes" as const, item_id: "34", open_book: false };
    const spy = await run({ external_kind: "canvas", finish_url_regex: "x$", canvas_scope: scope });
    await waitFor(() => expect(spy).toHaveBeenCalled());
    expect(spy.mock.calls[0]![0]).toMatchObject({ kind: "canvas", canvas_scope: scope });
  });

  it("sends no canvas_scope when the attempt has none", async () => {
    const spy = await run({ external_kind: "canvas", finish_url_regex: "x$", canvas_scope: null });
    await waitFor(() => expect(spy).toHaveBeenCalled());
    const sent = spy.mock.calls[0]![0] as unknown as Record<string, unknown>;
    expect("canvas_scope" in sent).toBe(false);
  });

  it("says Canvas exams are not available when the app cannot open one", async () => {
    const spy = await run({ external_kind: "canvas", finish_url_regex: "x$" });
    spy.mockReturnValue(false);
    await waitFor(() => expect(screen.getByTestId("open-failed-text")).toHaveTextContent(/Canvas exams are not available on this device yet/));
  });

  it("does not change the call for other website exams", async () => {
    const spy = await run({});
    await waitFor(() => expect(spy).toHaveBeenCalled());
    const sent = spy.mock.calls[0]![0] as unknown as Record<string, unknown>;
    expect("kind" in sent).toBe(false);
    expect(screen.queryByTestId("canvas-note")).toBeNull();
  });
});

describe("Canvas page and form wording", () => {
  it("shows the code hint only for the typed source", () => {
    const first = render(<Harness initial={canvasValue({ canvas_code_source: "managed" })} linked />);
    expect(document.getElementById("exam-canvas-code-hint")).toBeNull();
    first.unmount();
    render(<Harness initial={canvasValue({ canvas_code_source: "manual" })} linked />);
    expect(document.getElementById("exam-canvas-code-hint")).not.toBeNull();
  });

  it("keeps one name on the show and hide button", () => {
    render(<Harness initial={canvasValue({ canvas_code_source: "manual" })} linked />);
    expect(screen.getByRole("button", { name: "Show access code" })).not.toHaveAttribute("aria-pressed");
  });
});
