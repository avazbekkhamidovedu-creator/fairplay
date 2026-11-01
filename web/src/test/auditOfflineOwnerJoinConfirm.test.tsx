import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeAll, beforeEach, describe, expect, it, vi } from "vitest";
import { setToken, studentClassesApi, tokenKey } from "../api/client";
import { JoinClass } from "../components/student/JoinClass";
import { readCrossed, writeCrossed } from "../lib/examPrefs";
import { clearExamDataOnSignOut, encryptPackage, readOfflineRecord, saveOfflineRecord, startOffline } from "../lib/offline";
import { PENDING_JOIN_KEY } from "../pages/student/JoinLinkPage";

const b64url = (s: string) => btoa(s).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");
const tokenFor = (uid: number) => `${b64url(`${uid}:student:9999999999:0:abcd`)}.sig`;

let subtle: SubtleCrypto;
beforeAll(async () => {
  if (!globalThis.crypto?.subtle) {
    const mod = "node:crypto";
    const { webcrypto } = (await import(/* @vite-ignore */ mod)) as { webcrypto: Crypto };
    vi.stubGlobal("crypto", webcrypto);
  }
  subtle = globalThis.crypto.subtle;
});

describe("offline record belongs to one account", () => {
  beforeEach(() => localStorage.clear());

  it("another student does not resume the record or see its cross-outs", async () => {
    const exam = { exam_id: "E", title: "T", course: "C", duration_min: 30, questions: [] };
    const pkg = await encryptPackage(exam, "123456", "E", subtle, 1000);
    localStorage.setItem("fairplay:pkg:E", JSON.stringify(pkg));
    setToken("student", tokenFor(1));
    const a = await startOffline("E", "123456", subtle, 1000);
    writeCrossed("offline:E", { q1: [2] });
    expect(readOfflineRecord("E")?.owner).toBe("1");
    const again = await startOffline("E", "123456", subtle, 1000);
    expect(again.started_at).toBe(a.started_at);

    setToken("student", tokenFor(2));
    const b = await startOffline("E", "123456", subtle, 1000);
    expect(readOfflineRecord("E")?.owner).toBe("2");
    expect(b.deadline_at).not.toBe(a.deadline_at);
    expect(readCrossed("offline:E")).toEqual({});
  });

  it("a record saved without an owner resumes and is stamped", async () => {
    const exam = { exam_id: "E", title: "T", course: "C", duration_min: 30, questions: [] };
    const pkg = await encryptPackage(exam, "123456", "E", subtle, 1000);
    localStorage.setItem("fairplay:pkg:E", JSON.stringify(pkg));
    const view = { started_at: "s", deadline_at: "d" } as never;
    localStorage.setItem("fairplay:offline:E", JSON.stringify({ exam_id: "E", started_at: "s", view }));
    setToken("student", tokenFor(1));
    const got = await startOffline("E", "123456", subtle, 1000);
    expect(got).toEqual(view);
    expect(readOfflineRecord("E")?.owner).toBe("1");
  });

  it("a student 401 keeps unsent offline answers; a full sign-out removes them", () => {
    saveOfflineRecord({ exam_id: "E", started_at: "x", view: {} as never });
    localStorage.setItem("fairplay:pkg:E", "{}");
    localStorage.setItem("proctora:attempt:offline:E", JSON.stringify({ client_seq: 3, synced: 1 }));
    writeCrossed("offline:E", { q1: [1] });
    localStorage.setItem("fairplay:session:S", "{}");
    sessionStorage.setItem(PENDING_JOIN_KEY, "K7QM3XPA");
    clearExamDataOnSignOut(true);
    expect(readOfflineRecord("E")).not.toBeNull();
    expect(localStorage.getItem("fairplay:pkg:E")).not.toBeNull();
    expect(localStorage.getItem("proctora:attempt:offline:E")).not.toBeNull();
    expect(readCrossed("offline:E")).toEqual({ q1: [1] });
    expect(localStorage.getItem("fairplay:session:S")).toBeNull();
    expect(sessionStorage.getItem(PENDING_JOIN_KEY)).toBeNull();
    clearExamDataOnSignOut();
    expect(readOfflineRecord("E")).toBeNull();
    expect(localStorage.getItem("proctora:attempt:offline:E")).toBeNull();
  });

  it("sign-out removes offline cross-outs and the pending join code", () => {
    saveOfflineRecord({ exam_id: "E", started_at: "x", view: {} as never });
    writeCrossed("offline:E", { q1: [1] });
    sessionStorage.setItem(PENDING_JOIN_KEY, "K7QM3XPA");
    clearExamDataOnSignOut();
    expect(readCrossed("offline:E")).toEqual({});
    expect(sessionStorage.getItem(PENDING_JOIN_KEY)).toBeNull();
    expect(localStorage.getItem(tokenKey("student"))).toBeNull();
  });
});

describe("class join link needs a click", () => {
  afterEach(() => {
    sessionStorage.clear();
    vi.restoreAllMocks();
  });

  it("does not join on mount; joins on Confirm", async () => {
    const join = vi.spyOn(studentClassesApi, "join").mockResolvedValue({ id: 1, name: "Trap", teacher: "Other" } as never);
    sessionStorage.setItem(PENDING_JOIN_KEY, "K7QM3XPA");
    render(<JoinClass />);
    expect(await screen.findByText("Join this class?")).toBeInTheDocument();
    expect(join).not.toHaveBeenCalled();
    await userEvent.click(screen.getByRole("button", { name: "Confirm" }));
    expect(join).toHaveBeenCalledWith("K7QM3XPA");
    expect(sessionStorage.getItem(PENDING_JOIN_KEY)).toBeNull();
  });

  it("Cancel clears the code without joining", async () => {
    const join = vi.spyOn(studentClassesApi, "join");
    sessionStorage.setItem(PENDING_JOIN_KEY, "K7QM3XPA");
    render(<JoinClass />);
    await userEvent.click(await screen.findByRole("button", { name: "Cancel" }));
    expect(join).not.toHaveBeenCalled();
    expect(sessionStorage.getItem(PENDING_JOIN_KEY)).toBeNull();
  });
});
