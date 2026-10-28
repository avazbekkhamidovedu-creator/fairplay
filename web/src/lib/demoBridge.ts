// Browser demo only: the server reports "browser_demo": true in /api/health when it runs with
// FAIRPLAY_BROWSER_GATE=off. This stands in for the FairPlay app's native bridge so the student
// screens can be tried in a plain browser. Nothing here runs against a server that does not
// say so, and it never locks anything.

const DEMO_VERSION = "0.7.4-demo";
const DEMO_DEVICE = "DEMO-BROWSER-0001";
// The public key of dev servers (FAIRPLAY_DEV=1); a server that does not know it refuses the proof.
const DEV_APP_KEY = "fairplay-dev-app-key-not-secret";

type Msg = { action: string; nonce?: string; attempt_id?: string };

let locked = false;
let prelock = false;

const CHECKS = [
  { id: "macos_version", ok: true, message: "macOS 15.1 (demo)" },
  { id: "displays", ok: true, message: "One display" },
  { id: "remote_login", ok: true, message: "Remote Login is off" },
  { id: "accessibility", ok: true, message: "Lock permission granted" },
  { id: "network", ok: true, message: "The exam server answers" },
  { id: "vpn", ok: false, message: "A VPN is on (demo)", info: true },
];

function reply(detail: Record<string, unknown>): void {
  setTimeout(() => window.dispatchEvent(new CustomEvent("proctora-native", { detail })), 0);
}

const state = () => ({
  type: "state",
  locked,
  prelock,
  trusted: true,
  version: DEMO_VERSION,
  app_version: DEMO_VERSION,
  platform: "mac",
});

async function sign(message: string): Promise<string> {
  const enc = new TextEncoder();
  const key = await crypto.subtle.importKey("raw", enc.encode(DEV_APP_KEY), { name: "HMAC", hash: "SHA-256" }, false, ["sign"]);
  const sig = new Uint8Array(await crypto.subtle.sign("HMAC", key, enc.encode(message)));
  return Array.from(sig, (b) => b.toString(16).padStart(2, "0")).join("");
}

async function onMessage(msg: Msg): Promise<void> {
  switch (msg.action) {
    case "hello":
      reply(state());
      break;
    case "attest": {
      const nonce = String(msg.nonce ?? "");
      try {
        reply({
          type: "attestation",
          nonce,
          device_id: DEMO_DEVICE,
          signature: await sign(`start|${nonce}|${DEMO_DEVICE}`),
          version: DEMO_VERSION,
          build: "",
        });
      } catch {
        reply({ type: "attestationFailed", nonce, reason: "no_crypto" });
      }
      break;
    }
    case "preflight":
      reply({
        type: "preflight",
        problems: [],
        checks: CHECKS,
        platform: "mac",
        app_version: DEMO_VERSION,
        os_version: "macOS 15.1 (demo)",
        apps_to_close: [],
      });
      break;
    case "prelock":
      locked = prelock = true;
      reply({ type: "locked", prelock: true });
      break;
    case "lock":
      locked = true;
      prelock = false;
      reply({ type: "locked" });
      break;
    case "preunlock":
    case "unlock":
      locked = prelock = false;
      reply({ type: "unlocked" });
      break;
    case "quitApps":
      reply({ type: "appsQuit", still_running: [] });
      break;
    case "seal":
      reply({ type: "sealed", attempt_id: String(msg.attempt_id ?? ""), ok: false });
      break;
    default:
      break;
  }
}

/** Puts the stand-in bridge on the page (once). False when a real bridge is already there. */
export function installDemoBridge(): boolean {
  const w = window as unknown as { webkit?: { messageHandlers?: { proctora?: unknown } } };
  if (w.webkit?.messageHandlers?.proctora || (window as unknown as { chrome?: { webview?: unknown } }).chrome?.webview) {
    return false;
  }
  w.webkit = { messageHandlers: { proctora: { postMessage: (m: Msg) => void onMessage(m) } } };
  return true;
}

/** True only when the server's health answer says so (any other value is "no"). */
export function isBrowserDemo(health: unknown): boolean {
  return !!health && typeof health === "object" && (health as { browser_demo?: unknown }).browser_demo === true;
}
