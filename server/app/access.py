"""Audit 2026-10-12 S-03: which students may see and take an exam.

An exam belongs to a class (classes / class_members) or to a linked LMS course
(lms_course_members). A linked course decides on its own (its roster is the source of
truth); without a link the class decides. Only members see the exam while it is live,
download its offline package, get the package key and start it. An exam with neither is
not open to anyone (the teacher can't open it, see routes_teacher.open_exam)."""
from __future__ import annotations

from fastapi import HTTPException
from sqlalchemy import and_, exists, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from .models import ClassMember, CourseLink, CourseMember, Exam, SchoolClass


def member_of_exam(user_id: int):
    """SQL condition: the student is in the exam's class or linked LMS course. An exam
    from an external system is decided by that integration alone (its roster)."""
    from .integrations import registry
    in_class = exists().where(ClassMember.class_id == Exam.class_id, ClassMember.user_id == user_id)
    in_course = exists().where(CourseMember.course_link_id == Exam.course_link_id,
                               CourseMember.user_id == user_id)
    own = Exam.source == registry.FAIRPLAY
    return or_(and_(own, Exam.course_link_id.is_not(None), in_course),
               and_(own, Exam.course_link_id.is_(None), in_class),
               *[and_(Exam.source == s.id, s.roster_condition(user_id)) for s in registry.sources()])


async def is_member(session: AsyncSession, class_id: int | None, course_link_id: int | None,
                    user_id: int) -> bool:
    if course_link_id is not None:
        return (await session.execute(select(CourseMember.id).where(
            CourseMember.course_link_id == course_link_id, CourseMember.user_id == user_id).limit(1))).first() is not None
    if class_id is not None:
        return (await session.execute(select(ClassMember.id).where(
            ClassMember.class_id == class_id, ClassMember.user_id == user_id).limit(1))).first() is not None
    return False


async def require_member(session: AsyncSession, exam: dict, user_id: int) -> None:
    """403 with the class name unless the student belongs to the exam's class/course.
    `exam` is the cached student view (get_exam_cached)."""
    from .integrations.registry import integration_for
    integ = integration_for(exam)
    if integ is not None:
        if await integ.can_take(session, exam["id"], user_id):
            return
        raise HTTPException(403, integ.take_refused_message())
    class_id, link_id = exam.get("class_id"), exam.get("course_link_id")
    if await is_member(session, class_id, link_id, user_id):
        return
    name = None
    if link_id is not None:
        name = (await session.execute(select(CourseLink.name).where(CourseLink.id == link_id))).scalar()
    elif class_id is not None:
        name = (await session.execute(select(SchoolClass.name).where(SchoolClass.id == class_id))).scalar()
    raise HTTPException(403, f"This exam is for {name} students. Ask your teacher for the class code."
                        if name else "This exam is not for your class.")


# ---------------------------------------------------------------- 2026-10-23: open to
# Exam.open_to: NULL = everyone the exam is for; a list of user ids = only those students.
# Checked on the server only (start, offline package, package key): the start code alone
# never lets anyone else in. A student who already started is never stopped by it.
NOT_OPEN_FOR_YOU = "This exam isn't open for you yet"


def open_to_out(open_to) -> str | list[int]:
    """API shape: "all" or the sorted list of student ids."""
    return "all" if open_to is None else sorted({int(x) for x in open_to})


def is_open_for(open_to, user_id: int) -> bool:
    return open_to is None or int(user_id) in {int(x) for x in open_to}


def eligible_ids(e: Exam):
    """SQL select of the user ids that may take the exam (class, LMS course or the external
    source's roster); the selection of open_to is always a subset of these."""
    from .integrations.registry import integration_for
    integ = integration_for(e)
    if integ is not None:
        return integ.roster_user_ids(e.id)
    if e.course_link_id is not None:
        return select(CourseMember.user_id).where(CourseMember.course_link_id == e.course_link_id)
    if e.class_id is not None:
        return select(ClassMember.user_id).where(ClassMember.class_id == e.class_id)
    return None


async def eligible_students(session: AsyncSession, e: Exam) -> list[tuple[int, str, str]]:
    """(id, username, name) of the exam's students, by name."""
    from .models import User
    ids = eligible_ids(e)
    if ids is None:
        return []
    rows = await session.execute(select(User.id, User.username, User.name)
                                 .where(User.id.in_(ids), User.role == "student")
                                 .order_by(User.name, User.username))
    return [(int(i), u, n) for i, u, n in rows.all()]


async def require_open_for(session: AsyncSession, exam_id: str, user_id: int) -> None:
    """403 NOT_OPEN_FOR_YOU unless the exam is open to this student or they already have
    an attempt (a resume is never blocked). Reads the database, not the exam cache."""
    from .models import Attempt
    open_to = (await session.execute(select(Exam.open_to).where(Exam.id == exam_id))).scalar()
    if is_open_for(open_to, user_id):
        return
    started = (await session.execute(select(Attempt.id).where(
        Attempt.exam_id == exam_id, Attempt.student_id == user_id).limit(1))).first()
    if started is None:
        raise HTTPException(403, NOT_OPEN_FOR_YOU)


async def require_listed(session: AsyncSession, exam_id: str, user_id: int) -> None:
    """A selected-students exam is private: for anyone else (without an attempt) it answers
    exactly like an unknown exam id."""
    from .models import Attempt
    open_to = (await session.execute(select(Exam.open_to).where(Exam.id == exam_id))).scalar()
    if is_open_for(open_to, user_id):
        return
    started = (await session.execute(select(Attempt.id).where(
        Attempt.exam_id == exam_id, Attempt.student_id == user_id).limit(1))).first()
    if started is None:
        raise HTTPException(404, "Exam not found")
