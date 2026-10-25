Canvas mode: manual tests on the Mac app

Use a test Canvas course with a classic quiz that has an access code, and a teacher account linked to Canvas.

0. Code scope: the code is typed only on /courses/<id>/quizzes|assignments/<id> pages of the exam host. Sign-in and other pages of that host, and SSO hosts, never get it.
1. Managed classic exam: start the exam as a student. The app types the access code into the quiz page and the quiz starts. No code is visible on screen or in the exam page.
2. Wrong manual code: set the exam to manual with a wrong code. The app fills it 3 times, then shows "Ask your teacher for help". The exam view lists canvas_code_failed. The code is never shown.
3. Blocked host: in the quiz, open a link to a host that is not allowed (also try window.open to one). Nothing opens and blocked_navigation is recorded.
4. Duo / Microsoft sign-in popup: sign in through the SSO popup. On macOS 14.4 and newer the popup may stay a child window; on older macOS it opens in the main view. Both end signed in. canvas_sso_popup is recorded. A second popup while one is open is blocked.
5. Submit and finish: submit the quiz. About 2 s after the confirmation page, the app finishes the exam, the computer unlocks and Canvas is signed out (open the quiz link again after the next sign-in to FairPlay: Canvas asks for sign-in). Navigating away from the confirmation page within the 2 s must not finish the exam.
6. File upload question: open a quiz with a file upload question and click the upload button. No file panel opens and canvas_upload_unsupported is recorded.
7. Ordinary website exam: run a normal (non-Canvas) website exam. Allowed sites, finish URL and unlock behave as before, and no access code request is made.
8. Resume: quit the app mid-exam and reopen it, then resume. The page reloads, the code is filled again if the quiz asks for it, and the help alert only shows after a real failure (a 429 is retried up to 3 times, waiting the server's Retry-After, 1 to 20 s).
9. Device swap: after a swap to a second Mac, resume there. The second Mac fills the code; the first one is refused. Check canvas_code_issued / canvas_code_refused in the exam view: the server allows 6 code requests per attempt.
