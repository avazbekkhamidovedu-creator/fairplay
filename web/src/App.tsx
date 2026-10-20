import { useEffect } from "react";
import { Navigate, Route, Routes, useNavigate } from "react-router-dom";
import { setUnauthorizedHandler } from "./api/client";
import { hello } from "./lib/bridge";
import { UpdateRequired, useUpgradeRequired } from "./components/UpdateRequired";
import TeacherRoutes from "./pages/teacher/routes";
import StudentRoutes from "./pages/student/routes";

export default function App() {
  const navigate = useNavigate();
  useEffect(() => {
    setUnauthorizedHandler((role) => navigate(`/${role}/login`, { replace: true }));
  }, [navigate]);
  useEffect(() => {
    // Native bridge: announce ourselves; the Mac app answers with its state.
    hello();
  }, []);
  const upgradeTo = useUpgradeRequired();
  if (upgradeTo !== null) return <UpdateRequired minVersion={upgradeTo} />;

  return (
    <Routes>
      <Route path="/" element={<Navigate to="/teacher" replace />} />
      <Route path="/teacher/*" element={<TeacherRoutes />} />
      <Route path="/student/*" element={<StudentRoutes />} />
      <Route path="*" element={<Navigate to="/teacher" replace />} />
    </Routes>
  );
}
