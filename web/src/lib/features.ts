// 2026-10-13: optional parts of FairPlay (Scan & Grade, LMS sync, Practice) are off unless
// the server lists them in /api/health "features" (FAIRPLAY_FEATURES on the server).
// Insights is hidden in the UI for now (its student-profile API stays on).
import { useEffect, useState } from "react";

export type Feature = "scan" | "lms" | "practice" | "insights" | "legacy_student_ui";

type Health = { features?: unknown; student_grades?: unknown; code_runner?: unknown } | null;
let health: Promise<Health> | null = null;

export function loadHealth(): Promise<Health> {
  if (!health) {
    health = fetch("/api/health", { headers: { Accept: "application/json" } })
      .then((r) => (r.ok ? r.json() : null))
      .catch(() => null);
  }
  return health;
}

export function loadFeatures(): Promise<Set<Feature>> {
  return loadHealth().then((j) => {
    const list = Array.isArray(j?.features) ? (j?.features as unknown[]) : [];
    return new Set(list.filter((f): f is Feature => typeof f === "string") as Feature[]);
  });
}

/** Students see grades and past exams unless the server says it switched them off. */
export function loadStudentGrades(): Promise<boolean> {
  return loadHealth().then((j) => j?.student_grades !== false);
}

/** Test helper. */
export function _resetFeaturesForTests(): void {
  health = null;
}

/** The earlier student screens (class joining, course cards, grades list) show only when the server lists this. */
export function useLegacyStudentUi(): boolean {
  return useFeatures().has("legacy_student_ui");
}

export function useStudentGradesOn(): boolean {
  const [on, setOn] = useState(true);
  useEffect(() => {
    let alive = true;
    void loadStudentGrades().then((v) => alive && setOn(v));
    return () => {
      alive = false;
    };
  }, []);
  return on;
}

/** The switched-on optional features (empty until the server answers, or on error). */
export function useFeatures(): Set<Feature> {
  const [features, setFeatures] = useState<Set<Feature>>(() => new Set());
  useEffect(() => {
    let alive = true;
    void loadFeatures().then((f) => alive && setFeatures(f));
    return () => {
      alive = false;
    };
  }, []);
  return features;
}
