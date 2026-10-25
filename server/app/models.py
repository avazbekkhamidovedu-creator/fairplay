from __future__ import annotations

from datetime import datetime

from sqlalchemy import (JSON, BigInteger, Boolean, DateTime, Float, ForeignKey, Index, Integer,
                        LargeBinary, String, Text, UniqueConstraint, false, text, true)
from sqlalchemy.orm import Mapped, deferred, mapped_column

from .db import Base
from .timeutil import utcnow

# BIGINT autoincrement on Postgres, INTEGER PRIMARY KEY (rowid) on SQLite.
BigIntPK = BigInteger().with_variant(Integer(), "sqlite")


class User(Base):
    __tablename__ = "users"
    id: Mapped[int] = mapped_column(BigIntPK, primary_key=True, autoincrement=True)
    username: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    role: Mapped[str] = mapped_column(String(16), nullable=False)
    password_hash: Mapped[str] = mapped_column(String(256), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, nullable=False)
    # Audit S-15: bumped to sign this user out everywhere (tokens carry the version).
    token_version: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    # DKU OJ bridge v2: the DKU OJ username (NetID) this account belongs to, and when the
    # student chose their own username and password (None = never set up / reset). Unique
    # index (not a constraint) so db._add_missing_columns can add it to older databases.
    netid: Mapped[str | None] = mapped_column(String(64), nullable=True, unique=True, index=True)
    set_up_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


class Exam(Base):
    __tablename__ = "exams"
    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    teacher_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True, nullable=False)
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    duration_min: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(String(16), default="draft", index=True, nullable=False)
    start_code: Mapped[str | None] = mapped_column(String(8), nullable=True)
    questions: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    question_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    total_points: Mapped[float] = mapped_column(Float, nullable=False, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, index=True, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, nullable=False)
    # slice 2 (server_default so app.db can ALTER TABLE ADD COLUMN on an existing database)
    course: Mapped[str] = mapped_column(String(64), nullable=False, default="", server_default="")
    room: Mapped[str] = mapped_column(String(64), nullable=False, default="", server_default="")
    scheduled_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    shuffle_questions: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False,
                                                    server_default=false())
    grades_released: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False,
                                                  server_default=false())
    released_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    closed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    # slice 3: external-website exams (SEB-style). mode "questions" keeps the external
    # fields null/[]; mode "external" has no questions and opens external_url in the app.
    mode: Mapped[str] = mapped_column(String(16), nullable=False, default="questions",
                                      server_default="questions")
    external_url: Mapped[str | None] = mapped_column(String(2000), nullable=True)
    allowed_domains: Mapped[list] = mapped_column(JSON, nullable=False, default=list,
                                                  server_default=text("'[]'"))
    finish_url_contains: Mapped[str | None] = mapped_column(String(200), nullable=True)
    finish_url_regex: Mapped[str | None] = mapped_column(String(300), nullable=True)
    external_kind: Mapped[str | None] = mapped_column(String(16), nullable=True)
    canvas_quiz_type: Mapped[str | None] = mapped_column(String(16), nullable=True)
    canvas_code_source: Mapped[str | None] = mapped_column(String(16), nullable=True)
    canvas_code_enc: Mapped[str | None] = mapped_column(Text, nullable=True)
    canvas_code_failed: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False,
                                                     server_default=false())
    canvas_release_failed: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False,
                                                        server_default=false())
    canvas_removed_hosts: Mapped[list] = mapped_column(JSON, nullable=False, default=list,
                                                       server_default=text("'[]'"))
    canvas_open_claim: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    # 2026-10-14: apps the teacher allows during the exam (keys of allowed_apps.CATALOG);
    # every other windowed app is closed when the computer locks.
    allowed_apps: Mapped[list] = mapped_column(JSON, nullable=False, default=list,
                                               server_default=text("'[]'"))
    # slice 5: LMS sync. Plain (non-FK) columns so db._add_missing_columns can ALTER them in.
    course_link_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True, index=True)
    # Audit S-03: the class whose students see and may take this exam (or course_link_id).
    # Plain column (not FK) so db._add_missing_columns can add it to older databases.
    class_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True, index=True)
    # Audit S-03: 32 random bytes (hex) mixed into the offline-package key. Handed to class
    # members only while the exam is live, so a package downloaded early can't be opened by
    # guessing the 6-digit start code.
    package_secret: Mapped[str | None] = mapped_column(String(64), nullable=True)
    lms_assignment_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    # slice 6: "computer" | "paper" | "website". NULL on rows from before slice 6: derived
    # from mode by services.exam_delivery (external -> website, else computer).
    delivery: Mapped[str | None] = mapped_column(String(16), nullable=True)
    # Audit 2026-10-12: per-exam invigilator code (emergency exit in the app), checked by
    # the server with an attempt limit. New on every open.
    invigilator_code: Mapped[str | None] = mapped_column(String(8), nullable=True)
    # S3 (2026-10-20): a student coming back after FairPlay closed (crash, force quit) waits
    # until the teacher approves (routes_student._resume). On by default.
    resume_needs_approval: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True,
                                                        server_default=true())
    # S6: only the strongest lock counts (Windows: separate exam desktop; Mac: Apple's
    # assessment mode, not built yet). A beat without sep=1 is flagged weak_lock once.
    require_strong_lock: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False,
                                                      server_default=false())
    # 2026-10-22: the built-in sample exam ("Try a sample exam", app.trial). Owned by the
    # system account, in no class; every student may take it any time. Its attempts are
    # never shown to teachers and never count anywhere.
    is_trial: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False,
                                           server_default=false(), index=True)
    # 2026-10-23 (exam tools): what students get inside FairPlay instead of outside apps.
    # calculator "off" | "scientific" | "graphing"; open_book: students upload materials
    # before they start (ExamMaterial); websites: host names the exam pane may show (each
    # host and its subdomains), "questions" exams only.
    calculator: Mapped[str] = mapped_column(String(16), nullable=False, default="off",
                                            server_default="off")
    open_book: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False,
                                            server_default=false())
    websites: Mapped[list] = mapped_column(JSON, nullable=False, default=list,
                                           server_default=text("'[]'"))
    # 2026-10-24: allowed websites given as full links ([{"host", "url", "title"}]; each host is
    # also in `websites`): the page the exam's websites tab opens first. The apps never read it.
    website_links: Mapped[list] = mapped_column(JSON, nullable=False, default=list,
                                                server_default=text("'[]'"))
    # 2026-10-24: open book: students may upload their materials this many days before
    # scheduled_at (0 = off: only on "Before you start" once the exam is open). The window ends
    # at scheduled_at or when the exam opens, whichever is first (app.materials.upload_window).
    upload_opens_days: Mapped[int] = mapped_column(Integer, nullable=False, default=0,
                                                   server_default="0")
    # Phase 4: language of coding questions that do not set their own (students never pick)
    default_language: Mapped[str] = mapped_column(String(16), nullable=False, default="python",
                                                  server_default="python")
    # Integrations (app.integrations): "fairplay" (made here) or the id of the external
    # system the exam comes from (e.g. "dkuoj"), which then owns its content and grades.
    # external_frozen_version is set by the first student start (later versions never change
    # a running exam); external_meta is the integration's own data about the exam.
    source: Mapped[str] = mapped_column(String(16), nullable=False, default="fairplay",
                                        server_default="fairplay")
    external_key: Mapped[str | None] = mapped_column(String(200), nullable=True, index=True)
    external_version: Mapped[str | None] = mapped_column(String(32), nullable=True)
    external_frozen_version: Mapped[str | None] = mapped_column(String(32), nullable=True)
    external_synced_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    external_removed: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False,
                                             server_default=false())
    external_meta: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict,
                                          server_default=text("'{}'"))
    # 2026-10-24: what students may see after handing in: "nothing" | "score" | "breakdown" |
    # "answers" | "answers_and_results" (app.review; fits String(24)). Set by the exam's source (DKU OJ: the professor's
    # "Student review after hand-in"); NULL = the source's default. Native exams: unused.
    review_after_handin: Mapped[str | None] = mapped_column(String(24), nullable=True)
    # 2026-10-23: who may start the exam. NULL = everyone the exam is for (its class, LMS
    # course or external roster); a list of user ids = only those students (others still
    # see it as "not open for you yet"). Set when opening and while live.
    open_to: Mapped[list | None] = mapped_column(JSON, nullable=True)
    # 2026-10-23: the exam closes by itself at this time (UTC). Native exams: optional,
    # set in the editor. External exams take it from their source
    # (ExternalSource.closes_at), never from this column. auto_closed: the last close was
    # the automatic one (app.closing), not the teacher's.
    closes_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    auto_closed: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False,
                                              server_default=false())


class Attempt(Base):
    __tablename__ = "attempts"
    __table_args__ = (UniqueConstraint("exam_id", "student_id", name="uq_attempt_exam_student"),)
    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    exam_id: Mapped[str] = mapped_column(ForeignKey("exams.id"), index=True, nullable=False)
    student_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True, nullable=False)
    status: Mapped[str] = mapped_column(String(16), default="in_progress", nullable=False)
    started_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    deadline_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    submitted_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    answers: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    client_seq: Mapped[int] = mapped_column(BigInteger, nullable=False, default=-1)
    receipt: Mapped[str | None] = mapped_column(String(16), nullable=True)
    auto_submitted: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    late: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    # Audit S-02: answers that arrived with a submit after the deadline. Kept for the
    # teacher to look at, never graded (the graded set is what was saved in time).
    late_answers: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    # Audit S-10: answered-question count kept with every save, so the live room (polled
    # every 3 s) never loads the answers themselves. NULL on rows from before.
    answered: Mapped[int | None] = mapped_column(Integer, nullable=True)
    score: Mapped[float | None] = mapped_column(Float, nullable=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, nullable=False)
    # slice 2: teacher grades {qid: {points, comment, rubric_scores, graded_at}} + overall comment.
    grades: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict, server_default=text("'{}'"))
    overall_comment: Mapped[str] = mapped_column(Text, nullable=False, default="", server_default="")
    # Denormalised count of pending essay answers (kept in sync on submit/close/grade) so
    # exam lists and the grading overview can aggregate without loading every answer.
    needs_grading: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    # slice 6 (Scan & Grade): "computer" or "paper" (answers then hold scan crops, see
    # app.scan) and the AI drafts per question {qid: {points, comment, confidence, ...}}.
    source: Mapped[str] = mapped_column(String(16), nullable=False, default="computer",
                                        server_default="computer")
    ai_drafts: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict,
                                            server_default=text("'{}'"))
    # Audit 2026-10-12 #1-#3, #6: the app install that started the attempt and the current
    # session (sent as X-FairPlay-Session on every write). A start from another device is
    # refused until the teacher releases the device; a re-start on the same device rotates
    # the session and is flagged. client = "app" | "browser" (browser only when
    # FAIRPLAY_REQUIRE_APP=0).
    device_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    session_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    entries: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    client: Mapped[str | None] = mapped_column(String(16), nullable=True)
    # S5 (2026-10-20): handed in from answers the app sealed offline at time-up. late_sealed =
    # sealed after deadline + grace (the sealed answers went to late_answers). sealed_proof
    # makes a repeated upload of the same seal return the same receipt.
    late_sealed: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default=false())
    sealed_proof: Mapped[str | None] = mapped_column(String(64), nullable=True)
    # S3: waiting for the teacher after a new app run resumed this attempt. pending_since
    # stays set after approval (approval = "approved"); the wait is added to the deadline.
    pending_resume: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False,
                                                 server_default=false())
    pending_since: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    # S4: the student asked to continue on another computer (swap_device_id); once the
    # teacher approves, the old session may still send one final save for 120 s.
    swap_device_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    swap_requested_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    swap_old_session: Mapped[str | None] = mapped_column(String(64), nullable=True)
    swap_approved_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    # Integrations: the result the external system released for this attempt:
    # {released, total, total_points, updated_at, questions: [{question_id, points, feedback_md}]}
    external_result: Mapped[dict | None] = mapped_column(JSON, nullable=True)


class IntegrityEvent(Base):
    __tablename__ = "integrity_events"
    __table_args__ = (Index("ix_events_exam_attempt", "exam_id", "attempt_id"),
                      Index("ix_events_exam_review", "exam_id", "review"))
    id: Mapped[int] = mapped_column(BigIntPK, primary_key=True, autoincrement=True)
    attempt_id: Mapped[str] = mapped_column(ForeignKey("attempts.id", ondelete="CASCADE"), index=True,
                                            nullable=False)
    exam_id: Mapped[str] = mapped_column(ForeignKey("exams.id", ondelete="CASCADE"), index=True,
                                         nullable=False)
    type: Mapped[str] = mapped_column(String(64), nullable=False)
    detail: Mapped[str] = mapped_column(String(500), nullable=False, default="")
    client_time: Mapped[str | None] = mapped_column(String(64), nullable=True)
    server_time: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    review: Mapped[str] = mapped_column(String(16), nullable=False, default="open", server_default="open")
    note: Mapped[str] = mapped_column(String(500), nullable=False, default="", server_default="")


# ---------------------------------------------------------------- slice 5: LMS sync
class LmsConnection(Base):
    """A teacher's link to an LMS. `credential` is the API token sealed with
    app.lms.secretbox (never returned by the API)."""
    __tablename__ = "lms_connections"
    id: Mapped[int] = mapped_column(BigIntPK, primary_key=True, autoincrement=True)
    teacher_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True, nullable=False)
    provider: Mapped[str] = mapped_column(String(16), nullable=False)
    base_url: Mapped[str] = mapped_column(String(500), nullable=False)
    credential: Mapped[str] = mapped_column(Text, nullable=False)
    label: Mapped[str] = mapped_column(String(120), nullable=False, default="", server_default="")
    lms_user_id: Mapped[str] = mapped_column(String(64), nullable=False, default="", server_default="")
    lms_user_name: Mapped[str] = mapped_column(String(200), nullable=False, default="",
                                               server_default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, nullable=False)
    last_sync_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    last_error: Mapped[str | None] = mapped_column(String(500), nullable=True)


class CourseLink(Base):
    __tablename__ = "lms_course_links"
    __table_args__ = (UniqueConstraint("connection_id", "lms_course_id", name="uq_link_conn_course"),)
    id: Mapped[int] = mapped_column(BigIntPK, primary_key=True, autoincrement=True)
    connection_id: Mapped[int] = mapped_column(ForeignKey("lms_connections.id", ondelete="CASCADE"),
                                               index=True, nullable=False)
    lms_course_id: Mapped[str] = mapped_column(String(64), nullable=False)
    name: Mapped[str] = mapped_column(String(300), nullable=False, default="")
    code: Mapped[str] = mapped_column(String(120), nullable=False, default="", server_default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, nullable=False)
    last_sync_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    last_error: Mapped[str | None] = mapped_column(String(500), nullable=True)


class CourseMember(Base):
    __tablename__ = "lms_course_members"
    __table_args__ = (UniqueConstraint("course_link_id", "user_id", name="uq_member_link_user"),
                      UniqueConstraint("course_link_id", "lms_user_id", name="uq_member_link_lms"))
    id: Mapped[int] = mapped_column(BigIntPK, primary_key=True, autoincrement=True)
    course_link_id: Mapped[int] = mapped_column(
        ForeignKey("lms_course_links.id", ondelete="CASCADE"), index=True, nullable=False)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True, nullable=False)
    lms_user_id: Mapped[str] = mapped_column(String(64), nullable=False)


class SchoolClass(Base):
    """Audit S-03: a teacher's class. Students join with its code (or are added by
    username); an exam belongs to one class and only its members see it."""
    __tablename__ = "classes"
    id: Mapped[int] = mapped_column(BigIntPK, primary_key=True, autoincrement=True)
    teacher_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True, nullable=False)
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    # 2026-10-13: shown on the class card, e.g. "Fall 2026 · Section 2" (free text).
    term: Mapped[str | None] = mapped_column(String(80), nullable=True)
    join_code: Mapped[str] = mapped_column(String(16), unique=True, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, nullable=False)
    # Integrations: "<source>:<id>" of the external class this class mirrors (None = made here)
    external_ref: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)


class ClassMember(Base):
    __tablename__ = "class_members"
    __table_args__ = (UniqueConstraint("class_id", "user_id", name="uq_class_member"),)
    id: Mapped[int] = mapped_column(BigIntPK, primary_key=True, autoincrement=True)
    class_id: Mapped[int] = mapped_column(ForeignKey("classes.id", ondelete="CASCADE"),
                                          index=True, nullable=False)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True, nullable=False)
    joined_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, nullable=False)


class DeviceCheck(Base):
    """S1 (2026-10-20): the latest device check a student's FairPlay app ran, one row per
    (student, platform). The teacher's readiness list reads it before the exam."""
    __tablename__ = "device_checks"
    __table_args__ = (UniqueConstraint("student_id", "platform", name="uq_device_check_student_platform"),)
    id: Mapped[int] = mapped_column(BigIntPK, primary_key=True, autoincrement=True)
    student_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True, nullable=False)
    platform: Mapped[str] = mapped_column(String(300), nullable=False)
    app_version: Mapped[str] = mapped_column(String(300), nullable=False, default="")
    os_version: Mapped[str] = mapped_column(String(300), nullable=False, default="")
    # [{id, ok, message, info}] as sent (validated by schemas.DeviceCheckIn)
    checks: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    exam_id: Mapped[str | None] = mapped_column(String(300), nullable=True)
    checked_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, nullable=False)


class ExamMaterial(Base):
    """2026-10-23: a file a student brings to an open-book exam (PDF, PNG, JPEG, TXT, MD).
    Uploaded before the attempt exists, read inside the exam. The bytes live in the
    database (Render's disk is not kept across deploys); `data` is deferred so lists never
    load it."""
    __tablename__ = "exam_materials"
    __table_args__ = (Index("ix_materials_exam_student", "exam_id", "student_id"),)
    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    exam_id: Mapped[str] = mapped_column(ForeignKey("exams.id", ondelete="CASCADE"), nullable=False)
    student_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True, nullable=False)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    kind: Mapped[str] = mapped_column(String(8), nullable=False)          # pdf | png | jpg | txt | md
    size: Mapped[int] = mapped_column(Integer, nullable=False)
    data: Mapped[bytes] = deferred(mapped_column(LargeBinary, nullable=False))
    # 2026-10-23: hex sha256 of `data`, set on upload; the download's ETag ('' on older rows)
    sha256: Mapped[str] = mapped_column(String(64), nullable=False, default="", server_default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, nullable=False)


class AnswerFile(Base):
    """A file a student hands in for a file_upload question (bytes in the database: the
    host's disk is not kept across deploys)."""
    __tablename__ = "answer_files"
    __table_args__ = (Index("ix_answer_files_attempt", "attempt_id", "question_id"),)
    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    attempt_id: Mapped[str] = mapped_column(ForeignKey("attempts.id", ondelete="CASCADE"), nullable=False)
    student_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True, nullable=False)
    question_id: Mapped[str] = mapped_column(String(64), nullable=False)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    size: Mapped[int] = mapped_column(Integer, nullable=False)
    sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    data: Mapped[bytes] = deferred(mapped_column(LargeBinary, nullable=False))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, nullable=False)


# Integration tables (registered with Base here so create_all sees them)
from .integrations import models as _integration_models  # noqa: E402,F401


class Lease(Base):
    """2026-10-23: a named lease so one worker at a time runs a background job (e.g. the
    automatic close, app.closing). holder + expiry; an expired lease can be taken over."""
    __tablename__ = "leases"
    name: Mapped[str] = mapped_column(String(64), primary_key=True)
    holder: Mapped[str | None] = mapped_column(String(64), nullable=True)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
