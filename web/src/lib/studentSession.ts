import { setScopedEndHandler, setStudentExpiredHandler } from "../api/client";
import { clearExamDataOnSignOut } from "./offline";
import { keepDeviceCheck } from "./pendingDeviceCheck";

/** A scoped sign-in (homework or operator preview) ended: everything this page kept for the student leaves this computer (the
 *  saved device check, the exam's copies and packages, the cached name). The token itself is
 *  removed by the caller. */
export function wipeStudentState(): void {
  keepDeviceCheck(null);
  clearExamDataOnSignOut();
  try {
    for (const k of Object.keys(localStorage)) if (k.startsWith("fairplay:me:student")) localStorage.removeItem(k);
    sessionStorage.clear();
  } catch {
    /* storage unavailable */
  }
}

setScopedEndHandler(wipeStudentState);
setStudentExpiredHandler(() => clearExamDataOnSignOut(true));
