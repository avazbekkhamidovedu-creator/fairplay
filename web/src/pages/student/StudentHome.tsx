import { useState } from "react";
import { Link, useLocation, useNavigate } from "react-router-dom";
import type { StudentExamSummary } from "../../api/types";
import { NotLockedBanner } from "../../components/LockBanner";
import { firstName, examName, plural, shortTime, isToday, shortDate } from "../../components/student/format";
import { CourseGrid, useStudentClasses } from "../../components/student/Courses";
import { UpcomingExamCard } from "../../components/student/ExamCard";
import { ActiveList, PastList, TestsSection, scheduledExams, useExamTab, useStudentGrades } from "../../components/student/ExamTabs";
import { JoinClass } from "../../components/student/JoinClass";
import { StartCodeForm } from "../../components/student/StartCodeForm";
import { StudentLayout, StuLoading } from "../../components/student/StudentLayout";
import { TrialCard } from "../../components/student/TrialCard";
import { useMe } from "../../lib/useMe";
import { NOT_OPEN_FOR_YOU, pastExams, upcomingExams, useStudentExams } from "./useStudentExams";

/** 2026-10-24: open exams listed on the home page; the rest are on the Exams page. */
export const HOME_OPEN_SHOWN = 6;
/** 2026-10-24: results on the home page's Past tab; the rest are on the Grades page. */
export const HOME_PAST_SHOWN = 5;

export default function StudentHome() {
  const me = useMe("student");
  const { exams, error, reload } = useStudentExams();
  const [activeId, setActiveId] = useState<string | null>(null);
  // 2026-10-22: back from the sample exam (DonePage sends this once the computer unlocked).
  const trialDone = (useLocation().state as { trialDone?: string } | null)?.trialDone;
  const [trialNote, setTrialNote] = useState(!!trialDone);
  // 2026-10-24: course cards (each opens its course page) above "Your tests [Active | Past]"
  const courses = useStudentClasses();
  const grades = useStudentGrades();
  const [tab, setTab] = useExamTab();

  const upcoming = exams ? upcomingExams(exams) : [];
  const past = exams ? pastExams(exams) : [];
  const scheduled = scheduledExams(courses.classes ?? []);
  const name = firstName(me?.name);

  const summary =
    exams === null
      ? ""
      : upcoming.length === 0
        ? "No exam is open right now."
        : upcoming.length === 1
          ? upcoming[0]?.attempt?.status === "in_progress"
            ? "You have an exam in progress."
            : "One exam is open now."
          : `${upcoming.length} exams are open now.`;

  const renderLive = (live: StudentExamSummary[]) =>
    live.length <= 2 ? (
      <div className="stu-hero-col">
        {live.map((exam) => (
          <ExamHero
            key={exam.id}
            exam={exam}
            active={activeId === exam.id}
            onActivate={() => setActiveId(exam.id)}
            onCancel={() => setActiveId(null)}
            onChanged={reload}
          />
        ))}
      </div>
    ) : (
      // 2026-10-24: many open exams -> compact cards instead of big heroes
      <>
        <div className="stu-exam-list">
          {live.slice(0, HOME_OPEN_SHOWN).map((exam) => (
            <UpcomingExamCard key={exam.id} exam={exam} headingLevel={3} />
          ))}
        </div>
        {live.length > HOME_OPEN_SHOWN && (
          <Link to="/student/exams" className="stu-link">
            See all {live.length} open exams →
          </Link>
        )}
      </>
    );

  return (
    <>
      <NotLockedBanner />
      <StudentLayout>
        <main className="stu-main">
          <div className="stu-home-top">
            <div style={{ display: "flex", flexDirection: "column", gap: 4 }}>
              <h1 className="stu-h1">{name ? `Hi ${name}` : "Hi"}</h1>
              <p className="stu-sub" aria-live="polite">{summary}</p>
            </div>
            <TrialCard />
          </div>

          {trialNote && (
            <div className="stu-alert success stu-row" role="status" data-testid="trial-note">
              <span style={{ flex: 1 }}>
                That&apos;s how an exam works. On exam day your teacher gives you a start code, and FairPlay locks this
                computer the same way until you submit.
              </span>
              <button type="button" className="stu-btn sm quiet" onClick={() => setTrialNote(false)}>
                OK
              </button>
            </div>
          )}

          {error && (
            <div className="stu-alert warn" role="alert">
              {error}
            </div>
          )}

          <section className="stu-courses" aria-labelledby="courses-h" data-testid="courses">
            <div className="stu-row">
              <h2 id="courses-h" className="stu-section-title">
                Your courses
              </h2>
              {courses.classes && courses.classes.length > 0 && (
                <span className="stu-pill">{courses.classes.length}</span>
              )}
            </div>
            {courses.error && (
              <div className="stu-alert warn" role="alert">
                {courses.error}
              </div>
            )}
            {courses.classes === null ? (
              <StuLoading label="Loading your courses…" />
            ) : courses.classes.length === 0 ? (
              <div className="stu-empty stu-courses-empty" data-testid="courses-empty">
                <h3 style={{ margin: 0, fontSize: 17 }}>No courses yet</h3>
                <p className="stu-muted" style={{ margin: 0 }}>
                  Your DKU OJ courses show up here once your professor adds you. Exams only show up for your courses.
                </p>
              </div>
            ) : (
              <CourseGrid classes={courses.classes} exams={exams} />
            )}
          </section>

          <TestsSection
            idPrefix="home-tests"
            tab={tab}
            onTab={setTab}
            activeCount={upcoming.length + scheduled.length}
            pastCount={past.length}
          >
            {exams === null ? (
              <StuLoading label="Loading your exams…" />
            ) : tab === "active" ? (
              <ActiveList
                live={upcoming}
                scheduled={scheduled}
                renderLive={renderLive}
                showCourse
                empty={
                  <>
                    <h3>No open or upcoming exams</h3>
                    <p className="stu-muted">
                      When your teacher schedules or opens an exam, it shows up here. This page checks every few seconds.
                    </p>
                  </>
                }
              />
            ) : (
              <PastList
                past={past}
                grades={grades}
                shown={HOME_PAST_SHOWN}
                expandable={false}
                empty={
                  <>
                    <h3>No results yet</h3>
                    <p className="stu-muted">Exams you hand in show up here, with your grade once your teacher releases it.</p>
                  </>
                }
                footer={
                  <Link to="/student/grades" className="stu-link stu-past-more">
                    {past.length > HOME_PAST_SHOWN ? `All ${past.length} results on Grades →` : "All grades →"}
                  </Link>
                }
              />
            )}
          </TestsSection>

          <JoinClass
            hasClasses={!!courses.classes && courses.classes.length > 0}
            onJoined={() => {
              void courses.reload();
              void reload();
            }}
          />
        </main>
      </StudentLayout>
    </>
  );
}

function ExamHero({
  exam,
  active,
  onActivate,
  onCancel,
  onChanged,
}: {
  exam: StudentExamSummary;
  active: boolean;
  onActivate: () => void;
  onCancel: () => void;
  onChanged: () => void;
}) {
  const navigate = useNavigate();
  const inProgress = exam.attempt?.status === "in_progress";
  const notForMe = !inProgress && exam.open_for_me === false;   // 2026-10-23: selected students only
  const headId = `hero-${exam.id}`;
  const when = inProgress
    ? "In progress"
    : notForMe
      ? NOT_OPEN_FOR_YOU
      : exam.scheduled_at
      ? `Open now · ${isToday(exam.scheduled_at) ? "today" : shortDate(exam.scheduled_at)} ${shortTime(exam.scheduled_at)}`
      : "Open now";
  const meta = [
    exam.room && `Room ${exam.room}`,
    `${exam.duration_min} minutes`,
    exam.question_count != null && plural(exam.question_count, "question"),
  ]
    .filter(Boolean)
    .join(" · ");

  return (
    <section className="stu-exam-hero" aria-labelledby={headId}>
      <span className="stu-eyebrow">{when}</span>
      <h3 id={headId}>{examName(exam.course, exam.title)}</h3>
      <span className="stu-exam-hero-meta">{meta}</span>

      {notForMe ? (
        <div className="stu-exam-hero-note" data-testid="not-open-for-you">
          Your teacher opened this exam for some students first. It shows a Start button here when it opens for you.
        </div>
      ) : inProgress ? (
        <>
          <div className="stu-exam-hero-note">Your answers are saved. Continue where you stopped — the timer kept running.</div>
          <div className="stu-row">
            <button
              type="button"
              className="stu-btn primary lg"
              onClick={() => navigate(`/student/exam/${encodeURIComponent(exam.attempt!.attempt_id)}`)}
            >
              Continue exam
            </button>
          </div>
        </>
      ) : active ? (
        <StartCodeForm examId={exam.id} autoFocus onCancel={onCancel} onConflict={onChanged} />
      ) : (
        <>
          <div className="stu-exam-hero-note">
            Read the rules, lock this computer, then type the start code your teacher reads out.
          </div>
          <div className="stu-row">
            <button
              type="button"
              className="stu-btn primary lg"
              onClick={() => {
                onActivate();
                navigate(`/student/exams/${encodeURIComponent(exam.id)}/start`);
              }}
            >
              Start exam
            </button>
            <Link to={`/student/exams/${exam.id}/checkin`} className="stu-btn on-dark lg">
              Check in
            </Link>
          </div>
        </>
      )}
    </section>
  );
}
