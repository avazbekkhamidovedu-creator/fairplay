// DKU OJ bridge (docs/dkuoj-bridge.md §5, §6): exams built in DKU OJ are read-only in
// FairPlay ("Edit this exam in DKU OJ") and graded there ("Graded in DKU OJ").
import { Link } from "react-router-dom";
import type { Exam, Results } from "../../api/types";
import { formatWhen, plural } from "./util";
import "../../styles/oj.css";

export const EDIT_IN_OJ = "Edit this exam in DKU OJ";
export const GRADED_IN_OJ = "Graded in DKU OJ";

export function isOjExam(exam: Pick<Exam, "source"> | null | undefined): boolean {
  return exam?.source === "dkuoj";
}

/** Only http(s) links to DKU OJ are shown. */
function safeUrl(url: string | null | undefined): string | null {
  return url && /^https?:\/\//i.test(url) ? url : null;
}

function OjLink({ url, children }: { url: string | null | undefined; children: string }) {
  const href = safeUrl(url);
  if (!href) return null;
  return (
    <a href={href} target="_blank" rel="noopener noreferrer" className="btn btn-secondary">
      {children}
    </a>
  );
}

export function OjEditBanner({ exam }: { exam: Pick<Exam, "oj_url"> }) {
  return (
    <div className="alert alert-info fp-oj-note" role="note" data-testid="oj-edit-banner">
      <span>
        <strong>{EDIT_IN_OJ}.</strong> Its questions, points and settings come from DKU OJ and are read-only here.
        Changes made there reach FairPlay before the exam starts.
      </span>
      <OjLink url={exam.oj_url}>Open in DKU OJ</OjLink>
    </div>
  );
}

export function OjGradedBanner({ url }: { url: string | null | undefined }) {
  return (
    <div className="alert alert-info fp-oj-note" role="note" data-testid="oj-graded-banner">
      <span>
        <strong>{GRADED_IN_OJ}.</strong> Answers are sent to DKU OJ after each hand-in. Grade and release the results
        there; students see them in FairPlay once released.
      </span>
      <OjLink url={url}>Open in DKU OJ</OjLink>
    </div>
  );
}

/** Results tab of a DKU OJ exam: who handed in (scores live in DKU OJ). */
export function OjResults({ exam, results }: { exam: Exam; results: Results }) {
  const attempts = results.attempts;
  return (
    <>
      <OjGradedBanner url={results.oj_url ?? exam.oj_url} />
      <section className="t-card" aria-labelledby="ojr-h" style={{ gap: 12 }}>
        <h2 id="ojr-h">
          Hand-ins{" "}
          <span className="t-muted" style={{ fontWeight: 500 }}>
            · {plural(attempts.filter((a) => a.status === "submitted").length, "student")}
          </span>
        </h2>
        {attempts.length === 0 ? (
          <div className="t-empty">No student has started this exam yet.</div>
        ) : (
          <div className="t-table-scroll">
          <table className="t-table">
            <thead>
              <tr>
                <th scope="col">Student</th>
                <th scope="col">Status</th>
                <th scope="col">Handed in</th>
                <th scope="col">Receipt</th>
                <th scope="col">Flags</th>
                <th scope="col">
                  <span className="sr-only">Review</span>
                </th>
              </tr>
            </thead>
            <tbody>
              {attempts.map((a) => (
                <tr key={a.attempt_id}>
                  <td>
                    {a.name} <span className="t-muted t-small">{a.username}</span>
                  </td>
                  <td>
                    {a.status === "submitted" ? "Handed in" : "Writing"}
                    {a.late ? " · late" : ""}
                  </td>
                  <td>{a.submitted_at ? formatWhen(a.submitted_at) : "—"}</td>
                  <td className="mono">{a.receipt ?? "—"}</td>
                  <td>{a.flag_count ?? 0}</td>
                  <td>
                    <Link to={`/teacher/exams/${exam.id}/submissions/${a.attempt_id}`}>Review</Link>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
          </div>
        )}
      </section>
    </>
  );
}
