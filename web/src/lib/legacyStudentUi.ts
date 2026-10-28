import { useEffect, useState } from "react";

/** True when the server lists legacy_student_ui in /api/health features (FAIRPLAY_LEGACY_STUDENT_UI=1). */
export function useLegacyStudentUi(): boolean {
  const [on, setOn] = useState(false);
  useEffect(() => {
    let alive = true;
    fetch("/api/health", { headers: { Accept: "application/json" } })
      .then((r) => (r.ok ? r.json() : null))
      .then((j: { features?: unknown } | null) => {
        if (alive && Array.isArray(j?.features) && j.features.includes("legacy_student_ui")) setOn(true);
      })
      .catch(() => undefined);
    return () => {
      alive = false;
    };
  }, []);
  return on;
}
