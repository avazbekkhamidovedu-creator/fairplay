// 2026-10-23 (exam tools): the open-book files this student brought, read only. Opened in
// the same viewer the student had in the exam (no download there either).
import { useEffect, useState } from "react";
import { materialsApi } from "../../api/client";
import type { MaterialFile } from "../../api/types";
import { MaterialViewer } from "../student/MaterialViewer";
import { formatBytes } from "../../lib/materials";
import { errorText } from "./util";
import "../../styles/tools.css";

export function SubmissionMaterials({ examId, attemptId }: { examId: string; attemptId: string }) {
  const [files, setFiles] = useState<MaterialFile[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [open, setOpen] = useState<MaterialFile | null>(null);

  useEffect(() => {
    let alive = true;
    setFiles(null);
    setOpen(null);
    materialsApi.submissionFiles(examId, attemptId).then(
      (r) => alive && setFiles(r.files),
      (e) => alive && setError(errorText(e)),
    );
    return () => {
      alive = false;
    };
  }, [examId, attemptId]);

  return (
    <section className="t-card" aria-labelledby="mat-h" style={{ gap: 12 }}>
      <h2 id="mat-h">Materials</h2>
      {error && (
        <p className="alert alert-error" role="alert" style={{ margin: 0 }}>
          {error}
        </p>
      )}
      {files && files.length === 0 && (
        <p className="t-muted" style={{ margin: 0 }}>
          No files uploaded for this open-book exam.
        </p>
      )}
      {files && files.length > 0 && (
        <ul className="t-materials" aria-label="Uploaded materials">
          {files.map((f) => (
            <li key={f.id}>
              <button type="button" className={`t-material${open?.id === f.id ? " on" : ""}`} onClick={() => setOpen(open?.id === f.id ? null : f)} aria-expanded={open?.id === f.id}>
                <span className="t-material-kind">{f.kind.toUpperCase()}</span>
                <span className="t-material-name">{f.name}</span>
                <span className="t-small t-muted">{formatBytes(f.size)}</span>
              </button>
            </li>
          ))}
        </ul>
      )}
      {open && (
        <div className="t-material-view">
          <MaterialViewer file={open} load={() => materialsApi.teacherFile(examId, open.id)} />
        </div>
      )}
    </section>
  );
}
