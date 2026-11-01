import { useState } from "react";
import { StartCodeForm } from "./StartCodeForm";

/** After the invigilator let the student out: "Resume exam" always asks for the start code. */
export function ResumeExam({
  examId,
  className = "stu-btn primary lg",
  label = "Resume exam",
  onChanged,
}: {
  examId: string;
  className?: string;
  label?: string;
  onChanged?: () => void;
}) {
  const [open, setOpen] = useState(false);
  if (open)
    return (
      <div data-testid="resume-code">
        <p className="stu-muted" style={{ margin: "0 0 8px" }}>
          Enter the start code from your invigilator
        </p>
        <StartCodeForm examId={examId} autoFocus onCancel={() => setOpen(false)} onConflict={onChanged} />
      </div>
    );
  return (
    <button type="button" className={className} onClick={() => setOpen(true)}>
      {label}
    </button>
  );
}
