import LoginPage, { type LoginCopy } from "../LoginPage";

const COPY: LoginCopy = {
  title: "Student sign in",
  sub: "Sign in to write your exam and see your grades.",
  hint: "alice / alice123",
  pitch: "Your answers are saved as you type.",
  points: ["Answers sync even if the Wi-Fi drops", "Your teacher reads the start code aloud", "See feedback once grades are released"],
  note: "First time? Use the class login your professor gave you.",
};

export default function StudentLogin() {
  return <LoginPage role="student" copy={COPY} />;
}
