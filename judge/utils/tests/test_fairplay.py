"""FairPlay bridge: the FairPlay tag, the hiding rule, assignment checks and the exam package."""
import hashlib
import json
from datetime import timedelta

from django.contrib.auth.models import AnonymousUser
from django.core.exceptions import ValidationError
from django.core.files.base import ContentFile
from django.test import TestCase, override_settings
from django.utils import timezone

from judge.models import Contest, ContestProblem, ContestTag, EditorAttachment, Problem, Submission
from judge.models.tests.util import create_contest_problem, create_organization, create_problem, create_user
from judge.utils import fairplay as fairplay_utils
from judge.utils.fairplay import FAIRPLAY_TAG, fairplay_check_contest, fairplay_seb_error, fairplay_enabled, \
    fairplay_exams, fairplay_exempt, fairplay_hidden_for, fairplay_hidden_q, is_fairplay_exam, set_fairplay_exam
from judge.utils.fairplay_delivery import record_added_minutes
from judge.utils.fairplay_package import build_exam_entry, build_package, exam_roster, extract_samples
from judge.views.tests.fairplay_base import FairPlayDataMixin, SECRETS, TOKEN, create_contest, set_fairplay

# Keys that would carry an answer key or hidden grading data.
FORBIDDEN_KEYS = {
    'is_correct', 'correct', 'correct_options', 'answers', 'accepted_answers', 'reference_answer',
    'ai_rubric', 'rubric', 'grading_table', 'harness', 'harness_code', 'test_cases', 'tests', 'solution',
    'input_file', 'output_file', 'checker',
}


def walk_keys(value):
    if isinstance(value, dict):
        for key, item in value.items():
            yield key
            yield from walk_keys(item)
    elif isinstance(value, list):
        for item in value:
            yield from walk_keys(item)


class FairPlaySwitchTestCase(TestCase):
    @override_settings(FAIRPLAY_TOKEN='')
    def test_unset_token_is_off(self):
        self.assertFalse(fairplay_enabled())

    @override_settings(FAIRPLAY_TOKEN='short-token')
    def test_short_token_is_off(self):
        self.assertFalse(fairplay_enabled())

    @override_settings(FAIRPLAY_TOKEN=TOKEN)
    def test_long_token_is_on(self):
        self.assertTrue(fairplay_enabled())


class FairPlayTagTestCase(TestCase):
    """A FairPlay exam is an assignment with the ``fairplay`` contest tag: no column of its own."""

    def test_tag_on_and_off(self):
        contest = create_contest('fptag')
        self.assertFalse(is_fairplay_exam(contest))
        self.assertFalse(ContestTag.objects.filter(name=FAIRPLAY_TAG).exists())
        set_fairplay_exam(contest, True)
        self.assertTrue(is_fairplay_exam(contest))
        tag = ContestTag.objects.get(name=FAIRPLAY_TAG)
        self.assertRegex(tag.color, r'^#[0-9a-fA-F]{6}$')
        self.assertTrue(is_fairplay_exam(Contest.objects.get(pk=contest.pk)))
        self.assertEqual(list(fairplay_exams().values_list('key', flat=True)), ['fptag'])
        # Prefetched tags are used as they are.
        prefetched = Contest.objects.prefetch_related('tags').get(pk=contest.pk)
        with self.assertNumQueries(0):
            self.assertTrue(is_fairplay_exam(prefetched))
        set_fairplay_exam(contest, False)
        self.assertFalse(is_fairplay_exam(contest))
        self.assertFalse(is_fairplay_exam(Contest.objects.get(pk=contest.pk)))
        self.assertEqual(ContestTag.objects.filter(name=FAIRPLAY_TAG).count(), 1)
        self.assertFalse(fairplay_exams().exists())

    def test_other_tags_untouched(self):
        ContestTag.objects.create(name='exam', color='#123456')
        contest = create_contest('fptags', tags=('exam',))
        set_fairplay_exam(contest, True)
        set_fairplay_exam(contest, False)
        self.assertEqual(list(contest.tags.values_list('name', flat=True)), ['exam'])


class FairPlayCheckTestCase(TestCase):
    def _contest(self, **kwargs):
        now = timezone.now()
        defaults = dict(start_time=now, end_time=now + timedelta(hours=1), is_organization_private=True)
        defaults.update(kwargs)
        return create_contest('fpclean', fairplay_required=True, **defaults)

    def test_seb_and_fairplay_not_both(self):
        create_organization(name='fpcheckorg')
        contest = self._contest(seb_required=True, organizations=('fpcheckorg',))
        self.assertIn('not both', fairplay_seb_error(contest))
        with self.assertRaises(ValidationError):
            fairplay_check_contest(contest)

    def test_classic_mode_needs_a_student_list(self):
        for kwargs in (dict(is_organization_private=True),
                       dict(is_organization_private=False, is_private=True)):
            with self.subTest(**kwargs):
                contest = self._contest(**kwargs)
                self.assertIsNone(fairplay_seb_error(contest))
                fairplay_check_contest(contest)
                contest.delete()
        contest = self._contest(is_organization_private=False, is_private=False)
        self.assertIn('student list', fairplay_seb_error(contest))
        with self.assertRaises(ValidationError):
            fairplay_check_contest(contest)

    def test_native_mode_allows_a_public_exam(self):
        from django.test import override_settings
        contest = self._contest(is_organization_private=False, is_private=False)
        with override_settings(FAIRPLAY_NATIVE_BROWSER_ENABLED=True):
            self.assertIsNone(fairplay_seb_error(contest))
            fairplay_check_contest(contest)

    def test_not_a_fairplay_exam_is_not_checked(self):
        contest = create_contest('fpplain', is_organization_private=False)
        fairplay_check_contest(contest)


class FairPlayLiveLinkRetiredTestCase(TestCase):
    def test_no_live_link_helpers(self):
        self.assertFalse(hasattr(fairplay_utils, 'fairplay_live_url'))
        self.assertFalse(hasattr(fairplay_utils, 'sign_fairplay_live'))


class FairPlayHidingRuleTestCase(FairPlayDataMixin, TestCase):
    """fairplay_hidden_for per object and role; the queryset form agrees with the object form."""

    def assertRule(self, user, hidden):
        with self.subTest(user=str(user)):
            self.assertEqual(fairplay_hidden_for(user, self.exam), hidden)
            self.assertEqual(fairplay_hidden_for(user, self.code), hidden)
            cp = ContestProblem.objects.get(contest=self.exam, problem=self.code)
            self.assertEqual(fairplay_hidden_for(user, cp), hidden)
            self.assertEqual(Contest.objects.filter(fairplay_hidden_q(user, Contest), pk=self.exam.pk).exists(),
                             hidden)
            self.assertEqual(Problem.objects.filter(fairplay_hidden_q(user, Problem), pk=self.code.pk).exists(),
                             hidden)

    def test_students_and_anonymous_are_denied(self):
        for user in (self.student, self.classmate, self.outsider, AnonymousUser()):
            self.assertRule(user, True)

    def test_staff_see_everything(self):
        for user in (self.teacher, self.tester, self.course_admin, self.staff):
            self.assertRule(user, False)

    def test_every_kind_of_staff_is_exempt_and_students_stay_locked(self):
        from judge.models.notification import ensure_instructors_group
        ta = create_user(username='fp_ta')  # a class admin: the TA of the course's class
        self.klass.admins.add(ta.profile)
        course_instructor = create_user(username='fp_course_instructor')
        self.org.instructors.add(course_instructor.profile)
        curator = create_user(username='fp_curator')
        self.exam.curators.add(curator.profile)
        spectator = create_user(username='fp_spectator')
        self.exam.spectators.add(spectator.profile)
        site_instructor = create_user(username='fp_site_instructor')
        site_instructor.groups.add(ensure_instructors_group())
        django_staff = create_user(username='fp_django_staff', is_staff=True)
        superuser = create_user(username='fp_superuser', is_superuser=True, is_staff=True)
        for user in (ta, course_instructor, curator, spectator, site_instructor, django_staff, superuser):
            self.assertRule(user, False)
            self.assertTrue(fairplay_exempt(user, self.reload_exam()), user.username)
        for user in (self.student, self.classmate, self.outsider):
            self.assertRule(user, True)
            self.assertFalse(fairplay_exempt(user, self.reload_exam()), user.username)

    def test_a_ta_of_another_course_is_not_staff_of_this_exam(self):
        other = create_organization(name='fpotherorg')
        stranger = create_user(username='fp_other_ta')
        other.admins.add(stranger.profile)
        self.assertRule(stranger, True)

    def test_audience_is_whoever_dku_oj_lets_join(self):
        exam = self.reload_exam()
        self.assertTrue(exam.audience_allows(self.student.profile))
        self.assertFalse(exam.audience_allows(self.outsider.profile))  # in the course, not in the class
        exam.banned_users.add(self.classmate.profile)
        self.assertFalse(exam.audience_allows(self.classmate.profile))
        Contest.objects.filter(pk=exam.pk).update(is_organization_private=False, is_private=False)
        self.assertTrue(self.reload_exam().audience_allows(self.outsider.profile))  # a public exam: anyone

    def test_problem_author_keeps_the_problem(self):
        author = create_user(username='fp_problem_author')
        self.code.authors.add(author.profile)
        problem = Problem.objects.get(pk=self.code.pk)
        self.assertFalse(fairplay_hidden_for(author, problem))
        self.assertTrue(fairplay_hidden_for(author, self.reload_exam()))

    def test_off_hides_nothing(self):
        set_fairplay(self.exam, False)
        exam = self.reload_exam()
        self.assertFalse(fairplay_hidden_for(self.student, exam))
        self.assertFalse(fairplay_hidden_for(self.student, Problem.objects.get(pk=self.code.pk)))

    def test_shown_again_only_after_end_and_finalize(self):
        now = timezone.now()
        Contest.objects.filter(pk=self.exam.pk).update(end_time=now - timedelta(minutes=1))
        self.assertTrue(fairplay_hidden_for(self.student, self.reload_exam()))
        Contest.objects.filter(pk=self.exam.pk).update(results_finalized=True)
        record_added_minutes(self.exam, 0)
        self.assertFalse(fairplay_hidden_for(self.student, self.reload_exam()))
        self.assertFalse(fairplay_hidden_for(self.student, Problem.objects.get(pk=self.code.pk)))
        Contest.objects.filter(pk=self.exam.pk).update(end_time=now + timedelta(hours=1))
        self.assertTrue(fairplay_hidden_for(self.student, self.reload_exam()))

    def test_reused_problem_stays_hidden_until_the_other_exam_ends(self):
        now = timezone.now()
        Contest.objects.filter(pk=self.exam.pk).update(end_time=now - timedelta(minutes=1), results_finalized=True)
        record_added_minutes(self.exam, 0)
        other = create_contest('fpother', start_time=now - timedelta(hours=1), end_time=now + timedelta(hours=1),
                               is_organization_private=True, organizations=('fporg',), fairplay_required=True)
        create_contest_problem(contest=other, problem=self.code, points=5, order=1)
        self.assertFalse(fairplay_hidden_for(self.student, self.reload_exam()))
        self.assertTrue(fairplay_hidden_for(self.student, Problem.objects.get(pk=self.code.pk)))
        # The first exam's editor is not staff of the second one.
        self.assertTrue(fairplay_hidden_for(self.teacher, Problem.objects.get(pk=self.code.pk)))

    def test_submission_and_attachment(self):
        submission = Submission.objects.create(user=self.student.profile, problem=self.mcq,
                                               language=self.code.allowed_languages.first(),
                                               contest_object=self.exam)
        self.assertTrue(fairplay_hidden_for(self.student, submission))
        self.assertFalse(fairplay_hidden_for(self.teacher, submission))
        self.assertTrue(Submission.objects.filter(fairplay_hidden_q(self.student, Submission),
                                                  pk=submission.pk).exists())
        self.assertFalse(Submission.objects.filter(fairplay_hidden_q(self.teacher, Submission),
                                                   pk=submission.pk).exists())
        attachment = EditorAttachment(original_name='cell.png', size=3)
        attachment.file.save('cell.png', ContentFile(b'png'), save=False)
        attachment.save()
        Problem.objects.filter(pk=self.mcq.pk).update(
            description='See ![cell](/widgets/files/%s/)' % attachment.uuid)
        self.assertTrue(fairplay_hidden_for(self.student, attachment))
        self.assertFalse(fairplay_hidden_for(self.teacher, attachment))
        self.assertFalse(fairplay_hidden_for(self.student, EditorAttachment.objects.create(
            original_name='other.png', file='x/other.png')))


class FairPlayPackageTestCase(FairPlayDataMixin, TestCase):
    def test_every_question_type_serializes_without_answer_keys(self):
        package, _assets = build_package(self.exam)
        self.assertEqual([q['type'] for q in package['questions']],
                         ['choice', 'fill_blank', 'short_answer', 'file_upload', 'code'])
        body = json.dumps(package, ensure_ascii=False)
        for secret in SECRETS:
            self.assertNotIn(secret, body)
        self.assertEqual(set(walk_keys(package)) & FORBIDDEN_KEYS, set())
        # Each type alone as well.
        for cp in self.exam.contest_problems.select_related('problem'):
            single = create_contest('fpsingle%s' % cp.problem.code[2:], is_organization_private=True,
                                    fairplay_required=True)
            create_contest_problem(contest=single, problem=cp.problem, points=cp.points, order=1)
            with self.subTest(problem=cp.problem.code):
                only, _ = build_package(single)
                text = json.dumps(only, ensure_ascii=False)
                self.assertEqual(set(walk_keys(only)) & FORBIDDEN_KEYS, set())
                for secret in SECRETS:
                    self.assertNotIn(secret, text)

    def test_question_shapes(self):
        package, _ = build_package(self.exam)
        self.assertEqual(package['key'], 'fpexam')
        self.assertRegex(package['package_version'], r'^[0-9a-f]{16}$')
        self.assertEqual(package['total_points'], 66)
        choice, blank, short, upload, code = package['questions']
        self.assertEqual(choice['id'], 'p%06d' % self.mcq.id)
        self.assertEqual(choice['points'], 10)
        self.assertTrue(choice['partial'])
        self.assertEqual(choice['choice'], {
            'multiple': False, 'true_false': False, 'shuffle': True,
            'options': [{'id': self.wrong.id, 'text_md': 'Array'}, {'id': self.right.id, 'text_md': 'Hash table'}],
        })
        self.assertEqual(blank['fill_blank'], {'blanks': [{'index': 0, 'points': 3.0}, {'index': 1, 'points': 3.0}],
                                               'case_sensitive': False})
        self.assertEqual(short['short_answer'], {})
        self.assertEqual(upload['file_upload'], {'allowed_extensions': ['pdf', 'png'], 'max_file_size': 1024,
                                                 'instructions_md': ''})
        # Only the languages with a harness (the problem allows CPP14 too, but grades with a PY3 harness).
        self.assertEqual(code['code']['languages'], [{'key': 'PY3', 'name': 'Python 3'}])
        self.assertEqual(code['code']['templates']['PY3'], 'def reverse(head):\n    pass\n')
        self.assertEqual(code['code']['samples'], [{'input': '1 2 3\n', 'output': '3 2 1\n'}])
        self.assertEqual(code['code']['time_limit_s'], 1.0)
        self.assertEqual(code['code']['memory_limit_kb'], 65536)
        self.assertEqual(code['statement_md'], self.code.description)
        self.assertIsNone(code['statement_html'])
        self.assertEqual(code['title'], 'Reverse a list')

    def test_display_name_and_full_markup(self):
        ContestProblem.objects.filter(contest=self.exam, problem=self.short).update(display_name='Q3 essay')
        Problem.objects.filter(pk=self.short.pk).update(is_full_markup=True,
                                                        description='<b>Bold</b><script>alert(1)</script>')
        package, _ = build_package(self.exam)
        short = package['questions'][2]
        self.assertEqual(short['title'], 'Q3 essay')
        self.assertIsNone(short['statement_md'])
        self.assertIn('<b>Bold</b>', short['statement_html'])
        self.assertNotIn('<script', short['statement_html'])

    def test_version_changes_with_content(self):
        first, _ = build_package(self.exam)
        again, _ = build_package(self.exam)
        self.assertEqual(first['package_version'], again['package_version'])
        Problem.objects.filter(pk=self.mcq.pk).update(description='Changed')
        changed, _ = build_package(self.exam)
        self.assertNotEqual(first['package_version'], changed['package_version'])
        ContestProblem.objects.filter(contest=self.exam, problem=self.mcq).update(points=11)
        self.assertNotEqual(changed['package_version'], build_package(self.exam)[0]['package_version'])

    def test_images_become_assets(self):
        attachment = EditorAttachment(original_name='Cell.PNG', size=9)
        attachment.file.save('cell.png', ContentFile(b'png-bytes'), save=False)
        attachment.save()
        Problem.objects.filter(pk=self.mcq.pk).update(
            description='Look: ![cell](https://oj.example.edu/widgets/files/%s/) and <img src="/widgets/files/%s/">'
                        % (attachment.uuid, attachment.uuid))
        package, assets = build_package(self.exam)
        name = 'att-%s.png' % attachment.uuid.hex
        self.assertEqual(package['questions'][0]['statement_md'],
                         'Look: ![cell](fp-asset:%s) and <img src="fp-asset:%s">' % (name, name))
        self.assertEqual(package['assets'], [{'name': name, 'sha256': hashlib.sha256(b'png-bytes').hexdigest(),
                                              'bytes': 9, 'content_type': 'image/png'}])
        self.assertEqual(assets[name].read(), b'png-bytes')

    def test_samples_parser(self):
        self.assertEqual(extract_samples('**Sample Input 1**\n```\na\n```\n**Sample Output 1**\n```\nb\n```\n'
                                         '### 样例输入\n```\nc\n```\n### 样例输出\n```\nd\n```\n'
                                         '## Notes\n```\nnot a sample\n```'),
                         [{'input': 'a\n', 'output': 'b\n'}, {'input': 'c\n', 'output': 'd\n'}])

    def test_exam_entry(self):
        entry = build_exam_entry(self.exam)
        self.assertEqual(entry['key'], 'fpexam')
        self.assertEqual(entry['course'], {'id': self.org.id, 'name': 'fporg'})
        self.assertEqual(entry['classes'], [{'id': self.klass.id, 'name': 'FP Section A'}])
        self.assertEqual(entry['time_limit_seconds'], 5400)
        self.assertTrue(entry['results_hidden'])
        self.assertFalse(entry['removed'])
        # The exam's settings are FairPlay's: the exam list carries none.
        self.assertNotIn('settings', entry)
        # No email: FairPlay never uses it.
        self.assertEqual(entry['editors'], [{'username': 'fp_teacher', 'first_name': 'Shuai', 'last_name': 'Dong'}])
        self.assertEqual([r['username'] for r in entry['roster']], ['fp_classmate', 'fp_student'])
        self.assertEqual(entry['roster'][1], {'username': 'fp_student', 'first_name': 'Avazbek',
                                              'last_name': 'Khamidov', 'extra_time_seconds': 0})
        self.assertNotIn('@example.edu', json.dumps(entry))
        self.assertTrue(entry['start_time'].endswith('Z'))

    def test_roster_scopes(self):
        # Course-wide (no classes): every course member except staff.
        self.exam.classes.clear()
        self.assertEqual(sorted(p.user.username for p in exam_roster(self.exam)),
                         ['fp_classmate', 'fp_outsider', 'fp_student'])
        # Course + selected students.
        Contest.objects.filter(pk=self.exam.pk).update(is_private=True)
        exam = self.reload_exam()
        exam.private_contestants.add(self.student.profile)
        self.assertEqual([p.user.username for p in exam_roster(exam)], ['fp_student'])
        exam.banned_users.add(self.student.profile)
        self.assertEqual(list(exam_roster(exam)), [])

    def test_roster_leaves_out_teachers_like_the_class_roster(self):
        from judge.utils.fairplay_teaching import class_roster
        instructor = create_user(username='fp_instructor')
        class_admin = create_user(username='fp_class_admin')
        site_staff = create_user(username='fp_site_staff', is_staff=True)
        other_editor = create_user(username='fp_other_editor')
        for user in (instructor, class_admin, site_staff, other_editor, self.course_admin, self.tester):
            user.profile.organizations.add(self.org)
            self.klass.members.add(user.profile)
        self.org.instructors.add(instructor.profile)
        self.klass.admins.add(class_admin.profile)
        create_contest('fpanother', is_organization_private=True, organizations=('fporg',),
                       authors=('fp_other_editor',), fairplay_required=True)
        expected = ['fp_classmate', 'fp_student']
        self.assertEqual([p.user.username for p in exam_roster(self.exam)], expected)
        self.assertEqual([p.user.username for p in class_roster(self.klass)], expected)
        self.exam.classes.clear()
        self.assertEqual([p.user.username for p in exam_roster(self.reload_exam())],
                         ['fp_classmate', 'fp_outsider', 'fp_student'])

    def test_media_only_from_the_editor_upload_directory(self):
        from django.core.files.base import ContentFile
        from django.core.files.storage import default_storage
        allowed = default_storage.save('martor/fp-test-image.png', ContentFile(b'png'))
        other = default_storage.save('fp-private/secret.png', ContentFile(b'secret'))
        self.addCleanup(default_storage.delete, allowed)
        self.addCleanup(default_storage.delete, other)
        Problem.objects.filter(pk=self.mcq.pk).update(description='![a](/media/%s) ![b](/media/%s) '
                                                      '![c](/media/martor/../%s)' % (allowed, other, other))
        package, assets = build_package(self.reload_exam())
        self.assertEqual(len(assets), 1)
        mcq = next(q for q in package['questions'] if q['type'] == 'choice')
        self.assertIn('fp-asset:', mcq['statement_md'])
        self.assertIn('/media/%s' % other, mcq['statement_md'])

    def test_invisible_assignment_not_listed(self):
        from judge.utils.fairplay_package import tracked_exams
        self.assertIn(self.exam, tracked_exams())
        Contest.objects.filter(pk=self.exam.pk).update(is_visible=False)
        self.assertNotIn(self.exam, tracked_exams())

    def test_unrelated_problems_never_included(self):
        create_problem('fpunrelated')
        package, _ = build_package(self.exam)
        self.assertNotIn('fpunrelated', json.dumps(package))
