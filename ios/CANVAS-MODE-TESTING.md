Canvas mode: manual tests on the iPad app

Canvas mode needs no access code. The teacher builds the quiz in Canvas without a code. FairPlay keeps the student on the exam course and quiz, and once the quiz has started the student cannot leave before submitting. Use a test Canvas course with a quiz without an access code, a second course, another quiz in the same course, and a course with pages, modules and files. The server must send canvas_scope. Run on a real iPad (Assessment Mode does not run in the simulator) and, where noted, with a hardware keyboard.

Policy and navigation

1. Sign in and dashboard: start a Canvas exam while signed out of Canvas. Sign in. When Canvas lands on the dashboard (or the course page), the app opens the exam quiz page once by itself. Go back to the dashboard by hand: it is not opened again.
2. Another course: before the quiz starts, open the dashboard, tap another course. The page does not open, the lock bar shows "This page is not available during the exam." for a few seconds and blocked_navigation (canvas page /courses/<id>) is recorded. The quiz's own course page, the course quiz list and the exam quiz page do open.
3. Inbox and other pages: tap Inbox, Account, Calendar, Groups, Help in Canvas global navigation. All blocked with the message and an event, before the quiz and during it.
4. Quiz opens: open the exam quiz. It starts without any code being asked or typed by the app. Another quiz of the same course is blocked.
5. Quiz phase: once the quiz page (classic: Take the quiz; new quizzes and assignments: the assignment page) has loaded, the dashboard, the course page and the course quiz list are blocked too. canvas_quiz_started is recorded once in the exam view. The quiz, its results page, sign-in hosts and Canvas /login pages keep working: let the Canvas session expire (or sign out in another browser) mid-quiz and check that signing in again works.
6. Open book off: during the quiz, Pages, Modules and Files links of the course are blocked.
7. Open book on: the teacher turns on open book and a new attempt starts. Course pages, modules and files open during the quiz; a file download link (/files/<id>/download) stays blocked; Inbox and other courses stay blocked.
8. My quiz: from any allowed page, tap "Go to my quiz" in the lock bar. The exam quiz page opens. Tap it while on the quiz or while a page is still loading: nothing happens.
9. Back: with a previous allowed page in the history, "Back" goes there. During the quiz Back only returns to pages of the exam's Canvas host that are not sign-in pages. On the first page or when the previous page is not allowed, Back is greyed out. Back never reaches a blocked page. The edge-swipe back gesture does nothing.
10. Typed addresses and link tricks: a link with target=_blank to /inbox is blocked with the message. Path tricks in a link (/courses/<id>/quizzes/<id>/../../../inbox, %2f, //) are blocked.
11. In-page route changes: in the new quizzes or assignment page, use any in-page link that changes the address without a page load (a Canvas global navigation item in an open-book exam, a link to /inbox). If the address becomes a blocked page, the app returns to the previous allowed page (or the exam quiz) within about a second, shows the message, and records one blocked_navigation (canvas page /... (in page)) per page. Tapping the same link repeatedly never makes the page flicker more than once per second.
12. Stuck recovery: a page that keeps pushing a blocked address in the quiz brings the view back to the exam quiz up to 5 times at one per second, then once more to the exam link, then stops; the message stays.
13. Redirect loop: a link that keeps redirecting to a blocked page adds one blocked_navigation event per page, not one per redirect.
14. Missing scope: an exam of kind canvas whose canvas_scope is null, missing or malformed does not start; the app reports externalFailed with bad_request.
15. Bad scope: a canvas_scope with a wrong id, or an exam link that the scope does not allow, ends with externalFailed bad_request and the exam does not open.

Lock-in

16. No exit in the quiz phase: once canvas_quiz_started is recorded, the lock bar has no Finish exam button. Nothing in the app ends the exam except the finish page, a teacher close, the deadline, or the invigilator exit (hold the lock bar 3 s or Ctrl-Opt-Cmd-E).
17. Before the quiz: in the lobby the Finish exam button is shown and works as in a website exam.
18. Relaunch mid-quiz: start the quiz, force-quit FairPlay, open it again and resume the attempt. The exam reopens in the quiz phase: no Finish exam button, the dashboard and course page are blocked from the first page, and canvas_quiz_started is not recorded again.
19. Exam server pages: while the Canvas exam runs, a link that points at a FairPlay server page (for example /student/external-done/<attempt>) in a Canvas page is blocked and does not end the exam.
20. Finish: submit the quiz. About 2 s after the confirmation page, the app finishes the exam, the iPad unlocks and Canvas data is cleared. Navigating away from the confirmation page within the 2 s must not finish the exam.
21. Teacher close and deadline: close the exam from the live room, and separately let a short exam run out, while in the quiz phase. Both finish the exam and unlock.
22. Invigilator exit: use the invigilator exit during the quiz. The exam ends, the iPad unlocks and the quiz marker is cleared (starting the same attempt again begins in the lobby).

iPad behaviours that could leave the page

23. Long-press on a link and on an image in Canvas: no link preview, no context menu, no share sheet.
24. Select text in a quiz question and in an essay answer: the edit menu offers only Select, Select All, Delete, Undo, Redo. No Look Up, Translate, Share, Speak, Search Web, Copy or Paste.
25. Drag: try to drag a link, an image and selected text out of the page (finger or Apple Pencil), and to drop text onto an answer field. Nothing is dragged or dropped. Quiz question types that use drag and drop (New Quizzes ordering and categorization) cannot be answered by dragging: check that the keyboard or button alternative Canvas offers works.
26. Find in page: Cmd-F on a hardware keyboard shows no find bar.
27. Web Share: from the browser console of a dev build, navigator.share is undefined on Canvas pages.
28. Edge swipes, the three-finger swipe and text selection handles never move the view to another page.
29. Single window: no second window or tab ever appears. A link with target=_blank to an allowed Canvas page loads in the main view; to /inbox or another host it is blocked with the message and blocked_navigation (popup <host>). window.open(url) from a page is a plain navigation in the same view and goes through the same checks (a blocked address shows the message and blocked_navigation like a link). window.open() with an empty address does nothing. window.print() does nothing.
30. Sign-in popups: Duo and Microsoft sign-in open in the same view (window.open becomes a plain navigation) and end signed in. canvas_sso_popup is recorded only for a target=_blank link to another host, not for window.open. A sign-in flow that needs window.opener to receive the result cannot finish: if it fails at your school, sign in to Canvas before the exam or change the sign-in route, and report it.
31. Downloads: a link in Canvas that starts a download in the main view (a file attachment link, ?download=1, ?download_frd=1) is cancelled with the message and a blocked_navigation event. No download starts, no Files sheet opens.
32. File upload question: open a quiz with a file upload question and tap the upload button. No picker opens and nothing else is recorded.
33. Split View, Slide Over, Stage Manager and Control Center: Assessment Mode keeps them off. Check that none of them shows another app or window during the quiz.

Other behaviours

34. Ordinary website exam: run a normal (non-Canvas) website exam, then a Canvas exam, then a normal one again. Allowed sites, finish URL, the Finish exam button and unlock behave as before, and window.open on the allowed sites behaves as before after a Canvas exam (the Canvas page script is removed when the exam ends). Long-press link menus are off in every exam.
35. Finish pattern: a Canvas exam only finishes from finish_url_regex; finish_url_contains is ignored. With a missing, empty or unusable finish pattern (too long or unsafe), the lock bar keeps the Finish exam button even in the quiz phase, shows "The finish page could not be set up. Ask your teacher." until the exam ends, and the exam view lists one blocked_navigation event (finish pattern rejected).
36. Offline: switch Wi-Fi off in the quiz and on again. The offline screen shows and its retry reloads the exam quiz page, not a FairPlay page.
37. Page crash: if the Canvas page process stops, the page reloads and the exam stays in the same phase.
38. Web data: after finishing, sign-in cookies of every site except the FairPlay server (and its parent domains) are cleared. Check whether your Canvas host shares a registrable domain with the FairPlay server; if it does, Canvas stays signed in and must be signed out by hand when that matters.
39. First login at DKU Canvas: with a student account that has never signed in, complete the first-login steps, for example accepting the terms of use. /users, /profile and /accounts are blocked, so check that the first login still finishes and the exam link opens afterwards. If a step needs one of those pages, it must be done before the exam.
40. Broad allowed sites: a teacher-added wildcard such as *.instructure.com lets students follow links to any site under it. Check the allowed sites list of a real exam and keep it as narrow as possible.
41. Finish needs the quiz page first: in a classic quiz, open the quiz landing page (Go to my quiz or Back) and then follow a link to the quiz results or history page. The exam does not finish. Take the quiz again (Take the quiz page loads) and submit: the results page finishes the exam. After relaunch mid-quiz the same holds (the armed state is kept).
42. New Quizzes: the submission screen shows inside a frame of the assignment page. Submitting finishes the exam about 2 s later, though the main page address does not change.
43. Other hosts in the quiz phase: the server sends the sign-in hosts and (New Quizzes) quiz-lti hosts in canvas_scope. In the quiz phase a main-frame page on another host opens only if it matches one of those lists (exact host, or *.suffix for subdomains only); anything else, even a host the exam lists as allowed, is blocked and the view returns to the exam quiz within about a second. Check Duo, Microsoft and the school sign-in page still work mid-quiz, that the quiz-lti frame of a New Quizzes exam loads, and that a canvas_scope with a malformed sign_in_hosts or quiz_lti_hosts list ends with externalFailed bad_request.
44. Notice on a 744 pt iPad: in the lobby, trigger a blocked page. The message shows in the lock bar (the title and the clock hide for 4 s), readable without overlap.
45. Reload and crash recovery: after a page crash or a reload while a Canvas exam runs, the view loads the exam quiz link, never a FairPlay page.
42. Finish matches the path only: with the classic pattern, open a quiz page whose address has a query such as ?x=/quizzes/1/history (or a fragment). The exam does not finish. The real results or history page still finishes it.
43. Relaunch while armed: take the quiz (Take the quiz page loaded), force quit and relaunch. The quiz is restored armed: submitting finishes the exam. Relaunch after viewing the quiz landing page: submitting does not finish until the quiz page is taken again.
44. Pending finish: submit so that the finish page loads, then tap Go to my quiz within two seconds. The exam still finishes.
