import { useRef, useState, type KeyboardEvent } from "react";
import { NotLockedBanner } from "../../components/LockBanner";
import { UpcomingExamCard, UploadWindowCard } from "../../components/student/ExamCard";
import { PastExamRow } from "../../components/student/ResultRow";
import { StudentLayout, StuLoading } from "../../components/student/StudentLayout";
import { pastExams, upcomingExams, uploadWindowExams, useStudentExams } from "./useStudentExams";

/** 2026-10-24: past results beyond this many sit behind "Show all". */
export const PAST_SHOWN = 20;

type Tab = "upcoming" | "past";

export default function ExamsPage() {
  const { exams, error } = useStudentExams();
  const [tab, setTab] = useState<Tab>("upcoming");
  const [allPast, setAllPast] = useState(false);
  const tabRefs = useRef<Record<Tab, HTMLButtonElement | null>>({ upcoming: null, past: null });

  const upcoming = exams ? upcomingExams(exams) : [];
  const uploads = exams ? uploadWindowExams(exams) : [];
  const past = exams ? pastExams(exams) : [];
  const tabs: { id: Tab; label: string }[] = [
    { id: "upcoming", label: `Upcoming · ${upcoming.length + uploads.length}` },
    { id: "past", label: `Past · ${past.length}` },
  ];

  const onTabKey = (e: KeyboardEvent) => {
    if (e.key !== "ArrowLeft" && e.key !== "ArrowRight") return;
    e.preventDefault();
    const next: Tab = tab === "upcoming" ? "past" : "upcoming";
    setTab(next);
    tabRefs.current[next]?.focus();
  };

  return (
    <>
      <NotLockedBanner />
      <StudentLayout>
        <main className="stu-main" style={{ gap: 24 }}>
          <h1 className="stu-h1">Exams</h1>

          <div role="tablist" aria-label="Exam lists" className="stu-tabs" onKeyDown={onTabKey}>
            {tabs.map((t) => (
              <button
                key={t.id}
                ref={(el) => {
                  tabRefs.current[t.id] = el;
                }}
                type="button"
                role="tab"
                id={`tab-${t.id}`}
                aria-selected={tab === t.id}
                aria-controls={`panel-${t.id}`}
                tabIndex={tab === t.id ? 0 : -1}
                className="stu-tab"
                onClick={() => setTab(t.id)}
              >
                {t.label}
              </button>
            ))}
          </div>

          {error && (
            <div className="stu-alert warn" role="alert">
              {error}
            </div>
          )}

          {exams === null ? (
            <StuLoading label="Loading exams…" />
          ) : tab === "upcoming" ? (
            <section id="panel-upcoming" role="tabpanel" aria-labelledby="tab-upcoming" style={{ display: "flex", flexDirection: "column", gap: 14 }}>
              {upcoming.length === 0 && uploads.length === 0 ? (
                <div className="stu-card">
                  <h2>No open exams</h2>
                  <p className="stu-muted">When your teacher opens an exam, it shows up here.</p>
                </div>
              ) : (
                <>
                  {upcoming.map((e) => (
                    <UpcomingExamCard key={e.id} exam={e} />
                  ))}
                  {/* 2026-10-24: not open yet, but files can be uploaded now */}
                  {uploads.map((e) => (
                    <UploadWindowCard key={e.id} exam={e} headingLevel={2} />
                  ))}
                </>
              )}
            </section>
          ) : (
            <section id="panel-past" role="tabpanel" aria-labelledby="tab-past" className="stu-card" style={{ gap: 6 }}>
              <h2 style={{ marginBottom: 6 }}>Past results</h2>
              {past.length === 0 ? (
                <p className="stu-muted">Exams you submit show up here.</p>
              ) : (
                <>
                  <div className="stu-rows">
                    {(allPast ? past : past.slice(0, PAST_SHOWN)).map((e) => (
                      <PastExamRow key={e.id} exam={e} />
                    ))}
                  </div>
                  {past.length > PAST_SHOWN && (
                    <button type="button" className="stu-btn quiet stu-show-all" aria-expanded={allPast} onClick={() => setAllPast((v) => !v)}>
                      {allPast ? "Show fewer" : `Show all ${past.length} results`}
                    </button>
                  )}
                </>
              )}
            </section>
          )}
        </main>
      </StudentLayout>
    </>
  );
}
