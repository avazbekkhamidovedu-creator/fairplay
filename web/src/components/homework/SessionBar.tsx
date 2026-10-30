import { useEffect, useState } from "react";
import { MSG_CAP_WARNING, MSG_IDLE_WARNING, startHomeworkMonitor, type Warning } from "../../lib/homeworkSession";

export function SessionBar({ onExpire }: { onExpire: () => void }) {
  const [warning, setWarning] = useState<Warning>(null);
  useEffect(() => startHomeworkMonitor(setWarning, onExpire), [onExpire]);
  if (!warning) return null;
  return (
    <div className="cs-session-bar" role="status" data-testid="session-warning">
      {warning === "cap" ? MSG_CAP_WARNING : MSG_IDLE_WARNING}
    </div>
  );
}
