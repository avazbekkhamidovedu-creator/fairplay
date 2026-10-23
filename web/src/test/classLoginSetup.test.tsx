import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import LoginPage from "../pages/LoginPage";
import { checkNewLogin } from "../pages/ClassLoginSetup";

/** Bridge v2 §4: first sign-in with a temporary class login. */

type Reply = { status: number; body?: unknown };
type Call = { url: string; method: string; headers: Record<string, string>; body: unknown };

let calls: Call[];
let replies: Record<string, Reply[]>;

const SETUP = { setup_token: "SETUP-TOK", class: { name: "CS 101 Section A", course: "CS 101 Programming" } };
const IDENT = { identity_token: "IDENT-TOK", name: "Bob Li", netid: "bl642" };
const SESSION = { token: "SESSION-TOK", role: "student", name: "Bob Li" };

function reply(path: string, ...r: Reply[]) {
  replies[path] = r;
}

beforeEach(() => {
  calls = [];
  replies = {};
  vi.stubGlobal(
    "fetch",
    vi.fn(async (url: string, init?: RequestInit) => {
      if (url === "/api/health") return new Response(JSON.stringify({ ok: true }), { status: 200 });
      calls.push({
        url,
        method: init?.method ?? "GET",
        headers: (init?.headers ?? {}) as Record<string, string>,
        body: init?.body ? JSON.parse(String(init.body)) : undefined,
      });
      const q = replies[url] ?? [];
      const r = q.length > 1 ? q.shift()! : q[0] ?? { status: 404, body: { detail: "Not found" } };
      return new Response(r.body === undefined ? "" : JSON.stringify(r.body), { status: r.status });
    }),
  );
});

afterEach(() => {
  vi.unstubAllGlobals();
  sessionStorage.clear();
});

function renderLogin() {
  render(
    <MemoryRouter initialEntries={["/student/login"]}>
      <Routes>
        <Route path="/student/login" element={<LoginPage role="student" />} />
        <Route path="/student" element={<p>Student home</p>} />
      </Routes>
    </MemoryRouter>,
  );
}

async function signInWithClassLogin(user: ReturnType<typeof userEvent.setup>) {
  reply("/api/auth/login", { status: 200, body: SETUP });
  await user.type(screen.getByLabelText("Username"), "cs101a-m4tz");
  await user.type(screen.getByLabelText("Password"), "river-cotton-58{Enter}");
  await screen.findByRole("heading", { name: "Who are you?" });
}

async function identify(user: ReturnType<typeof userEvent.setup>) {
  await user.type(screen.getByLabelText("First name"), "Bob");
  await user.type(screen.getByLabelText("Last name"), "Li");
  await user.type(screen.getByLabelText("NetID"), "bl642{Enter}");
}

function storageValues(): string {
  const all: string[] = [];
  for (const s of [localStorage, sessionStorage]) {
    for (let i = 0; i < s.length; i++) all.push(`${s.key(i)}=${s.getItem(s.key(i)!)}`);
  }
  return all.join("\n");
}

describe("class login set-up", () => {
  it("shows the first-time hint under the sign-in form", async () => {
    renderLogin();
    expect(screen.getByText("First time? Use the class login your professor gave you.")).toBeInTheDocument();
  });

  it("full happy path: identify, choose a login, stored like a normal sign-in", async () => {
    const user = userEvent.setup();
    renderLogin();
    await signInWithClassLogin(user);

    // step 1
    expect(screen.getByText("CS 101 Section A · CS 101 Programming")).toBeInTheDocument();
    expect(screen.getByText("Step 1 of 2")).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "Who are you?" })).toHaveFocus();
    expect(screen.getByText("Forgot your login? Ask your professor to reset it.")).toBeInTheDocument();
    expect(screen.getByLabelText("NetID")).toHaveAttribute("autocomplete", "off");
    expect(localStorage.getItem("proctora:token:student")).toBeNull();

    reply("/api/setup/identify", { status: 200, body: IDENT });
    await identify(user);
    await screen.findByRole("heading", { name: "Choose your own login" });
    const idCall = calls.find((c) => c.url === "/api/setup/identify")!;
    expect(idCall.method).toBe("POST");
    expect(idCall.headers.Authorization).toBe("Bearer SETUP-TOK");
    expect(idCall.body).toEqual({ first_name: "Bob", last_name: "Li", netid: "bl642" });

    // step 2
    expect(screen.getByText("Step 2 of 2")).toBeInTheDocument();
    expect(screen.getByText("Bob Li (bl642)")).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "Choose your own login" })).toHaveFocus();
    expect(screen.getByLabelText("Username")).toHaveAttribute("autocomplete", "username");
    expect(screen.getByLabelText("Password")).toHaveAttribute("autocomplete", "new-password");
    expect(screen.getByLabelText("Password again")).toHaveAttribute("autocomplete", "new-password");

    reply("/api/setup/account", { status: 200, body: SESSION });
    await user.type(screen.getByLabelText("Username"), "BobLee");
    await user.type(screen.getByLabelText("Password"), "correct horse 9");
    await user.type(screen.getByLabelText("Password again"), "correct horse 9{Enter}");

    await screen.findByRole("heading", { name: "You're set up, Bob." });
    expect(screen.getByText(/Next time sign in as/)).toHaveTextContent("Next time sign in as boblee.");
    const accCall = calls.find((c) => c.url === "/api/setup/account")!;
    expect(accCall.headers.Authorization).toBe("Bearer IDENT-TOK");
    expect(accCall.body).toEqual({ username: "boblee", password: "correct horse 9", password2: "correct horse 9" });
    expect(localStorage.getItem("proctora:token:student")).toBe("SESSION-TOK");

    await user.click(screen.getByRole("button", { name: "Go to my exams" }));
    expect(await screen.findByText("Student home")).toBeInTheDocument();
  });

  it("never writes the setup or identity token to browser storage", async () => {
    const user = userEvent.setup();
    const setItem = vi.spyOn(Storage.prototype, "setItem");
    renderLogin();
    await signInWithClassLogin(user);
    reply("/api/setup/identify", { status: 200, body: IDENT });
    await identify(user);
    await screen.findByRole("heading", { name: "Choose your own login" });
    expect(storageValues()).not.toMatch(/SETUP-TOK|IDENT-TOK/);
    reply("/api/setup/account", { status: 200, body: SESSION });
    await user.type(screen.getByLabelText("Username"), "boblee");
    await user.type(screen.getByLabelText("Password"), "correct horse 9");
    await user.type(screen.getByLabelText("Password again"), "correct horse 9{Enter}");
    await screen.findByRole("heading", { name: "You're set up, Bob." });
    const written = setItem.mock.calls.map((c) => String(c[1])).join("\n");
    expect(written).not.toMatch(/SETUP-TOK|IDENT-TOK/);
    expect(storageValues()).not.toMatch(/SETUP-TOK|IDENT-TOK/);
    setItem.mockRestore();
  });

  it("shows the server's generic identify error as is", async () => {
    const user = userEvent.setup();
    renderLogin();
    await signInWithClassLogin(user);
    const msg = "This name and NetID are not on the class list, or this NetID is already set up. Check them, or ask your professor.";
    reply("/api/setup/identify", { status: 400, body: { detail: msg } });
    await identify(user);
    expect(await screen.findByRole("alert")).toHaveTextContent(msg);
    expect(screen.getByRole("heading", { name: "Who are you?" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Continue" })).toBeEnabled();
  });

  it("503: the class list can't be checked right now", async () => {
    const user = userEvent.setup();
    renderLogin();
    await signInWithClassLogin(user);
    reply("/api/setup/identify", { status: 503, body: { detail: "Can't check the class list right now, try again in a minute" } });
    await identify(user);
    expect(await screen.findByRole("alert")).toHaveTextContent(
      "Can't check the class list right now, try again in a minute.",
    );
  });

  it("expired setup token goes back to sign-in", async () => {
    const user = userEvent.setup();
    renderLogin();
    await signInWithClassLogin(user);
    reply("/api/setup/identify", { status: 401, body: { detail: "Invalid or expired token" } });
    await identify(user);
    expect(await screen.findByRole("alert")).toHaveTextContent("Your class login session expired. Sign in again.");
    expect(screen.getByRole("heading", { name: "Student sign in" })).toBeInTheDocument();
    expect(screen.getByLabelText("Password")).toHaveValue("");
  });

  it("expired class login shows the server's message on sign-in", async () => {
    const user = userEvent.setup();
    renderLogin();
    const msg = "This class login has expired. Ask your professor for a new one.";
    reply("/api/auth/login", { status: 401, body: { detail: msg } });
    await user.type(screen.getByLabelText("Username"), "cs101a-m4tz");
    await user.type(screen.getByLabelText("Password"), "river-cotton-58{Enter}");
    expect(await screen.findByRole("alert")).toHaveTextContent(msg);
  });

  it("409: that username is taken", async () => {
    const user = userEvent.setup();
    renderLogin();
    await signInWithClassLogin(user);
    reply("/api/setup/identify", { status: 200, body: IDENT });
    await identify(user);
    await screen.findByRole("heading", { name: "Choose your own login" });
    reply("/api/setup/account", { status: 409, body: { detail: "That username is taken" } });
    await user.type(screen.getByLabelText("Username"), "alice");
    await user.type(screen.getByLabelText("Password"), "correct horse 9");
    await user.type(screen.getByLabelText("Password again"), "correct horse 9{Enter}");
    expect(await screen.findByRole("alert")).toHaveTextContent("That username is taken");
    expect(localStorage.getItem("proctora:token:student")).toBeNull();
  });

  it("checks the new login before sending", async () => {
    const user = userEvent.setup();
    renderLogin();
    await signInWithClassLogin(user);
    reply("/api/setup/identify", { status: 200, body: IDENT });
    await identify(user);
    await screen.findByRole("heading", { name: "Choose your own login" });

    await user.type(screen.getByLabelText("Username"), "bob lee");
    await user.type(screen.getByLabelText("Password"), "short");
    await user.type(screen.getByLabelText("Password again"), "short{Enter}");
    expect(await screen.findByRole("alert")).toHaveTextContent(/3 to 32 characters/);
    expect(calls.some((c) => c.url === "/api/setup/account")).toBe(false);

    await user.clear(screen.getByLabelText("Username"));
    await user.type(screen.getByLabelText("Username"), "boblee{Enter}");
    expect(screen.getByRole("alert")).toHaveTextContent("at least 10 characters");
    expect(calls.some((c) => c.url === "/api/setup/account")).toBe(false);
  });

  it("checkNewLogin matches the contract", () => {
    expect(checkNewLogin("ab", "x".repeat(10), "x".repeat(10), "bl642")).toMatch(/3 to 32/);
    expect(checkNewLogin("a".repeat(33), "x".repeat(10), "x".repeat(10), "bl642")).toMatch(/3 to 32/);
    expect(checkNewLogin("bob!", "x".repeat(10), "x".repeat(10), "bl642")).toMatch(/3 to 32/);
    expect(checkNewLogin("bob.lee_1-x", "123456789", "123456789", "bl642")).toMatch(/at least 10/);
    expect(checkNewLogin("boblee1234", "boblee1234", "boblee1234", "bl642")).toMatch(/username or NetID/);
    expect(checkNewLogin("boblee", "BL642BL642", "BL642BL642", "BL642BL642")).toMatch(/username or NetID/);
    expect(checkNewLogin("boblee", "correct horse 9", "correct horse 8", "bl642")).toMatch(/don't match/);
    expect(checkNewLogin("bob.lee_1-x", "correct horse 9", "correct horse 9", "bl642")).toBeNull();
  });

  it("disables Continue while sending", async () => {
    const user = userEvent.setup();
    renderLogin();
    await signInWithClassLogin(user);
    let release: (r: Response) => void = () => undefined;
    (fetch as unknown as ReturnType<typeof vi.fn>).mockImplementationOnce(
      () => new Promise<Response>((res) => (release = res)),
    );
    await identify(user);
    expect(screen.getByRole("button", { name: "Checking…" })).toBeDisabled();
    release(new Response(JSON.stringify(IDENT), { status: 200 }));
    await screen.findByRole("heading", { name: "Choose your own login" });
  });
});

describe("normal login unchanged", () => {
  it("stores the token and goes to /student", async () => {
    const user = userEvent.setup();
    renderLogin();
    reply("/api/auth/login", { status: 200, body: { token: "NORMAL", role: "student", name: "Alice" } });
    await user.type(screen.getByLabelText("Username"), "alice");
    await user.type(screen.getByLabelText("Password"), "alice123{Enter}");
    expect(await screen.findByText("Student home")).toBeInTheDocument();
    expect(localStorage.getItem("proctora:token:student")).toBe("NORMAL");
    expect(calls.filter((c) => c.url.startsWith("/api/setup/"))).toHaveLength(0);
  });

  it("wrong password still says invalid username or password", async () => {
    const user = userEvent.setup();
    renderLogin();
    reply("/api/auth/login", { status: 401, body: { detail: "Invalid username or password" } });
    await user.type(screen.getByLabelText("Username"), "alice");
    await user.type(screen.getByLabelText("Password"), "nope{Enter}");
    await waitFor(() => expect(screen.getByRole("alert")).toHaveTextContent("Invalid username or password."));
  });
});
