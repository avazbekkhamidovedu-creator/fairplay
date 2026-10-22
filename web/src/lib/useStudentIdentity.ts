import { useEffect, useState } from "react";
import { auth, currentStudentId } from "../api/client";

/**
 * 2026-10-23: the student's full name and NetID (username) for the exam footer.
 * Cached on this device so the footer still shows them when the exam runs offline.
 */
export interface StudentIdentity {
  name: string;
  netId: string;
}

export const IDENTITY_KEY = "fairplay:me:student";
/** Per signed-in student, so one laptop shared by two students never shows the wrong name. */
const keyFor = () => `${IDENTITY_KEY}:${currentStudentId() ?? "?"}`;

function readCached(): StudentIdentity | null {
  try {
    const v = JSON.parse(localStorage.getItem(keyFor()) ?? "null") as Partial<StudentIdentity> | null;
    return v && typeof v.name === "string" && typeof v.netId === "string" ? { name: v.name, netId: v.netId } : null;
  } catch {
    return null;
  }
}

export function useStudentIdentity(): StudentIdentity | null {
  const [me, setMe] = useState<StudentIdentity | null>(readCached);
  useEffect(() => {
    let alive = true;
    auth.me("student").then(
      (m) => {
        if (!alive || m.role !== "student") return;
        const v = { name: m.name, netId: m.username };
        setMe(v);
        try {
          localStorage.setItem(keyFor(), JSON.stringify(v));
        } catch {
          /* ignore */
        }
      },
      () => undefined,
    );
    return () => {
      alive = false;
    };
  }, []);
  return me;
}
