from django.test import RequestFactory, SimpleTestCase, TestCase, override_settings
from django.urls import reverse

from judge.models import Class, OrganizationRequest
from judge.models.tests.util import CommonDataMixin, create_user
from judge.views.organization import safe_redirect_url


@override_settings(ALLOWED_HOSTS=['testserver'])
class SafeRedirectUrlTest(SimpleTestCase):
    def setUp(self):
        self.factory = RequestFactory()

    def test_allows_same_host_relative_path(self):
        request = self.factory.get('/organizations/')
        self.assertEqual(
            safe_redirect_url(request, '/organization/1-open/', '/fallback/'),
            '/organization/1-open/',
        )

    def test_rejects_open_redirect(self):
        request = self.factory.get('/organizations/')
        self.assertEqual(
            safe_redirect_url(request, 'https://evil.example/phish', '/fallback/'),
            '/fallback/',
        )

    def test_empty_candidate_uses_fallback(self):
        request = self.factory.get('/organizations/')
        self.assertEqual(safe_redirect_url(request, '', '/fallback/'), '/fallback/')
        self.assertEqual(safe_redirect_url(request, None, '/fallback/'), '/fallback/')


class TeacherJoinRequestActionsTest(CommonDataMixin, TestCase):
    """Approve / reopen must respect grants_organization (class-only revoke)."""

    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.org = cls.organizations['open']
        cls.org.is_open = False
        cls.org.save()
        cls.student = create_user(username='join_student')
        cls.student_profile = cls.student.profile
        cls.klass = Class.objects.create(
            organization=cls.org,
            name='Join Class A',
            slug='join-class-a',
        )
        cls.klass.admins.add(cls.users['staff_organization_admin'].profile)

    def _post_action(self, action, request_ids, user='staff_organization_admin'):
        self.client.force_login(self.users[user])
        return self.client.post(
            reverse('organization_join_requests'),
            {'action': action, 'request_ids': [str(i) for i in request_ids]},
        )

    def test_reopen_class_only_request_keeps_course_membership(self):
        self.student_profile.organizations.add(self.org)
        req = OrganizationRequest.objects.create(
            user=self.student_profile,
            organization=self.org,
            request_class=self.klass,
            state='A',
            reason='already in course',
            grants_organization=False,
        )
        self.student_profile.classes.add(self.klass)

        response = self._post_action('reopen', [req.id])
        self.assertEqual(response.status_code, 302)

        req.refresh_from_db()
        self.assertEqual(req.state, 'P')
        self.assertTrue(self.student_profile.organizations.filter(id=self.org.id).exists())
        self.assertFalse(self.student_profile.classes.filter(id=self.klass.id).exists())

    def test_reopen_course_request_removes_course_membership(self):
        req = OrganizationRequest.objects.create(
            user=self.student_profile,
            organization=self.org,
            request_class=self.klass,
            state='A',
            reason='join course',
            grants_organization=True,
        )
        self.student_profile.organizations.add(self.org)
        self.student_profile.classes.add(self.klass)

        response = self._post_action('reopen', [req.id])
        self.assertEqual(response.status_code, 302)

        req.refresh_from_db()
        self.assertEqual(req.state, 'P')
        self.assertFalse(self.student_profile.organizations.filter(id=self.org.id).exists())
        self.assertFalse(self.student_profile.classes.filter(id=self.klass.id).exists())

    def test_approve_pending_request(self):
        req = OrganizationRequest.objects.create(
            user=self.student_profile,
            organization=self.org,
            request_class=None,
            state='P',
            reason='please',
            grants_organization=True,
        )
        response = self._post_action('approve', [req.id])
        self.assertEqual(response.status_code, 302)
        req.refresh_from_db()
        self.assertEqual(req.state, 'A')
        self.assertTrue(self.student_profile.organizations.filter(id=self.org.id).exists())

    def test_approve_class_only_adds_course_membership(self):
        """Class approve must not leave the user in-class without course."""
        req = OrganizationRequest.objects.create(
            user=self.student_profile,
            organization=self.org,
            request_class=self.klass,
            state='P',
            reason='class only',
            grants_organization=False,
        )
        self.assertFalse(self.student_profile.organizations.filter(id=self.org.id).exists())

        response = self._post_action('approve', [req.id])
        self.assertEqual(response.status_code, 302)
        req.refresh_from_db()
        self.assertEqual(req.state, 'A')
        self.assertTrue(self.student_profile.organizations.filter(id=self.org.id).exists())
        self.assertTrue(self.student_profile.classes.filter(id=self.klass.id).exists())

    def test_reopen_course_request_clears_all_classes_under_course(self):
        other = Class.objects.create(
            organization=self.org,
            name='Join Class B',
            slug='join-class-b',
        )
        req = OrganizationRequest.objects.create(
            user=self.student_profile,
            organization=self.org,
            request_class=self.klass,
            state='A',
            reason='join course',
            grants_organization=True,
        )
        self.student_profile.organizations.add(self.org)
        self.student_profile.classes.add(self.klass, other)

        response = self._post_action('reopen', [req.id])
        self.assertEqual(response.status_code, 302)
        self.assertFalse(self.student_profile.organizations.filter(id=self.org.id).exists())
        self.assertFalse(self.student_profile.classes.filter(id=self.klass.id).exists())
        self.assertFalse(self.student_profile.classes.filter(id=other.id).exists())


class ClassAccessAndMaterialsTest(CommonDataMixin, TestCase):
    """Class page gate + material download require membership/admin."""

    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.org = cls.organizations['open']
        cls.org.is_open = True
        cls.org.save()
        cls.klass = Class.objects.create(
            organization=cls.org,
            name='Access Class',
            slug='access-class',
        )
        cls.admin = cls.users['staff_organization_admin']
        cls.klass.admins.add(cls.admin.profile)
        cls.member = create_user(username='class_member')
        cls.outsider = create_user(username='class_outsider')
        cls.member.profile.organizations.add(cls.org)
        cls.member.profile.classes.add(cls.klass)
        from judge.models import ClassMaterial
        from django.core.files.base import ContentFile
        cls.material = ClassMaterial.objects.create(
            class_obj=cls.klass,
            name='secret.txt',
            is_folder=False,
            uploaded_by=cls.admin.profile,
        )
        cls.material.file.save('secret.txt', ContentFile(b'secret-bytes'), save=True)

    def _class_url(self):
        return reverse('class_home', args=[self.org.id, self.org.slug, self.klass.id, self.klass.slug])

    def _download_url(self):
        return reverse(
            'class_material_download',
            args=[self.org.id, self.org.slug, self.klass.id, self.klass.slug, self.material.id],
        )

    def test_anonymous_class_home_forbidden(self):
        response = self.client.get(self._class_url())
        self.assertEqual(response.status_code, 403)

    def test_outsider_class_home_forbidden(self):
        self.client.force_login(self.outsider)
        response = self.client.get(self._class_url())
        self.assertEqual(response.status_code, 403)

    def test_member_class_home_ok(self):
        self.client.force_login(self.member)
        response = self.client.get(self._class_url())
        self.assertEqual(response.status_code, 200)

    def test_admin_class_home_ok(self):
        self.client.force_login(self.admin)
        response = self.client.get(self._class_url())
        self.assertEqual(response.status_code, 200)

    def test_pending_can_open_class_but_not_download(self):
        pending = create_user(username='class_pending')
        pending.profile.organizations.add(self.org)
        OrganizationRequest.objects.create(
            user=pending.profile,
            organization=self.org,
            request_class=self.klass,
            state='P',
            reason='want in',
            grants_organization=False,
        )
        self.client.force_login(pending)
        response = self.client.get(self._class_url())
        self.assertEqual(response.status_code, 200)
        self.assertFalse(response.context['can_view_content'])
        dl = self.client.get(self._download_url())
        self.assertEqual(dl.status_code, 403)

    def test_member_can_download_material(self):
        self.client.force_login(self.member)
        response = self.client.get(self._download_url())
        self.assertEqual(response.status_code, 200)
        self.assertEqual(b''.join(response.streaming_content), b'secret-bytes')

    def test_outsider_download_forbidden(self):
        self.client.force_login(self.outsider)
        response = self.client.get(self._download_url())
        self.assertEqual(response.status_code, 403)


class LeaveCourseClearsClassesTest(CommonDataMixin, TestCase):
    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.org = cls.organizations['open']
        cls.klass = Class.objects.create(
            organization=cls.org,
            name='Leave Class',
            slug='leave-class',
        )
        cls.student = create_user(username='leave_student')
        cls.student.profile.organizations.add(cls.org)
        cls.student.profile.classes.add(cls.klass)

    def test_leave_removes_class_membership(self):
        self.client.force_login(self.student)
        response = self.client.post(
            reverse('leave_organization', args=[self.org.id, self.org.slug]),
        )
        self.assertEqual(response.status_code, 302)
        self.assertFalse(self.student.profile.organizations.filter(id=self.org.id).exists())
        self.assertFalse(self.student.profile.classes.filter(id=self.klass.id).exists())


class KickUserClearsClassesTest(CommonDataMixin, TestCase):
    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.org = cls.organizations['open']
        cls.klass = Class.objects.create(
            organization=cls.org,
            name='Kick Class',
            slug='kick-class',
        )
        cls.admin = cls.users['staff_organization_admin']
        cls.org.admins.add(cls.admin.profile)
        cls.student = create_user(username='kick_student')
        cls.student.profile.organizations.add(cls.org)
        cls.student.profile.classes.add(cls.klass)

    def test_kick_removes_course_and_class(self):
        self.client.force_login(self.admin)
        response = self.client.post(
            reverse('organization_user_kick', args=[self.org.id, self.org.slug]),
            {'user': self.student.profile.id},
        )
        self.assertEqual(response.status_code, 302)
        self.assertFalse(self.student.profile.organizations.filter(id=self.org.id).exists())
        self.assertFalse(self.student.profile.classes.filter(id=self.klass.id).exists())


class SyncClassMembersToOrgTest(CommonDataMixin, TestCase):
    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.org = cls.organizations['open']
        cls.klass = Class.objects.create(
            organization=cls.org,
            name='Sync Class',
            slug='sync-class',
        )

    def test_sync_adds_org_membership(self):
        from judge.views.organization import sync_class_members_to_organization
        user = create_user(username='sync_only_class')
        self.klass.members.add(user.profile)
        self.assertFalse(user.profile.organizations.filter(id=self.org.id).exists())
        sync_class_members_to_organization(self.klass)
        self.assertTrue(user.profile.organizations.filter(id=self.org.id).exists())
