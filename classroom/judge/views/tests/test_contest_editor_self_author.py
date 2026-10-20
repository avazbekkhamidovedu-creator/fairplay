"""A teacher who saves their own assignment with themselves missing from Authors (or an empty
list, as a script or a cleared field sends) stays an author instead of locking themselves out
with a 403. Found by the teacher load test (docs/classroom/reports/scaling.md)."""
import json
from datetime import timedelta

from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from judge.models.notification import ensure_instructors_group
from judge.models.tests.util import CommonDataMixin, create_contest, create_user
from judge.views.tests import test_contest_editor


class EditorKeepsSelfAsAuthorTestCase(CommonDataMixin, TestCase):
    _full_payload = test_contest_editor.ContestEditViewTestCase._full_payload

    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.editor = create_user(username='self_author_site_editor', user_permissions=(
            'add_contest', 'edit_own_contest', 'edit_all_contest'))
        cls.teacher = create_user(username='self_author_teacher')
        cls.teacher.groups.add(ensure_instructors_group())
        cls.colleague = create_user(username='self_author_colleague')
        now = timezone.now()
        cls.own = create_contest(key='ownassignment', name='Own', start_time=now + timedelta(days=1),
                                 end_time=now + timedelta(days=2), authors=('self_author_teacher',))
        cls.contest = create_contest(key='editablecontest', name='Editable', start_time=now + timedelta(days=1),
                                     end_time=now + timedelta(days=2), authors=('self_author_teacher',))

    def post_edit(self, user, key, **overrides):
        self.client.force_login(user)
        payload = self._full_payload(key=key, **overrides)
        payload.pop('is_visible', None)
        return self.client.post(reverse('contest_edit', args=[key]), data=json.dumps(payload),
                                content_type='application/json')

    def test_teacher_cannot_drop_themselves(self):
        response = self.post_edit(self.teacher, 'ownassignment', authors=[str(self.colleague.profile.id)])
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(set(self.own.authors.all()), {self.teacher.profile, self.colleague.profile})
        self.assertEqual(self.client.get(reverse('contest_edit', args=['ownassignment'])).status_code, 200)

    def test_empty_author_list_keeps_the_teacher(self):
        response = self.post_edit(self.teacher, 'ownassignment', authors=[])
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(list(self.own.authors.all()), [self.teacher.profile])

    def test_site_editor_can_still_change_the_list(self):
        response = self.post_edit(self.editor, 'editablecontest', authors=[str(self.colleague.profile.id)])
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(list(self.contest.authors.all()), [self.colleague.profile])
