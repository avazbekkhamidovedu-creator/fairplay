import { useState } from "react";
import { Navigate, useNavigate, useSearchParams } from "react-router-dom";
import { getToken } from "../../api/client";
import { isNative } from "../../lib/bridge";
import LoginPage, { type LoginCopy } from "../LoginPage";
import NativeHome from "./NativeHome";

const COPY: LoginCopy = {
  title: "Student sign in",
  sub: "Sign in to write your exam and see your grades.",
  hint: "alice / alice123",
  pitch: "Your answers are saved as you type.",
  points: ["Answers sync even if the Wi-Fi drops", "Your teacher reads the start code aloud", "See feedback once grades are released"],
  note: "First time? Use the class login your professor gave you.",
};

/** The app's home (/student/home): the native home screen when nobody is signed in, the
 *  student's page when someone is. Never a code screen. */
export function StudentAppHome() {
  const navigate = useNavigate();
  if (!isNative() || getToken("student")) return <Navigate to={getToken("student") ? "/student" : "/student/login"} replace />;
  return <NativeHome onUseAccount={() => navigate("/student/login?account=1")} />;
}

export default function StudentLogin() {
  const [params] = useSearchParams();
  const [account, setAccount] = useState(params.get("account") === "1");
  if (isNative() && !account) return <NativeHome onUseAccount={() => setAccount(true)} />;
  return <LoginPage role="student" copy={COPY} />;
}
