// 2026-10-23: open an exam to everyone or to selected students, and change the selection
// while it is live. The server enforces it (start, offline package); this is the teacher UI.
import { useEffect, useMemo, useState } from "react";
import { teacherApi } from "../../api/client";
import type { Exam, ExamStudent, OpenTo } from "../../api/types";
import { errorText, formatWhen, plural } from "./util";

/** "Closes automatically at …" (external exams: "… (set in DKU OJ)"), or null. */
export function closesAtLabel(exam: Pick<Exam, "closes_at" | "external">, closesAt = exam.closes_at): string | null {
  if (!closesAt) return null;
  const from = exam.external ? ` (set in ${exam.external.name})` : "";
  return `Closes automatically at ${formatWhen(closesAt)}${from}`;
}

/** The exam's students, loaded once per exam. */
export function useExamStudents(examId: string, enabled: boolean) {
  const [students, setStudents] = useState<ExamStudent[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  useEffect(() => {
    if (!enabled || students !== null) return;
    let alive = true;
    teacherApi.examStudents(examId).then(
      (s) => alive && setStudents(s),
      (e) => alive && setError(errorText(e)),
    );
    return () => {
      alive = false;
    };
  }, [examId, enabled, students]);
  return { students, error };
}

/** Searchable checklist of students. */
export function StudentChecklist({
  students,
  selected,
  onChange,
  idPrefix = "ot",
}: {
  students: ExamStudent[];
  selected: number[];
  onChange: (ids: number[]) => void;
  idPrefix?: string;
}) {
  const [query, setQuery] = useState("");
  const chosen = useMemo(() => new Set(selected), [selected]);
  const q = query.trim().toLowerCase();
  const shown = q
    ? students.filter((s) => s.name.toLowerCase().includes(q) || s.username.toLowerCase().includes(q))
    : students;
  const toggle = (id: number) => onChange(chosen.has(id) ? selected.filter((x) => x !== id) : [...selected, id]);
  const addShown = () => onChange([...new Set([...selected, ...shown.map((s) => s.id)])]);
  const clear = () => onChange([]);

  if (students.length === 0) return <div className="t-empty">No students in this exam's class yet.</div>;
  return (
    <div data-testid="student-checklist">
      <input
        className="input pr-search"
        type="search"
        aria-label="Search students"
        placeholder="Search by name or username"
        value={query}
        onChange={(e) => setQuery(e.target.value)}
      />
      <div className="t-small t-muted" style={{ display: "flex", gap: 10, alignItems: "center", marginBottom: 6 }}>
        <span aria-live="polite">{plural(selected.length, "student")} selected</span>
        <button type="button" className="t-mini-link" onClick={addShown} disabled={shown.length === 0}>
          {q ? "select these" : "select all"}
        </button>
        <button type="button" className="t-mini-link" onClick={clear} disabled={selected.length === 0}>
          clear
        </button>
      </div>
      <ul className="pr-student-list" aria-label="Students">
        {shown.map((s) => {
          const id = `${idPrefix}-stu-${s.id}`;
          const on = chosen.has(s.id);
          return (
            <li key={s.id} className={`pr-student${on ? " on" : ""}`}>
              <input id={id} type="checkbox" checked={on} onChange={() => toggle(s.id)} />
              <label htmlFor={id}>
                <span className="pr-student-name">{s.name}</span>
                <span className="t-small t-muted">
                  {s.username}
                  {s.started ? " · started" : ""}
                </span>
              </label>
            </li>
          );
        })}
        {shown.length === 0 && <li className="pr-student t-muted">No student matches “{query}”.</li>}
      </ul>
    </div>
  );
}

/** "Everyone" or "Selected students" (+ the checklist). */
export function OpenToChoice({
  examId,
  value,
  onChange,
  idPrefix = "ot",
}: {
  examId: string;
  value: OpenTo;
  onChange: (v: OpenTo) => void;
  idPrefix?: string;
}) {
  const selectedMode = value !== "all";
  const { students, error } = useExamStudents(examId, selectedMode);
  const choices: { all: boolean; title: string; hint: string }[] = [
    { all: true, title: "Everyone", hint: "Every student of the exam can start with the code." },
    {
      all: false,
      title: "Selected students",
      hint: "Only the students you tick can start. The exam is hidden from the others.",
    },
  ];
  return (
    <fieldset style={{ border: 0, padding: 0, margin: 0, minWidth: 0 }}>
      <legend className="t-small" style={{ fontWeight: 600 }}>
        Who can start
      </legend>
      <div className="ext-choices">
        {choices.map((c) => {
          const on = c.all !== selectedMode;
          return (
            <label key={c.title} className={`ext-choice${on ? " selected" : ""}`}>
              <input
                type="radio"
                name={`${idPrefix}-mode`}
                checked={on}
                onChange={() => onChange(c.all ? "all" : [])}
              />
              <span>
                <span className="ext-choice-title">{c.title}</span>
                <span className="ext-choice-hint">{c.hint}</span>
              </span>
            </label>
          );
        })}
      </div>
      {selectedMode && (
        <div style={{ marginTop: 10 }}>
          {error && <span className="t-inline-error">{error}</span>}
          {students === null && !error ? (
            <div className="t-empty">Loading students…</div>
          ) : students ? (
            <StudentChecklist students={students} selected={value as number[]} onChange={onChange} idPrefix={idPrefix} />
          ) : null}
        </div>
      )}
    </fieldset>
  );
}

/** Live room: who may start now, and change it. Removing someone who started never stops them. */
export function OpenToEditor({
  exam,
  openTo,
  selected,
  onChanged,
}: {
  exam: Exam;
  openTo: OpenTo;
  selected: ExamStudent[] | null | undefined;
  onChanged: (e: Exam) => void;
}) {
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState<OpenTo>(openTo);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function save() {
    setBusy(true);
    setError(null);
    try {
      let change: { open_to: OpenTo } | { add: number[]; remove: number[] };
      if (draft === "all" || openTo === "all") change = { open_to: draft };
      else {
        const before = new Set(openTo);
        const after = new Set(draft);
        change = { add: draft.filter((x) => !before.has(x)), remove: openTo.filter((x) => !after.has(x)) };
      }
      onChanged(await teacherApi.updateOpenTo(exam.id, change));
      setEditing(false);
    } catch (e) {
      setError(errorText(e));
    } finally {
      setBusy(false);
    }
  }

  const names = (selected ?? []).map((s) => s.name);
  return (
    <section className="t-card" aria-labelledby="opento-h" data-testid="open-to-editor">
      <div className="t-card-head">
        <h2 id="opento-h">Who can start</h2>
        {!editing && (
          <button
            type="button"
            className="t-mini-link"
            onClick={() => {
              setDraft(openTo);
              setEditing(true);
            }}
          >
            change
          </button>
        )}
      </div>
      {!editing ? (
        <span className="t-small" data-testid="open-to-summary">
          {openTo === "all"
            ? "Everyone in the exam can start with the code."
            : openTo.length === 0
              ? "Nobody is selected yet: nobody new can start."
              : `Only ${plural(openTo.length, "selected student")}: ${names.join(", ")}`}
        </span>
      ) : (
        <>
          <OpenToChoice examId={exam.id} value={draft} onChange={setDraft} idPrefix="ot-live" />
          <span className="t-small t-muted">Students who already started keep writing, even if you remove them.</span>
          {error && <span className="t-inline-error">{error}</span>}
          <div style={{ display: "flex", gap: 8 }}>
            <button type="button" className="btn btn-primary" onClick={() => void save()} disabled={busy}>
              {busy ? "Saving…" : "Save"}
            </button>
            <button type="button" className="btn btn-secondary" onClick={() => setEditing(false)} disabled={busy}>
              Cancel
            </button>
          </div>
        </>
      )}
    </section>
  );
}
