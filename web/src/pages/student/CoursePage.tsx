// 2026-10-24: one course's own page (/student/courses/:classId), opened from a course card on
// the home page: the course, its section, instructor(s) and source, then
// "Your tests [Active | Past]" for this course only.
import { Link, useParams } from "react-router-dom";
import { NotLockedBanner } from "../../components/LockBanner";
import { courseTitle, examInCourse, sourceLabel, useStudentClasses } from "../../components/student/Courses";
import {
  ActiveList,
  PastList,
  TestsSection,
  activeCount,
  moreScheduled,
  scheduledExams,
  useExamTab,
  useStudentGrades,
} from "../../components/student/ExamTabs";
import { ArrowLeftIcon } from "../../components/student/icons";
import { StudentLayout, StuLoading } from "../../components/student/StudentLayout";
import type { StudentClass } from "../../api/types";
import { pastExams, upcomingExams, uploadWindowExams, useStudentExams } from "./useStudentExams";

/** "Shuai Dong" -> Instructor; "A, B" / "A and B" / "A; B" -> Instructors. */
export function instructorLabel(teacher: string): string {
  return /[,;]|\sand\s|&/.test(teacher) ? "Instructors" : "Instructor";
}

/** The section line: the class name when it is not the course title itself. */
function sectionName(k: StudentClass): string | null {
  const title = courseTitle(k);
  return k.name && k.name.trim() && k.name !== title ? k.name : null;
}

export default function CoursePage() {
  const { classId = "" } = useParams();
  const { classes, error: classesError } = useStudentClasses();
  const { exams, error } = useStudentExams();
  const grades = useStudentGrades();
  const [tab, setTab] = useExamTab();
  const k = classes?.find((c) => String(c.id) === classId) ?? null;

  const mine = k && exams ? exams.filter((e) => examInCourse(e, k)) : [];
  const live = upcomingExams(mine);
  const uploads = uploadWindowExams(mine);
  const past = pastExams(mine);
  const scheduled = k ? scheduledExams([k]) : [];
  const more = k && exams ? moreScheduled(k, exams) : 0;

  return (
    <>
      <NotLockedBanner />
      <StudentLayout>
        <main className="stu-main stu-course-page">
          <Link to="/student" className="stu-back" data-testid="course-back">
            <ArrowLeftIcon size={16} />
            Home
          </Link>

          {(error || classesError) && (
            <div className="stu-alert warn" role="alert">
              {error || classesError}
            </div>
          )}

          {classes === null ? (
            <StuLoading label="Loading the course…" />
          ) : !k ? (
            <section className="stu-card" data-testid="course-missing">
              <h1 className="stu-h1">Course not found</h1>
              <p className="stu-muted" style={{ margin: 0 }}>
                You are not in this course (any more), or it was removed. Your courses are on the home page.
              </p>
              <Link to="/student" className="stu-btn primary" style={{ alignSelf: "flex-start" }}>
                Back to Home
              </Link>
            </section>
          ) : (
            <>
              <header className="stu-course-head" data-testid="course-head">
                <span className={`stu-source-badge ${k.source === "dkuoj" ? "dkuoj" : "fairplay"}`}>{sourceLabel(k)}</span>
                <h1 className="stu-h1 stu-clamp-2" title={courseTitle(k)}>
                  {courseTitle(k)}
                </h1>
                <dl className="stu-course-facts">
                  {sectionName(k) && (
                    <div>
                      <dt>Section</dt>
                      <dd className="stu-ellipsis" title={sectionName(k) ?? undefined}>
                        {sectionName(k)}
                      </dd>
                    </div>
                  )}
                  {k.teacher && (
                    <div>
                      <dt>{instructorLabel(k.teacher)}</dt>
                      <dd className="stu-ellipsis" title={k.teacher}>
                        {k.teacher}
                      </dd>
                    </div>
                  )}
                  {k.term && (
                    <div>
                      <dt>Term</dt>
                      <dd>{k.term}</dd>
                    </div>
                  )}
                </dl>
              </header>

              <TestsSection
                idPrefix="course-tests"
                tab={tab}
                onTab={setTab}
                activeCount={activeCount(live, uploads, scheduled)}
                pastCount={past.length}
              >
                {exams === null ? (
                  <StuLoading label="Loading exams…" />
                ) : tab === "active" ? (
                  <ActiveList
                    live={live}
                    uploads={uploads}
                    scheduled={scheduled}
                    more={more}
                    empty={
                      <>
                        <h3>No upcoming exams in this course</h3>
                        <p className="stu-muted">They appear here when your teacher schedules them.</p>
                      </>
                    }
                  />
                ) : (
                  <PastList
                    past={past}
                    grades={grades}
                    showCourse={false}
                    empty={
                      <>
                        <h3>No finished exams yet</h3>
                        <p className="stu-muted">Exams you hand in for this course show up here, with your grade once it is released.</p>
                      </>
                    }
                  />
                )}
              </TestsSection>
            </>
          )}
        </main>
      </StudentLayout>
    </>
  );
}
