import { studentApi } from "../api/client";
import type { PreflightReport } from "./bridge";

// The device check a student ran before signing in. It stays in memory only and is sent to
// the teacher (POST /api/student/device-check) once there is a token.
let pending: PreflightReport | null = null;

export function keepDeviceCheck(report: PreflightReport | null): void {
  pending = report;
}

export function hasPendingDeviceCheck(): boolean {
  return pending !== null;
}

/** Sends the kept check; it is dropped once the server has it, kept when the send fails. */
export async function sendPendingDeviceCheck(): Promise<boolean> {
  const report = pending;
  if (!report) return false;
  try {
    await studentApi.deviceCheck({
      platform: report.platform,
      app_version: report.app_version,
      os_version: report.os_version,
      checks: report.checks,
      exam_id: null,
    });
    if (pending === report) pending = null;
    return true;
  } catch {
    return false;
  }
}
