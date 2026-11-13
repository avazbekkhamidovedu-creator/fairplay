// Student routes (owned by the student UI). Mounted at /student/* in App.tsx.
import "../../api/student";
import { Suspense, lazy } from "react";
import { Navigate, Route, Routes } from "react-router-dom";
import { RequireAuth } from "../../components/RequireAuth";
import { FeatureGate, GradesGate, LegacyGate } from "../../components/FeatureGate";
import JoinLinkPage from "./JoinLinkPage";
import StudentLogin, { StudentAppHome } from "./StudentLogin";
import StudentHome from "./StudentHome";
import ExamsPage from "./ExamsPage";
import CoursePage from "./CoursePage";
import CheckInPage from "./CheckInPage";
import BeforeStartPage from "./BeforeStartPage";
import UploadFilesPage from "./UploadFilesPage";
import ExamTakingPage from "./ExamTakingPage";
import DonePage from "./DonePage";
import GradesPage from "./GradesPage";
import FeedbackPage from "./FeedbackPage";
import StudentPracticePage from "./PracticePage";
import ExternalExamPage from "./ExternalExamPage";
import ExternalDonePage from "./ExternalDonePage";
import HomeworkPage from "./HomeworkPage";
import AdminTestPage from "./AdminTestPage";

const DeviceTestPage = lazy(() => import("./DeviceTestPage"));

export default function StudentRoutes() {
  return (
    <Routes>
      <Route path="login" element={<StudentLogin />} />
      <Route path="home" element={<StudentAppHome />} />
      <Route index element={<RequireAuth role="student"><StudentHome /></RequireAuth>} />
      {/* 2026-10-24: a course's own page (course cards on the home page) */}
      <Route path="courses/:classId" element={<RequireAuth role="student"><LegacyGate><CoursePage /></LegacyGate></RequireAuth>} />
      <Route path="exams" element={<RequireAuth role="student"><ExamsPage /></RequireAuth>} />
      <Route path="exams/:examId/start" element={<RequireAuth role="student"><BeforeStartPage /></RequireAuth>} />
      {/* 2026-10-24: open-book files before the exam opens (the early upload window) */}
      <Route path="exams/:examId/upload" element={<RequireAuth role="student"><UploadFilesPage /></RequireAuth>} />
      <Route path="exams/:examId/checkin" element={<RequireAuth role="student"><CheckInPage /></RequireAuth>} />
      <Route path="exam/:attemptId" element={<RequireAuth role="student"><ExamTakingPage /></RequireAuth>} />
      <Route path="external/:attemptId" element={<RequireAuth role="student"><ExternalExamPage /></RequireAuth>} />
      <Route path="external-done/:attemptId" element={<RequireAuth role="student"><ExternalDonePage /></RequireAuth>} />
      <Route path="done/:attemptId" element={<RequireAuth role="student"><DonePage /></RequireAuth>} />
      <Route path="grades" element={<RequireAuth role="student"><GradesGate><GradesPage /></GradesGate></RequireAuth>} />
      <Route path="grades/:examId" element={<RequireAuth role="student"><GradesGate><FeedbackPage /></GradesGate></RequireAuth>} />
      <Route path="practice" element={<RequireAuth role="student"><FeatureGate feature="practice" fallback="/student"><StudentPracticePage /></FeatureGate></RequireAuth>} />
      <Route path="practice/:setId" element={<RequireAuth role="student"><FeatureGate feature="practice" fallback="/student"><StudentPracticePage /></FeatureGate></RequireAuth>} />
      {/* 2026-10-13: the class link a teacher shares (works signed in or not). */}
      <Route path="join/:code" element={<LegacyGate><JoinLinkPage /></LegacyGate>} />
      <Route path="homework" element={<HomeworkPage />} />
      {/* operator preview sign-in: no link points here */}
      <Route path="admin-test" element={<AdminTestPage />} />
      <Route path="device-test" element={<Suspense fallback={null}><DeviceTestPage /></Suspense>} />
      <Route path="*" element={<Navigate to="/student" replace />} />
    </Routes>
  );
}
