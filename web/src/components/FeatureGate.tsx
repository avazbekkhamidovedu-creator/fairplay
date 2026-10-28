// 2026-10-13: renders an optional page only when the server switched the feature on.
import { useEffect, useState, type ReactNode } from "react";
import { Navigate } from "react-router-dom";
import { loadFeatures, loadStudentGrades, type Feature } from "../lib/features";
import { Loading } from "./Loading";

export function FeatureGate({ feature, fallback, children }: { feature: Feature; fallback: string; children: ReactNode }) {
  const [on, setOn] = useState<boolean | null>(null);
  useEffect(() => {
    let alive = true;
    void loadFeatures().then((f) => alive && setOn(f.has(feature)));
    return () => {
      alive = false;
    };
  }, [feature]);
  if (on === null) return <Loading label="Loading…" />;
  return on ? <>{children}</> : <Navigate to={fallback} replace />;
}

/** Renders the grades pages only unless the server switched student grades off. */
export function GradesGate({ children }: { children: ReactNode }) {
  const [on, setOn] = useState<boolean | null>(null);
  useEffect(() => {
    let alive = true;
    void loadStudentGrades().then((v) => alive && setOn(v));
    return () => {
      alive = false;
    };
  }, []);
  if (on === null) return <Loading label="Loading…" />;
  return on ? <>{children}</> : <Navigate to="/student" replace />;
}

/** The earlier class and course screens render only when the server lists legacy_student_ui. */
export function LegacyGate({ children }: { children: ReactNode }) {
  return (
    <FeatureGate feature="legacy_student_ui" fallback="/student">
      {children}
    </FeatureGate>
  );
}
