"""DKU OJ-specific tables (the generic outbox is integrations.models.ExternalDelivery,
answer files are core: models.AnswerFile)."""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import BigInteger, DateTime, ForeignKey, Integer, LargeBinary, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, deferred, mapped_column

from ...db import Base
from ...timeutil import utcnow

BigIntPK = BigInteger().with_variant(Integer(), "sqlite")


class OjEditor(Base):
    """A DKU OJ editor of an exam: may open it in FairPlay (exam list, live room)."""
    __tablename__ = "oj_editors"
    __table_args__ = (UniqueConstraint("exam_id", "user_id", name="uq_oj_editor"),)
    id: Mapped[int] = mapped_column(BigIntPK, primary_key=True, autoincrement=True)
    exam_id: Mapped[str] = mapped_column(ForeignKey("exams.id", ondelete="CASCADE"), index=True, nullable=False)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True, nullable=False)


class OjRosterEntry(Base):
    """A student DKU OJ lists for an exam: only they may see and start it."""
    __tablename__ = "oj_roster"
    __table_args__ = (UniqueConstraint("exam_id", "user_id", name="uq_oj_roster"),)
    id: Mapped[int] = mapped_column(BigIntPK, primary_key=True, autoincrement=True)
    exam_id: Mapped[str] = mapped_column(ForeignKey("exams.id", ondelete="CASCADE"), index=True, nullable=False)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True, nullable=False)
    extra_time_seconds: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    first_name: Mapped[str | None] = mapped_column(String(200), nullable=True)
    last_name: Mapped[str | None] = mapped_column(String(200), nullable=True)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


class OjStudentGrant(Base):
    """Extra time a professor gave one student from DKU OJ. Kept apart from OjRosterEntry,
    whose extra_time_seconds is DKU OJ's own value and is overwritten by every roster sync."""
    __tablename__ = "oj_student_grants"
    __table_args__ = (UniqueConstraint("exam_id", "user_id", name="uq_oj_grant"),)
    id: Mapped[int] = mapped_column(BigIntPK, primary_key=True, autoincrement=True)
    exam_id: Mapped[str] = mapped_column(ForeignKey("exams.id", ondelete="CASCADE"), index=True, nullable=False)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True, nullable=False)
    extra_seconds: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    by: Mapped[str] = mapped_column(String(150), nullable=False, default="", server_default="")
    at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, nullable=False)


class OjAsset(Base):
    """A file referenced by a statement (fp-asset:<name>), checked against its sha256.
    Bytes in the database (the host's disk is not kept across deploys)."""
    __tablename__ = "oj_assets"
    __table_args__ = (UniqueConstraint("exam_id", "name", name="uq_oj_asset"),)
    id: Mapped[int] = mapped_column(BigIntPK, primary_key=True, autoincrement=True)
    exam_id: Mapped[str] = mapped_column(ForeignKey("exams.id", ondelete="CASCADE"), index=True, nullable=False)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    content_type: Mapped[str] = mapped_column(String(100), nullable=False)
    size: Mapped[int] = mapped_column(Integer, nullable=False)
    data: Mapped[bytes] = deferred(mapped_column(LargeBinary, nullable=False))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, nullable=False)


class OjState(Base):
    """Small named values: the sync cursor and the one-runner lock (holder + expiry)."""
    __tablename__ = "oj_state"
    name: Mapped[str] = mapped_column(String(64), primary_key=True)
    value: Mapped[str | None] = mapped_column(Text, nullable=True)
    holder: Mapped[str | None] = mapped_column(String(64), nullable=True)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


class ClassLogin(Base):
    """v2 §3/§4: the one temporary login a professor hands a DKU OJ class. Signing in with
    it only starts the student set-up (identify, then own username and password). The
    password is checked against its hash (same hashing as user passwords); since
    2026-10-24 a copy is also kept ENCRYPTED (AES-GCM, key derived from the server secret,
    setup.seal_login_password) so professors can see it again in DKU OJ (product decision,
    contract v2 §3). A class has at most one active login: a new one disables the old one
    (disabled_at)."""
    __tablename__ = "class_logins"
    id: Mapped[int] = mapped_column(BigIntPK, primary_key=True, autoincrement=True)
    class_id: Mapped[int] = mapped_column(BigInteger, index=True, nullable=False)     # DKU OJ class id
    class_name: Mapped[str] = mapped_column(String(200), nullable=False, default="", server_default="")
    course_name: Mapped[str] = mapped_column(String(200), nullable=False, default="", server_default="")
    username: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    password_hash: Mapped[str] = mapped_column(String(256), nullable=False)
    # "v1:<base64 nonce+ciphertext>"; None for logins made before 2026-10-24 (not recoverable)
    password_enc: Mapped[str | None] = mapped_column(String(256), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    disabled_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    disabled_reason: Mapped[str | None] = mapped_column(String(32), nullable=True)
    used: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    failed_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    created_by: Mapped[str] = mapped_column(String(150), nullable=False, default="", server_default="")


class ClassLoginEvent(Base):
    """Audit trail the professor sees: login_created / login_stopped / login_locked /
    setup_ok / setup_failed / student_reset. Never a password or token."""
    __tablename__ = "class_login_events"
    id: Mapped[int] = mapped_column(BigIntPK, primary_key=True, autoincrement=True)
    class_id: Mapped[int] = mapped_column(BigInteger, index=True, nullable=False)
    class_login_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True, index=True)
    kind: Mapped[str] = mapped_column(String(32), nullable=False)
    netid: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    actor: Mapped[str] = mapped_column(String(150), nullable=False, default="", server_default="")
    detail: Mapped[str] = mapped_column(String(300), nullable=False, default="", server_default="")
    at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, nullable=False)


class ExamAccessCode(Base):
    """Legacy (removed 2026-11): the per-student exam access code. Nothing reads or writes this
    table any more; the model stays only so the existing table is kept (no schema drop)."""
    __tablename__ = "exam_access_codes"
    __table_args__ = (UniqueConstraint("exam_id", "netid", name="uq_access_code_netid"),
                      UniqueConstraint("exam_id", "code_hmac", name="uq_access_code_hmac"))
    id: Mapped[int] = mapped_column(BigIntPK, primary_key=True, autoincrement=True)
    exam_id: Mapped[str] = mapped_column(ForeignKey("exams.id", ondelete="CASCADE"), index=True, nullable=False)
    netid: Mapped[str] = mapped_column(String(64), nullable=False)
    first_name: Mapped[str] = mapped_column(String(200), nullable=False, default="", server_default="")
    last_name: Mapped[str] = mapped_column(String(200), nullable=False, default="", server_default="")
    code_hmac: Mapped[str] = mapped_column(String(64), nullable=False)
    code_sealed: Mapped[str] = mapped_column(String(256), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, nullable=False)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    used_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    bound_device: Mapped[str | None] = mapped_column(String(128), nullable=True)
    bound_device_pub: Mapped[str | None] = mapped_column(String(200), nullable=True)
