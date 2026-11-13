# Real Device Testing Checklist

Test the FairPlay exam app on real hardware (Mac, Windows PC, iPad) with a local or staging FairPlay server. The homework sign-in and new exam features are not yet deployed to the public VM.

## Before You Start (Developer Setup)

The app under test must connect to a FairPlay server with these features enabled:
- Server settings:
  - `FAIRPLAY_HOMEWORK_LOGIN=dkuoj` (enables homework sign-in)
  - `DKUOJ_URL` (DKU OJ instance URL, e.g. http://10.200.31.64)
  - `DKUOJ_PUBLIC_URL` (public URL students see)
  - `HOMEWORK_WEBTEST_SITES` (allowed test websites)
- At least one exam with:
  - `box_link` set (for Duke Box tests)
  - A test student account (username and password) on the exam's roster
- (Confirm with the developer that all settings are in place.)

## Test Setup

Confirm:
- Server running and accessible (health check: `/api/health`)
- At least one live exam with FairPlay set as the proctor
- A student account on the exam's roster (there is no exam access code any more)
- Network connection works on the device

## Device Test Sample Exam

1. On the student dashboard, tap or click **"Try a sample exam"** (card on the home screen).
2. Start the sample exam: the app shows a preflight screen with device checks (no blocks should appear).
3. Lock the device (open the exam in the app, NOT the browser):
   - **Expected:** A white "Sample exam" screen appears; the device is locked (no home/app switcher).
   - **Timer:** Shows "Leaves automatically in 1:00" and counts down to 0.
   - The sample has 4 questions (MCQ, numeric, short, code) — answer none, some, or all.
4. Leave the exam before time-up: tap **"Leave"** button.
   - **Expected:** Immediate unlock (no grace period on the sample). Dashboard appears.
5. Retake: tap **"Try a sample exam"** again within 15 seconds and confirm it keeps the same session (same window, no re-entry approval).
6. Start once more and wait for the timer to reach 0:00.
   - **Expected:** Auto-lock opens the dashboard after 1 minute; the sample is gone (one attempt per student, replaced on a fresh start).

## Sign-in & Early Entry

1. Open the app. The home screen shows **"Sign in"**, **"Test my device"** and (if enabled) the homework link. There is no box to type an exam code.
2. Sign in with the student's username and password for an exam that is scheduled to start in the future (e.g., in 30 minutes).
   - **Expected:** The dashboard shows the exam as not open yet (it is not offered as the student's exam until it opens).
3. When the exam opens, return to the dashboard.
   - **Expected:** The exam card shows "Open now"; starting it locks the app and shows the check-in screen.

## Device Test Preview (1-Minute Live Exam)

1. From the app home screen (nobody signed in), look for **"Test my device"** (button at the top).
2. Tap **"Test my device"** — a pop-up appears: **"Try the exam"** with a "Start" button.
3. Tap **"Start"** to begin the device test.
   - **Expected:** White paper-style exam screen with sample questions; lock bar at the top shows "Device test", timer "Leaves automatically in 1:00".
   - No apps are closed; the device is in a lightweight preview lock (only a red "blocked-apps" screen if you try to switch apps, then ~2 seconds before showing the block).
   - Questions are MCQ, numeric, short (no code); 4 samples; answer any or all.
   - The lock icon in the lock bar (if visible) shows a lock symbol.
4. Try to open another app or access the home screen.
   - **Expected:** Within 2 seconds, a red "close these apps" screen blocks the screen. Dismiss it by returning to the exam.
5. Wait for the timer to reach 0:00 or tap **"Leave"**.
   - **Expected:** Exam ends, device unlocks, and you return to the app home screen (preview is not saved).

## Real Exam Lock & Unlock

1. Sign in, start the exam with the start code the invigilator reads out, and complete the check-in.
   - **Expected:** Lock bar shows the exam title, course (if set), lock symbol, save status, and a timer.
   - The app has closed windowed apps (Mac/iPad) or taken over the desktop (Windows, separate exam screen or taskbar hiding).
2. Answer questions and let the timer count down to 0:00, or manually submit by selecting "Hand in" / "Submit".
   - **Expected:** Auto-submit at time-up if you haven't submitted.
   - Device unlocks immediately after submission.
3. Tap **"Back to home"** to return to the student home page.

## Resume After App Crash or Restart

**Precondition:** An exam is in progress on the device.

1. Start an exam, answer a few questions, then force-quit the FairPlay app (kill the process, not a normal close).
   - **Mac:** Force Quit (Cmd-Opt-Esc) or Activity Monitor.
   - **Windows:** Task Manager (Alt+Ctrl+Delete) or Kill the process.
   - **iPad:** Swipe up from the bottom to force-close.
2. Relaunch the FairPlay app.
   - **Expected:** The check-in page shows **"Your exam is in progress"** with a **"Resume exam"** button.
3. Tap **"Resume exam"**.
   - **Expected:** The app opens the exam page directly; the session is resumed. If resume approval is required, a message appears: **(confirm with the developer)**.
4. Answer a new question and submit.
   - **Expected:** Submission succeeds and shows the congratulations screen.

## 'Move to Another Computer' Flow (Device Swap)

**Precondition:** Exam is in progress on Computer A; you have access to Computer B.

1. Start an exam on Computer A, answer some questions, then switch to Computer B.
2. On Computer B, sign in as the same student and open the same exam.
   - **Expected:** An error appears: **"This exam is already open on another computer. Ask the invigilator to move it to this one."** (or similar device-swap message).
   - The page shows an option to request the teacher to move the exam (message to the live room).
3. Go to the live room (as the professor on Computer C) and check the student list.
   - **Expected:** A badge appears next to the student: **"Wants to continue on another computer"** with an **"Approve swap"** button.
4. Tap **"Approve swap"**.
   - **Expected (Computer B):** The start screen reappears, ready to start the exam again. Tap "Start" to bind the exam to Computer B.
   - **Expected (Computer A):** All future requests (answers, heartbeats) from Computer A are rejected; the `GET /handin` call (internal) returns "moved".
5. After ~120 seconds, attempt to send answers from Computer A.
   - **Expected:** Rejected (session error).

## DKU OJ Homework Login Window

1. On the app home screen (below the Sign in button), look for: **"Homework? Log in with DKU OJ"** (or the configured label).
2. Tap the link.
   - **Expected:** A secure browser window opens (isolated profile, no exam lock active).
   - Window shows the DKU OJ login page (username and password).
3. Enter DKU OJ credentials (use the test account the developer gave you).
   - **Expected:** Sign-in succeeds; the app moves to the homework page (welcome screen with "Signed in as [Name]").
4. If the server is not reachable during sign-in:
   - **Expected (confirm with the developer):** Error message; no offline path (homework requires the server).

## Homework Page & Session Bar

1. After signing in to homework, you are on the homework dashboard (list of assignments, due dates, etc.).
   - **Expected:** A top bar shows: **"Welcome, [Name]"** with a sign-out button.
   - A "Remember this device" option may appear.
2. Leave the homework idle for > 30 minutes without opening assignments.
   - **Expected (after 28 minutes):** A warning banner appears: **"You will be signed out in 2 minutes. Tap anywhere to stay signed in."**
3. Ignore the warning and wait for expiration.
   - **Expected:** Session ends; you are signed out and returned to the homework sign-in screen.
4. Verify the 4-hour session cap.
   - **Expected:** After 4 hours from sign-in, a warning appears: **"Your 4-hour session ends soon. Save your work."** (warning 5 minutes before the cap), then session ends.

## Sign-Out on App Quit

1. Sign in to homework (from the app home screen).
2. While on the homework page, close the FairPlay app (normal close, not force-quit).
   - **Mac:** Cmd-Q or the menu.
   - **Windows:** Alt-F4 or the close button.
   - **iPad:** Swipe from the bottom to close.
3. Relaunch the app.
   - **Expected:** You are back at the app home screen, signed out of homework (session is cleared on app close).

## 'Remember This Device' with Biometric Auth

1. Sign in to homework and go to the homework page.
2. Look for a **"Remember this device"** card.
3. Tap **"Remember this device"**.
   - **Expected:** A prompt appears for biometric authentication (Touch ID on Mac, Face ID on iPad, Windows Hello on Windows).
   - Complete the biometric prompt.
   - The device is remembered for 14 days.
4. Close the app and relaunch it.
   - **Expected:** On the app home screen, a new button appears: **"Continue as [Name]"**.
5. Tap **"Continue as [Name]"**.
   - **Expected:** Biometric prompt appears (or no prompt if within the grace period — confirm with the developer).
   - You are signed in to homework without re-entering credentials.
6. Tap **"Forget this device"** to revoke the stored key.
   - **Expected:** The next app restart asks for credentials again.

### Windows Hello without PIN

**Windows only:** Test with a device that has Windows Hello (facial recognition or fingerprint) but NO PIN set.

1. Enable **"Remember this device"** while on the homework page.
   - **Expected:** A prompt appears for Windows Hello biometric. No PIN is requested.
   - Complete the biometric and succeed.
2. Close and relaunch the app; tap **"Continue as [Name]"**.
   - **Expected:** Windows Hello prompt appears, no PIN required.

## 'Try the Web View' Links

1. On the homework page, look for a section or button labeled **"Try the web view"** or **"Websites"**.
2. The list shows allowed websites (e.g., "Canvas", "Python Docs", "Duke Box").
3. Tap one of the website links (e.g., "Canvas").
   - **Expected:** A restricted non-locking browser window opens (no exam lock is active).
   - Only the listed website (and its subdomains) can load; others are blocked.
   - You can navigate the site freely (open, read, download) but you cannot upload or submit.
4. Close the window (back button or close).
   - **Expected:** You return to the homework page (the restricted window is separate from the locked exam).

## Open Exam with Allowed Websites

1. Start a real exam and lock the device.
2. On the exam page, look for a **"Websites"** tab or button (appears if the exam has allowed websites set).
3. Tap **"Websites"**.
   - **Expected:** A panel or tab opens with allowed website links (e.g., "Canvas", "Duke Box", "Course Notes").
   - Links are in a restricted iframe (same-origin policy for FairPlay server, allowed hosts only).
4. Tap a link (e.g., "Canvas").
   - **Expected:** The site loads inside the exam page; you can read and navigate but cannot upload files.
5. If the exam allows open-book materials (PDFs, images, text files), you'll also see a **"Materials"** tab with your uploaded files.
   - Open a PDF or image.
   - **Expected:** Displayed in a restricted viewer; no download or save options outside the exam.

## Duke Box Overlay in Locked Exam (Running Exam Only)

Duke Box is only available while an exam is in progress (status "in_progress").

1. Start a real exam (with a Duke Box folder configured on the exam) and lock the device.
2. On the exam page, look for a **"Duke Box"** button in the top bar or in the More menu.
3. Tap **"Duke Box"** — a restricted overlay window appears, separate from the exam.
   - **Expected:** Duke Box login appears; the overlay is constrained to Duke Box and Duo (if multi-factor auth is required).
   - You can browse folders, open files (PDFs, images, docs), but cannot download or upload.
4. Open a PDF or document inside Duke Box.
   - **Expected:** The file is displayed; the exam remains locked in the background.
   - You cannot save or download the file.
5. Try to upload a file.
   - **Expected:** Action is blocked (restricted profile).
6. Close the Duke Box overlay.
   - **Expected:** You return to the locked exam; Duke Box is closed.
7. Sign in to Duke Box and record network activity (for HAR file, see below).

### Capturing a HAR File (Duke Box Login)

**For engineers:** To debug Duke Box access issues, capture a HAR (HTTP Archive) file of the login and file-browsing session.

1. Open the FairPlay app on a device and start an exam with Duke Box enabled.
2. Open Chrome (or Edge on Windows) on your **computer** (not the device).
3. Press F12 to open Developer Tools → **Network** tab.
4. Check **"Preserve log"** (gear icon) so that all requests are saved even if the page reloads.
5. On the FairPlay device, open the Duke Box overlay and sign in.
6. Open a folder, a PDF, a docx, and an image file.
7. Back in Chrome, right-click in the Network tab → **"Save all as HAR with content"** → save the file (e.g., `box-login-har.txt`).
8. Share the HAR file with the developer (do NOT share passwords or sensitive tokens; the file may contain them, so redact if needed).

## Professor Student Help Page Actions

**Setup:** Open the live room as the professor while a student is taking an exam.

1. **Approve Resume:** A student's FairPlay app crashed and restarted.
   - **Student view:** "Wait for your teacher" message; exam is paused.
   - **Professor view:** Student list shows a badge **"Waiting for approval"** next to the student's name with an **"Approve"** button.
   - Tap **"Approve"** → time is added to the student's deadline (up to 15 minutes for the restart time).
   - **Expected (student):** "Wait" message clears; exam resumes.

2. **Release Device / Move to Another Computer:** A student needs to switch to a different device.
   - **Professor view:** In the student list, tap **"Move to another computer"** (or **"Release device"**).
   - **Expected:** The student's device binding is cleared; they can now start the exam on a new device.

3. **(Removed) Reissue Access Code:** there are no per-student exam access codes any more, so there is nothing to reissue. A student on a new device uses Release Device / Move to Another Computer (step 2).

4. **Reopen Exam:** The exam was closed early (manually by the professor), but a student did not finish.
   - **Professor view:** Tap **"Reopen"** next to the student.
   - **Expected (student):** If in-progress, the exam becomes writable again; if submitted, an option to "Resume" appears (depending on the exam settings).

5. **Grant Extra Time:** The student ran out of time or needs more time.
   - **Professor view:** Tap **"Extra time"** (or similar) and enter the number of minutes (e.g., 10).
   - **Expected (student):** The deadline is extended by that many minutes; the timer updates.

6. **Offline Exit Code:** The student claims the network is down or the app is unresponsive.
   - **Professor view:** Tap **"Offline exit"** and enter the Mac code or Windows code the student shows on their screen.
   - **Expected:** The system validates the code and issues an offline exit token to the student.
   - **Student view:** The app shows an "Exit code" field where you paste the token; the device unlocks after verification.

## End Screen After Hand-In

1. Complete an exam and tap **"Hand in"** or **"Submit"** (or wait for time-up auto-submit).
   - **Expected:** A congratulations screen appears:
     - **"Congratulations, your exam is submitted."**
     - Message about grades (varies: "You will receive your grade on Canvas…" for LMS, or "Your professor will share your grade." for non-LMS).
     - A button to **"Back to home"**.
   - The device is unlocked.
2. If the server is unreachable during submit:
   - **Expected:** A fallback screen offers: **"Try again now"**, **"Submit later from this computer"** (offline seal), or **"Ask the teacher for help"** (offline exit).

## iPad-Specific: Session Gone

**iPad only:** Test a scenario where the session token expires or is revoked while the exam is locked.

1. Start an exam on iPad and lock the device (using Assessment Mode or Single App Mode).
2. While locked, the session is revoked on the server (e.g., the professor reopens the exam or the token expires).
3. The iPad app sends a heartbeat or attempts to save an answer.
   - **Expected:** The server responds with "session_gone" (404 or 409).
   - The app detects this and shows: **"Your exam session ended. Ask your teacher for help."**
   - Device unlocks; you are prompted to contact the professor.

