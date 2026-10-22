// 2026-10-23 (review fixes): allowed websites match exactly; "*.python.org" is python.org and
// all its subdomains; no wildcard on a public suffix or shared hosting. Same rule as the apps.
import { fireEvent, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { useState } from "react";
import { describe, expect, it } from "vitest";
import { WebsitesPaneContent } from "../components/student/ExamToolPanes";
import { ExamToolsSettings, type ExamToolsValue } from "../components/teacher/ExamToolsSettings";
import { SHARED_SUFFIXES, entryMatches, hostAllowed, isSharedSuffix, normalizeWebsite, siteLabel, siteUrl } from "../lib/websites";

describe("website entries", () => {
  it("accepts one leading *. on a real site", () => {
    expect(normalizeWebsite("*.Python.org")).toEqual({ ok: true, host: "*.python.org" });
    expect(normalizeWebsite("https://*.cppreference.com/")).toEqual({ ok: true, host: "*.cppreference.com" });
    expect(normalizeWebsite("*.dku.edu.cn")).toEqual({ ok: true, host: "*.dku.edu.cn" });
    expect(normalizeWebsite("docs.python.org")).toEqual({ ok: true, host: "docs.python.org" });
  });

  it("refuses wildcards on shared suffixes and anywhere but the front", () => {
    for (const bad of [
      "*.com", "*.github.io", "*.vercel.app", "*.netlify.app", "*.pages.dev", "*.web.app", "*.co.uk", "*.com.cn",
      "*.blogspot.com", "*.herokuapp.com", "*.appspot.com", "*.com.au", "*.localhost", "*", "*.", "*.*.python.org",
      "docs.*.org", "*python.org", "*.10.0.0.1", "*.python.org/3/",
    ])
      expect(normalizeWebsite(bad).ok, bad).toBe(false);
    const r = normalizeWebsite("*.github.io");
    expect(!r.ok && r.error).toMatch(/Anyone can make a site under github.io/);
    expect(isSharedSuffix("org.au") && isSharedSuffix("com") && !isSharedSuffix("python.org")).toBe(true);
    expect(SHARED_SUFFIXES.has("vercel.app")).toBe(true);
  });

  it("matches like the apps: exact (+www.), or the whole site for *.", () => {
    expect(entryMatches("docs.python.org", "docs.python.org")).toBe(true);
    expect(entryMatches("www.docs.python.org", "docs.python.org")).toBe(true);
    expect(entryMatches("wolfram.com", "www.wolfram.com")).toBe(true);
    expect(entryMatches("python.org", "docs.python.org")).toBe(false);
    expect(entryMatches("docs.python.org", "python.org")).toBe(false);
    expect(entryMatches("python.org", "*.python.org")).toBe(true);
    expect(entryMatches("a.b.python.org", "*.python.org")).toBe(true);
    expect(entryMatches("evilpython.org", "*.python.org")).toBe(false);
    expect(entryMatches("python.org.evil.net", "*.python.org")).toBe(false);
    expect(hostAllowed("peps.python.org", ["docs.python.org", "*.python.org"])).toBe(true);
  });

  it("shows *.python.org as python.org (all pages) and opens python.org", () => {
    expect(siteLabel("*.python.org")).toBe("python.org (all pages)");
    expect(siteLabel("docs.python.org")).toBe("docs.python.org");
    expect(siteUrl("*.python.org")).toBe("https://python.org/");
  });
});

describe("websites pane with a wildcard entry", () => {
  it("lists it as the whole site and opens its home page", () => {
    render(<WebsitesPaneContent sites={["*.python.org", "en.cppreference.com"]} />);
    expect(screen.queryByText("*.python.org")).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: "python.org (all pages)" }));
    expect(document.querySelector("iframe")!.getAttribute("src")).toBe("https://python.org/");
    expect(screen.getByTestId("website-host").textContent).toBe("python.org");
  });
});

describe("teacher: allowed websites input", () => {
  function Settings() {
    const [v, setV] = useState<ExamToolsValue>({ calculator: "off", openBook: false, websites: [] });
    return (
      <>
        <ExamToolsSettings value={v} onChange={setV} />
        <output data-testid="v">{JSON.stringify(v.websites)}</output>
      </>
    );
  }

  it("explains exact vs *. and takes both", async () => {
    render(<Settings />);
    fireEvent.click(screen.getByRole("checkbox", { name: /Allowed websites/ }));
    expect(screen.getByText(/docs\.python\.org — exactly this site; \*\.python\.org — all of python\.org\./)).toBeInTheDocument();
    const site = screen.getByLabelText("Website to allow");
    await userEvent.type(site, "*.github.io{Enter}");
    expect(screen.getByText(/Anyone can make a site under github.io/)).toBeInTheDocument();
    await userEvent.clear(site);
    await userEvent.type(site, "*.Python.org{Enter}");
    await userEvent.type(site, "docs.python.org{Enter}");
    expect(JSON.parse(screen.getByTestId("v").textContent!)).toEqual(["*.python.org", "docs.python.org"]);
    expect(screen.getByRole("button", { name: "Remove *.python.org" })).toBeInTheDocument();
  });
});
