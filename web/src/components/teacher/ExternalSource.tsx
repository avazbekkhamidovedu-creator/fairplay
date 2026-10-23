// Exams that come from another system (integrations): read-only in FairPlay
// ("Edit this exam in <name>") and/or graded there ("Graded in <name>"). The name always
// comes from the exam's `external` block.
import { Link } from "react-router-dom";
import type { Exam, ExternalDelivery, ExternalInfo, ResultAttempt, Results } from "../../api/types";
import { formatWhen, plural } from "./util";
import "../../styles/questions.css";

/** Used when an older answer has no name. */
export const FALLBACK_SOURCE_NAME = "the other system";

export const editIn = (name: string) => `Edit this exam in ${name}`;
export const gradedIn = (name: string) => `Graded in ${name}`;

export function externalOf(exam: Pick<Exam, "external"> | null | undefined): ExternalInfo | null {
  return exam?.external ?? null;
}

/** The exam comes from another system. */
export function isExternalExam(exam: Pick<Exam, "external"> | null | undefined): boolean {
  return !!exam?.external;
}

export function sourceName(ext: Pick<ExternalInfo, "name"> | null | undefined): string {
  return ext?.name?.trim() || FALLBACK_SOURCE_NAME;
}

/** Only http(s) links are shown. */
function safeUrl(url: string | null | undefined): string | null {
  return url && /^https?:\/\//i.test(url) ? url : null;
}

function SourceLink({ ext }: { ext: ExternalInfo }) {
  const href = safeUrl(ext.url);
  if (!href) return null;
  return (
    <a href={href} target="_blank" rel="noopener noreferrer" className="btn btn-secondary">
      Open in {sourceName(ext)}
    </a>
  );
}

/** "Edit this exam in <name>" (kind edit) or "Graded in <name>" (kind graded). */
export function ExternalSourceBanner({ external, kind }: { external: ExternalInfo; kind: "edit" | "graded" }) {
  const name = sourceName(external);
  return (
    <div className="alert alert-info fp-external-note" role="note" data-testid={`external-${kind}-banner`}>
      {kind === "edit" ? (
        <span>
          <strong>{editIn(name)}.</strong> Its questions, points and settings come from {name} and are read-only here.
          Changes made there reach FairPlay before the exam starts.
        </span>
      ) : (
        <span>
          <strong>{gradedIn(name)}.</strong> Answers are sent to {name} after each hand-in. Grade and release the results
          there; students see them in FairPlay once released.
        </span>
      )}
      <SourceLink ext={external} />
    </div>
  );
}

/** Results tab of an exam graded elsewhere: who handed in, delivery and released scores. */
export function ExternalResults({ exam, results }: { exam: Exam; results: Results }) {
  const attempts = results.attempts;
  const ext = results.external ?? exam.external;
  if (!ext) return null;
  const name = sourceName(ext);
  return (
    <>
      <ExternalSourceBanner external={ext} kind="graded" />
      <section className="t-card" aria-labelledby="extr-h" style={{ gap: 12 }}>
        <h2 id="extr-h">
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
                <th scope="col">Sent to {name}</th>
                <th scope="col">Score in {name}</th>
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
                  <td>
                    <DeliveryCell d={a.external_delivery} />
                  </td>
                  <td>{externalScore(a)}</td>
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

/** "82 / 100" once released by the external source, else "Not released". */
export function externalScore(a: Pick<ResultAttempt, "external_result" | "total_points">): string {
  const r = a.external_result;
  if (!r?.released) return "Not released";
  return `${r.total ?? 0} / ${r.total_points ?? a.total_points}`;
}

const DELIVERY_LABEL: Record<ExternalDelivery["status"], string> = {
  pending: "Waiting to send",
  sent: "Sent",
  failed: "Retrying",
  dead: "Not sent",
};

/** Delivery of a student's answers to the external source (pending / sent / failed). */
export function DeliveryCell({ d }: { d: ExternalDelivery | null | undefined }) {
  if (!d) return <span className="t-muted">—</span>;
  const tone = d.status === "sent" ? "green" : d.status === "dead" ? "red" : d.status === "failed" ? "orange" : "";
  const extra = d.status === "failed" ? ` (${d.tries} ${d.tries === 1 ? "try" : "tries"})` : "";
  return (
    <span data-testid="external-delivery" title={d.last_error ?? undefined}>
      <span className={`t-pill${tone ? ` ${tone}` : ""}`}>{DELIVERY_LABEL[d.status] ?? d.status}</span>
      {extra}
      {d.status === "dead" && <span className="t-small t-muted"> · ask the FairPlay admin</span>}
    </span>
  );
}
