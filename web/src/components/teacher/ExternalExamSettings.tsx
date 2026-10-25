// Slice 3: "Where do students answer?" — questions in FairPlay, or an exam held on another
// website (Canvas quiz, Google Form, Moodle…) that the Mac app opens inside the lockdown.
// Self-contained: the parent keeps the value and sends externalExamInput(value) on save.
import { useEffect, useId, useState } from "react";
import type { CanvasQuizType, Delivery, ExamMode } from "../../api/types";
import {
  splitDomainText,
  describeEntry,
  effectiveAllowedSites,
  broadDomainWarnings,
  externalUrlWarning,
  CANVAS_QUIZ_TYPE_LABEL,
  defaultFinishRegex,
  detectCanvasQuizType,
  effectiveCodeSource,
  canvasCodeWarning,
  CANVAS_SIGN_IN_HOSTS,
  FINISH_REGEX_RULE,
  normalizeDomains,
  isAutoCanvasHost,
  effectiveQuizType,
  urlHost,
  type ExternalSettingsErrors,
  type ExternalSettingsValue,
} from "./externalValidation";
import "../../styles/external.css";
import "../../styles/slice6.css";

export interface ExternalExamSettingsProps {
  value: ExternalSettingsValue;
  onChange: (next: ExternalSettingsValue) => void;
  /** Errors to show (from validateExternalSettings or the server). */
  errors?: ExternalSettingsErrors;
  disabled?: boolean;
  /**
   * Slice 6: when given, three delivery cards replace the two mode cards. Picking "website"
   * sets mode "external"; "computer" / "paper" set mode "questions".
   */
  delivery?: Delivery;
  /** 2026-10-13: paper needs Scan & Grade, which is off unless the server switches it on. */
  allowPaper?: boolean;
  onDeliveryChange?: (next: Delivery) => void;
  /**
   * Canvas mode: whether the teacher has a Canvas account linked. null = not known yet.
   * Needed for the automatic access code.
   */
  canvasLinked?: boolean | null;
}

const DELIVERY_CHOICES: { delivery: Delivery; title: string; hint: string }[] = [
  {
    delivery: "computer",
    title: "On computers (FairPlay app, locked)",
    hint: "Students answer your questions in the locked FairPlay app. Automatic questions are graded for you.",
  },
  {
    delivery: "paper",
    title: "On paper (print, scan, AI-assisted grading)",
    hint: "Print one booklet per student, scan the answers, and check AI-suggested grades before they count.",
  },
  {
    delivery: "website",
    title: "On another website",
    hint: "Students answer on Canvas, Google Forms, Moodle or another site, opened inside the locked FairPlay app.",
  },
];

const CHOICES: { mode: ExamMode; title: string; hint: string }[] = [
  {
    mode: "questions",
    title: "Questions in FairPlay",
    hint: "Write the questions here. FairPlay grades the automatic ones for you.",
  },
  {
    mode: "external",
    title: "On another website",
    hint: "Students answer on Canvas, Google Forms, Moodle or another site, opened inside the locked FairPlay app.",
  },
];

export function ExternalExamSettings({
  value,
  onChange,
  errors = {},
  disabled,
  delivery,
  onDeliveryChange,
  allowPaper = true,
  canvasLinked = null,
}: ExternalExamSettingsProps) {
  const uid = useId();
  const name = `exam-mode-${uid}`;
  // The textarea keeps its own text so typing a newline or a half-written name is not
  // rewritten under the cursor; the parent gets the list of non-empty lines.
  const [domainText, setDomainText] = useState(() => value.allowed_domains.join("\n"));
  useEffect(() => {
    const mine = splitDomainText(domainText);
    const same =
      mine.length === value.allowed_domains.length && mine.every((d, i) => d === value.allowed_domains[i]);
    if (!same) setDomainText(value.allowed_domains.join("\n"));
    // only react to changes coming from the parent
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [value.allowed_domains]);

  const [showCode, setShowCode] = useState(false);
  const set = (patch: Partial<ExternalSettingsValue>) => onChange({ ...value, ...patch });
  const external = value.mode === "external";
  // Audit W-09: the same list the app enforces (the exam address may open only its folder).
  const preview = effectiveAllowedSites(value.external_url, value.allowed_domains);
  const warnings = external ? broadDomainWarnings(value.allowed_domains) : [];
  const urlWarning = external ? externalUrlWarning(value.external_url) : null;
  const canvas = external && value.external_kind === "canvas";
  const quizType = effectiveQuizType(value);
  const detected = detectCanvasQuizType(value.external_url);
  const codeSource = effectiveCodeSource(value);
  const canvasHost = urlHost(value.external_url);
  const removedHosts = value.canvas_removed_hosts ?? [];
  const signInHosts = CANVAS_SIGN_IN_HOSTS.filter((h) => !removedHosts.includes(h));
  const codeWarning = canvasCodeWarning(value.canvas_access_code);
  const removeTeacherHost = (host: string) => {
    const next = normalizeDomains(value.allowed_domains).filter((d) => d !== host);
    setDomainText(next.join("\n"));
    set({ allowed_domains: next });
  };

  return (
    <fieldset className="ext-settings" disabled={disabled}>
      <legend>Where do students answer?</legend>
      {delivery !== undefined && onDeliveryChange ? (
        <div className={`ext-choices${allowPaper ? " three" : ""}`}>
          {DELIVERY_CHOICES.filter((c) => allowPaper || c.delivery !== "paper" || delivery === "paper").map((c) => {
            const current: Delivery = value.mode === "external" ? "website" : delivery === "paper" ? "paper" : "computer";
            const selected = current === c.delivery;
            return (
              <label key={c.delivery} className={`ext-choice${selected ? " selected" : ""}`}>
                <input
                  type="radio"
                  name={name}
                  value={c.delivery}
                  checked={selected}
                  onChange={() => {
                    const mode: ExamMode = c.delivery === "website" ? "external" : "questions";
                    if (mode !== value.mode) set({ mode });
                    onDeliveryChange(c.delivery);
                  }}
                />
                <span>
                  <span className="ext-choice-title">{c.title}</span>
                  <span className="ext-choice-hint">{c.hint}</span>
                </span>
              </label>
            );
          })}
        </div>
      ) : (
      <div className="ext-choices">
        {CHOICES.map((c) => {
          const selected = value.mode === c.mode;
          return (
            <label key={c.mode} className={`ext-choice${selected ? " selected" : ""}`}>
              <input
                type="radio"
                name={name}
                value={c.mode}
                checked={selected}
                onChange={() => set({ mode: c.mode })}
              />
              <span>
                <span className="ext-choice-title">{c.title}</span>
                <span className="ext-choice-hint">{c.hint}</span>
              </span>
            </label>
          );
        })}
      </div>
      )}

      {external && (
        <div className="ext-fields">
          <div className="ext-field" role="radiogroup" aria-labelledby="exam-site-kind">
            <span id="exam-site-kind" className="ext-label">
              Which website?
            </span>
            <div className="ext-choices">
              <label className={`ext-choice${!canvas ? " selected" : ""}`}>
                <input
                  type="radio"
                  name={`${name}-site`}
                  checked={!canvas}
                  onChange={() => set({ external_kind: null })}
                />
                <span>
                  <span className="ext-choice-title">Any website</span>
                  <span className="ext-choice-hint">Google Forms, Moodle or another site you choose.</span>
                </span>
              </label>
              <label className={`ext-choice${canvas ? " selected" : ""}`}>
                <input
                  type="radio"
                  name={`${name}-site`}
                  checked={canvas}
                  onChange={() => set({ external_kind: "canvas", canvas_code_source: value.canvas_code_source ?? "managed" })}
                />
                <span>
                  <span className="ext-choice-title">Canvas quiz</span>
                  <span className="ext-choice-hint">
                    The quiz opens only inside FairPlay. FairPlay types the Canvas access code, so students cannot
                    start it outside the lock.
                  </span>
                </span>
              </label>
            </div>
          </div>

          <div className="ext-field">
            <label htmlFor="exam-external-url">{canvas ? "Canvas quiz link" : "Exam website address"}</label>
            <input
              id="exam-external-url"
              className="ext-input"
              type="url"
              inputMode="url"
              autoComplete="off"
              spellCheck={false}
              placeholder="https://canvas.dku.edu/courses/123/quizzes/456"
              value={value.external_url ?? ""}
              aria-invalid={errors.external_url ? true : undefined}
              aria-describedby="exam-external-url-hint"
              onChange={(e) => set({ external_url: e.target.value })}
            />
            <p id="exam-external-url-hint" className="ext-hint">
              {canvas
                ? "Copy the link of the quiz or assignment from your browser while you view it in Canvas. It must start with https://. Students open it from the FairPlay app after entering the start code."
                : "The page students should see first, for example the quiz or form link. Students open it from the FairPlay app after entering the start code."}
            </p>
            {errors.external_url && (
              <p className="ext-error" role="alert">
                {errors.external_url}
              </p>
            )}
            {!errors.external_url && urlWarning && <p className="ext-hint ext-warn">{urlWarning}</p>}
          </div>

          {canvas && (
            <div className="ext-field" data-testid="canvas-settings">
              <label htmlFor="exam-canvas-type">Quiz type</label>
              <select
                id="exam-canvas-type"
                className="ext-input"
                value={quizType}
                aria-describedby="exam-canvas-type-hint"
                onChange={(e) => set({ canvas_quiz_type: e.target.value as CanvasQuizType })}
              >
                {(Object.keys(CANVAS_QUIZ_TYPE_LABEL) as CanvasQuizType[]).map((t) => (
                  <option key={t} value={t}>
                    {CANVAS_QUIZ_TYPE_LABEL[t]}
                  </option>
                ))}
              </select>
              <p id="exam-canvas-type-hint" className="ext-hint" data-testid="canvas-type-hint">
                {detected
                  ? `Detected from the link: ${CANVAS_QUIZ_TYPE_LABEL[detected]}. Change it only if this is wrong.`
                  : "FairPlay could not tell the type from the link, so it assumes a Classic Quiz. Choose the right one."}
              </p>
              {quizType === "new" && (
                <p className="ext-hint ext-warn" role="note" data-testid="canvas-new-warning">
                  New Quizzes cannot be set up automatically. FairPlay cannot set or change their access code, so
                  you type the code yourself and it stays as you set it in Canvas.
                </p>
              )}

              <fieldset className="ext-subset">
              <legend className="ext-label">How the access code is set</legend>
              <div className="ext-choices">
                <label className={`ext-choice${codeSource === "managed" ? " selected" : ""}`}>
                  <input
                    type="radio"
                    name={`${name}-code`}
                    checked={codeSource === "managed"}
                    disabled={quizType === "new"}
                    onChange={() => set({ canvas_code_source: "managed" })}
                  />
                  <span>
                    <span className="ext-choice-title">FairPlay sets and changes it for me</span>
                    <span className="ext-choice-hint">
                      FairPlay puts a random code on the quiz in Canvas when you open the exam and removes it when
                      the exam closes. You never see it.
                    </span>
                  </span>
                </label>
                <label className={`ext-choice${codeSource === "manual" ? " selected" : ""}`}>
                  <input
                    type="radio"
                    name={`${name}-code`}
                    checked={codeSource === "manual"}
                    onChange={() => set({ canvas_code_source: "manual" })}
                  />
                  <span>
                    <span className="ext-choice-title">I will type the code I set in Canvas</span>
                    <span className="ext-choice-hint">Set an access code on the quiz in Canvas, then type the same code here.</span>
                  </span>
                </label>
              </div>
              </fieldset>

              {codeSource === "managed" && canvasLinked !== true && (
                <p className="ext-hint ext-warn" role="note" data-testid="canvas-link-warning">
                  {canvasLinked === false
                    ? "No Canvas account is linked yet. Link your Canvas account first (under Canvas in the menu), or the exam cannot open."
                    : "Link your Canvas account first. FairPlay needs it to set the code."}
                </p>
              )}

              {codeSource === "manual" && (
                <>
                  <label htmlFor="exam-canvas-code-input">Canvas access code</label>
                  <div className="ext-code-row">
                  <input
                    id="exam-canvas-code-input"
                    className="ext-input"
                    type={showCode ? "text" : "password"}
                    autoComplete="off"
                    autoCapitalize="off"
                    autoCorrect="off"
                    spellCheck={false}
                    value={value.canvas_access_code ?? ""}
                    placeholder={value.canvas_code_stored ? "A code is saved. Type a new one to replace it." : ""}
                    aria-invalid={errors.canvas_access_code ? true : undefined}
                    aria-describedby="exam-canvas-code-hint"
                    onChange={(e) => set({ canvas_access_code: e.target.value })}
                  />
                  <button
                    type="button"
                    className="ext-code-toggle"
                    aria-pressed={showCode}
                    aria-label={showCode ? "Hide access code" : "Show access code"}
                    onClick={() => setShowCode((x) => !x)}
                  >
                    {showCode ? "Hide" : "Show"}
                  </button>
                  </div>
                  {codeWarning && (
                    <p className="ext-hint ext-warn" role="note" data-testid="canvas-code-space-warning">
                      {codeWarning}
                    </p>
                  )}
                  {errors.canvas_access_code && (
                    <p className="ext-error" role="alert">
                      {errors.canvas_access_code}
                    </p>
                  )}
                </>
              )}
              <p id="exam-canvas-code-hint" className="ext-hint">
                The code is never shown again after you save, to you or to students. If you lose a typed code, set
                a new one in Canvas and enter it here.
              </p>
            </div>
          )}

          <div className="ext-field">
            <label htmlFor="exam-allowed-domains">
              Other websites students may open <span className="ext-optional">(one per line)</span>
            </label>
            <textarea
              id="exam-allowed-domains"
              className="ext-input"
              rows={4}
              spellCheck={false}
              autoCapitalize="off"
              placeholder={"login.microsoftonline.com\ndku.edu"}
              value={domainText}
              aria-invalid={errors.allowed_domains ? true : undefined}
              aria-describedby="exam-allowed-domains-hint"
              onChange={(e) => {
                setDomainText(e.target.value);
                set({ allowed_domains: splitDomainText(e.target.value) });
              }}
            />
            <p id="exam-allowed-domains-hint" className="ext-hint">
              Add every website the exam needs, including the sign-in page (e.g. login.microsoftonline.com). Each
              line opens exactly that site: dku.edu does not open canvas.dku.edu — write *.dku.edu for all
              sub-sites. Add a path to open only part of a site (docs.google.com/forms/d/e/…/). Everything else
              is blocked and shown to you in the live room.
            </p>
            {errors.allowed_domains && (
              <p className="ext-error" role="alert">
                {errors.allowed_domains}
              </p>
            )}
            {warnings.length > 0 && (
              <ul className="ext-hint ext-warn" aria-label="Check these websites">
                {warnings.map((w) => (
                  <li key={w}>{w}</li>
                ))}
              </ul>
            )}
            {canvas && (
              <>
                <p className="ext-hint" id="exam-allowed-preview" data-testid="canvas-auto-sites">
                  Students can open these sites. FairPlay adds the Canvas site{quizType === "new" ? ", the New Quizzes sites" : ""} and
                  the sign-in sites when you save. Remove a sign-in site you do not use.
                </p>
                <ul className="ext-chips" aria-labelledby="exam-allowed-preview" data-testid="canvas-chips">
                  {canvasHost && (
                    <li title="The Canvas site is always allowed" data-locked="true">
                      {canvasHost} <span className="ext-chip-lock">always allowed</span>
                    </li>
                  )}
                  {signInHosts.map((h) => (
                    <li key={h} title={describeEntry(h)}>
                      {h}
                      <button
                        type="button"
                        className="ext-chip-x"
                        aria-label={`Remove ${h}`}
                        onClick={() => set({ canvas_removed_hosts: [...removedHosts, h] })}
                      >
                        ×
                      </button>
                    </li>
                  ))}
                  {normalizeDomains(value.allowed_domains)
                    .filter((d) => !isAutoCanvasHost(d, value.external_url))
                    .map((d) => (
                      <li key={d} title={describeEntry(d)}>
                        {d}
                        <button type="button" className="ext-chip-x" aria-label={`Remove ${d}`} onClick={() => removeTeacherHost(d)}>
                          ×
                        </button>
                      </li>
                    ))}
                </ul>
                {removedHosts.length > 0 && (
                  <p className="ext-hint">
                    Removed:{" "}
                    {removedHosts.map((h) => (
                      <button
                        key={h}
                        type="button"
                        className="ext-link-btn"
                        aria-label={`Add ${h} back`}
                        onClick={() => set({ canvas_removed_hosts: removedHosts.filter((x) => x !== h) })}
                      >
                        {h} (add back)
                      </button>
                    ))}
                  </p>
                )}
              </>
            )}
            {!canvas && preview.length > 0 && (
              <>
                <p className="ext-hint" id="exam-allowed-preview">
                  Students can open:
                </p>
                <ul className="ext-chips" aria-labelledby="exam-allowed-preview">
                  {preview.map((d) => (
                    <li key={d} title={describeEntry(d)}>
                      {d}
                    </li>
                  ))}
                </ul>
              </>
            )}
          </div>

          {canvas && (
            <div className="ext-field" data-testid="canvas-finish">
              <span className="ext-label">When does the exam finish?</span>
              <p className="ext-hint">
                FairPlay hands the exam in by itself when Canvas shows its “submitted” page. This quiz type uses the
                pattern <code>{defaultFinishRegex(quizType)}</code>. Only this finish pattern is used; the page address
                is not checked in any other way. Students can also press Finish exam.
              </p>
              <details>
                <summary>Advanced: use another finish pattern</summary>
                <label htmlFor="exam-finish-regex">Finish pattern (regular expression)</label>
                <input
                  id="exam-finish-regex"
                  className="ext-input"
                  type="text"
                  autoComplete="off"
                  spellCheck={false}
                  placeholder={defaultFinishRegex(quizType)}
                  value={value.finish_url_regex ?? ""}
                  aria-invalid={errors.finish_url_regex ? true : undefined}
                  aria-describedby="exam-finish-regex-rule"
                  onChange={(e) => set({ finish_url_regex: e.target.value })}
                />
                <p className="ext-hint" id="exam-finish-regex-rule">
                  {FINISH_REGEX_RULE}
                </p>
                {errors.finish_url_regex && (
                  <p className="ext-error" role="alert">
                    {errors.finish_url_regex}
                  </p>
                )}
              </details>
            </div>
          )}

          {!canvas && (
          <div className="ext-field">
            <label htmlFor="exam-finish-contains">
              Finish automatically when the page address contains <span className="ext-optional">(optional)</span>
            </label>
            <input
              id="exam-finish-contains"
              className="ext-input"
              type="text"
              autoComplete="off"
              spellCheck={false}
              placeholder="formResponse"
              value={value.finish_url_contains ?? ""}
              aria-invalid={errors.finish_url_contains ? true : undefined}
              aria-describedby="exam-finish-contains-hint"
              onChange={(e) => set({ finish_url_contains: e.target.value })}
            />
            <p id="exam-finish-contains-hint" className="ext-hint">
              When the website shows its “submitted” page, FairPlay hands the exam in and unlocks the computer.
              For Google Forms use formResponse. Leave empty if students press “Finish exam” themselves.
            </p>
            {errors.finish_url_contains && (
              <p className="ext-error" role="alert">
                {errors.finish_url_contains}
              </p>
            )}
          </div>
          )}
        </div>
      )}
    </fieldset>
  );
}

export default ExternalExamSettings;
