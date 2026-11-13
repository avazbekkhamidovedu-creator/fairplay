import { useState } from "react";
import { HomeworkLink, SignedOutNotice } from "../../components/code-signin/HomeworkLink";
import { DeviceCheckDialog, type DeviceCheckState } from "../../components/code-signin/DeviceCheckDialog";
import { TryExamScreenDialog } from "../../components/code-signin/TryExamScreenDialog";
import { UpdateCard, useSignInUpdate } from "../../components/code-signin/UpdatePrompt";
import { AppVersion } from "../../components/AppVersion";
import { LogoMark, Wordmark } from "../../components/ui/Icon";
import * as bridge from "../../lib/bridge";
import { keepDeviceCheck } from "../../lib/pendingDeviceCheck";
import "../../styles/code-signin.css";

/** The app's home screen before anyone is signed in: sign in with the class account, Homework,
 *  the update card, and a device test that works with nobody signed in. There is no exam code
 *  to type here. */
export default function NativeHome({ onUseAccount }: { onUseAccount: () => void }) {
  const [check, setCheck] = useState<DeviceCheckState>({ kind: "idle" });
  const [panel, setPanel] = useState(false);
  const [tryOpen, setTry] = useState(false);
  const update = useSignInUpdate();
  const installing = update?.phase.kind === "installing";

  async function runCheck() {
    setCheck({ kind: "running" });
    const report = await bridge.preflightReport();
    keepDeviceCheck(report);
    setCheck(report ? { kind: "done", report } : { kind: "none" });
  }

  function openPanel() {
    if (bridge.getNativeState()?.deviceTest === true) {
      setTry(true);
      return;
    }
    setPanel(true);
    if (check.kind === "idle" || check.kind === "none") void runCheck();
  }

  return (
    <div className="cs-page">
      <header className="cs-top">
        <span className="cs-brand-col">
          <span className="cs-brand">
            <LogoMark />
            <Wordmark />
          </span>
          <AppVersion short className="cs-version" />
        </span>
        <button type="button" className="cs-test-btn" onClick={openPanel}>
          <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
            <rect x="4.5" y="5" width="15" height="10.5" rx="1.5" />
            <path d="M2.5 18.5h19" />
          </svg>
          Test my device
        </button>
      </header>
      <main className="cs-main">
        <section className="cs-card" aria-labelledby="cs-title">
          {update && !update.hidden && <UpdateCard update={update} countdown />}
          <h1 id="cs-title" className="cs-title">
            Sign in
          </h1>
          <p className="cs-sub">Use the username and password from your class.</p>
          <SignedOutNotice />
          <button type="button" className="cs-btn cs-btn-primary cs-block" onClick={onUseAccount} disabled={installing}>
            Sign in with username and password
          </button>
          <HomeworkLink />
        </section>
      </main>
      {tryOpen && <TryExamScreenDialog onClose={() => setTry(false)} />}
      {panel && <DeviceCheckDialog state={check} onAgain={() => void runCheck()} onClose={() => setPanel(false)} update={update} />}
    </div>
  );
}
