// Audit 2026-10-12 S-03: a student joins a class with the code the teacher gives out.
// Exams of that class then appear on the home page.
// 2026-10-24: only the "Join another class" box; the course cards (Courses.tsx) list the classes.
import { useEffect, useState, type FormEvent } from "react";
import { ApiError, NetworkError, studentClassesApi } from "../../api/client";
import { takeJoinCode } from "../../pages/student/JoinLinkPage";

export function JoinClass({ onJoined, hasClasses = true }: { onJoined?: () => void; hasClasses?: boolean }) {
  const [code, setCode] = useState("");
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState<{ ok: boolean; text: string } | null>(null);

  // 2026-10-13: arrived through a class link -> join straight away.
  useEffect(() => {
    const pending = takeJoinCode();
    if (pending) void doJoin(pending);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  async function join(e: FormEvent) {
    e.preventDefault();
    if (!code.trim()) return;
    await doJoin(code);
  }

  async function doJoin(value: string) {
    setBusy(true);
    setMessage(null);
    try {
      const k = await studentClassesApi.join(value);
      const who = k.teacher ? ` (${k.teacher})` : "";
      setMessage({ ok: true, text: `You joined ${k.name}${who}. Its exams will show up here.` });
      setCode("");
      onJoined?.();
    } catch (err) {
      setMessage({
        ok: false,
        text: err instanceof ApiError ? err.detail : err instanceof NetworkError ? "No connection. Try again." : "Could not join.",
      });
    } finally {
      setBusy(false);
    }
  }

  return (
    <section className="stu-card stu-join-card" aria-labelledby="join-h" data-testid="join-class">
      <h2 id="join-h">{hasClasses ? "Join another class" : "Join a class"}</h2>
      <p className="stu-muted stu-small" style={{ margin: 0 }}>
        Your DKU OJ courses appear here by themselves. Use a class code only if your teacher gave you one.
      </p>
      <form onSubmit={join} className="stu-join-form">
        <label htmlFor="join-code" className="sr-only">
          Class code
        </label>
        <input
          id="join-code"
          className="stu-answer-input"
          placeholder="Class code, e.g. K7QM-3XPA"
          value={code}
          maxLength={20}
          autoComplete="off"
          autoCapitalize="characters"
          spellCheck={false}
          onChange={(e) => setCode(e.target.value.toUpperCase())}
        />
        <button type="submit" className="stu-btn primary" disabled={busy || code.trim().length < 8}>
          {busy ? "Joining…" : "Join"}
        </button>
      </form>
      {message && (
        <p role={message.ok ? "status" : "alert"} className={message.ok ? "stu-hint stu-join-ok" : "stu-field-error"} style={{ margin: 0 }}>
          {message.text}
        </p>
      )}
    </section>
  );
}
