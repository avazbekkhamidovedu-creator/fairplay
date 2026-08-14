from django.test import RequestFactory, SimpleTestCase, TestCase, override_settings
from django.urls import reverse

from judge.models import Class, OrganizationRequest, InstructorVerificationRequest
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


class InstructorJoinRequestActionsTest(CommonDataMixin, TestCase):
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

    def test_cross_class_material_id_not_downloadable(self):
        """Student in class A must not fetch class B's file by swapping material id."""
        other = Class.objects.create(
            organization=self.org, name='Other Class Materials', slug='other-class-mat',
        )
        other.members.add(self.member.profile)
        from django.core.files.base import ContentFile
        foreign = ClassMaterial.objects.create(
            class_obj=other, name='other-secret.txt', is_folder=False,
            uploaded_by=self.admin.profile,
        )
        foreign.file.save('other-secret.txt', ContentFile(b'other-bytes'), save=True)
        # URL claims self.klass but mid belongs to other class → 404
        url = reverse(
            'class_material_download',
            args=[self.org.id, self.org.slug, self.klass.id, self.klass.slug, foreign.id],
        )
        self.client.force_login(self.member)
        response = self.client.get(url)
        self.assertEqual(response.status_code, 404)

    def test_delete_folder_removes_nested_oss_files(self):
        from django.core.files.base import ContentFile
        from judge.models import StoredFileRecord

        folder = ClassMaterial.objects.create(
            class_obj=self.klass, name='Notes', is_folder=True, uploaded_by=self.admin.profile,
        )
        nested = ClassMaterial.objects.create(
            class_obj=self.klass, parent=folder, name='nested.txt', is_folder=False,
            uploaded_by=self.admin.profile,
        )
        nested.file.save('nested.txt', ContentFile(b'nested-bytes'), save=True)
        path = nested.file.name
        self.assertTrue(path)
        self.assertTrue(StoredFileRecord.objects.filter(path=path).exists())

        self.client.force_login(self.admin)
        response = self.client.post(
            reverse(
                'class_material_action',
                args=[self.org.id, self.org.slug, self.klass.id, self.klass.slug],
            ),
            {'action': 'delete', 'material_ids': [str(folder.id)]},
        )
        self.assertEqual(response.status_code, 302)
        self.assertFalse(ClassMaterial.objects.filter(id=folder.id).exists())
        self.assertFalse(ClassMaterial.objects.filter(id=nested.id).exists())
        record = StoredFileRecord.objects.filter(path=path).first()
        self.assertIsNotNone(record)
        self.assertEqual(record.status, StoredFileRecord.STATUS_DELETED)
        self.assertTrue(path.startswith('class_materials/'))

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


class ScopedInstructorVerificationTest(CommonDataMixin, TestCase):
    """Instructor apply is per course/class; approval grants class admin only."""

    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.org = cls.organizations['open']
        cls.klass = Class.objects.create(
            organization=cls.org,
            name='CS101 Spring',
            slug='cs101-spring',
            term_session='Spring',
            academic_year='2026',
        )
        cls.other = Class.objects.create(
            organization=cls.org,
            name='CS101 Fall',
            slug='cs101-fall',
            term_session='Fall',
            academic_year='2026',
        )
        cls.applicant = create_user(username='instructor_applicant')

    def test_submit_requires_course_and_class(self):
        self.client.force_login(self.applicant)
        response = self.client.post(reverse('organization_instructor_verification'), {
            'reason': 'I instruct this session',
        })
        self.assertEqual(response.status_code, 302)
        self.assertFalse(InstructorVerificationRequest.objects.filter(user=self.applicant.profile).exists())

    def test_submit_and_approve_grants_only_that_class(self):
        from django.contrib.auth.models import Group
        from judge.models.notification import INSTRUCTORS_GROUP

        self.client.force_login(self.applicant)
        response = self.client.post(reverse('organization_instructor_verification'), {
            'organization': self.org.id,
            'request_class': self.klass.id,
            'reason': 'I instruct CS101 Spring',
        })
        self.assertEqual(response.status_code, 302)
        req = InstructorVerificationRequest.objects.get(user=self.applicant.profile, state='P')
        self.assertEqual(req.request_class_id, self.klass.id)
        self.assertEqual(req.organization_id, self.org.id)

        self.client.force_login(self.users['superuser'])
        response = self.client.post(
            reverse('organization_instructor_requests'),
            {'action': 'approve', 'request_ids': [str(req.id)]},
        )
        self.assertEqual(response.status_code, 302)
        req.refresh_from_db()
        self.assertEqual(req.state, 'A')
        self.assertTrue(self.klass.admins.filter(id=self.applicant.profile.id).exists())
        self.assertFalse(self.other.admins.filter(id=self.applicant.profile.id).exists())
        self.assertTrue(self.applicant.profile.organizations.filter(id=self.org.id).exists())
        # Must not become course administrator.
        self.assertFalse(self.org.admins.filter(id=self.applicant.profile.id).exists())
        self.applicant.refresh_from_db()
        self.assertTrue(self.applicant.is_staff)
        req.refresh_from_db()
        self.assertTrue(req.granted_staff)
        self.assertTrue(req.granted_class_admin)
        instructors = Group.objects.filter(name=INSTRUCTORS_GROUP).first()
        if instructors is not None:
            self.assertFalse(instructors.user_set.filter(id=self.applicant.id).exists())

        # Class instructor can open their class, not the sibling class.
        self.client.force_login(self.applicant)
        ok = self.client.get(self.klass.get_absolute_url())
        self.assertEqual(ok.status_code, 200)
        blocked = self.client.get(self.other.get_absolute_url())
        self.assertEqual(blocked.status_code, 403)
        # Cannot open course edit (course administrator only).
        edit = self.client.get(reverse('edit_organization', args=[self.org.id, self.org.slug]))
        self.assertIn(edit.status_code, (403, 302))

    def test_revoke_removes_class_admin(self):
        req = InstructorVerificationRequest.objects.create(
            user=self.applicant.profile,
            organization=self.org,
            request_class=self.klass,
            reason='instruct',
            state='A',
            granted_class_admin=True,
            granted_staff=True,
        )
        self.klass.admins.add(self.applicant.profile)
        self.org.members.add(self.applicant.profile)
        self.applicant.is_staff = True
        self.applicant.save(update_fields=['is_staff'])

        self.client.force_login(self.users['superuser'])
        response = self.client.post(
            reverse('organization_instructor_requests') + '?status=A',
            {'action': 'reject', 'request_ids': [str(req.id)]},
        )
        self.assertEqual(response.status_code, 302)
        self.assertFalse(self.klass.admins.filter(id=self.applicant.profile.id).exists())
        # Course membership kept after revoke.
        self.assertTrue(self.applicant.profile.organizations.filter(id=self.org.id).exists())
        self.applicant.refresh_from_db()
        self.assertFalse(self.applicant.is_staff)

    def test_revoke_preserves_unrelated_staff(self):
        """Staff granted outside instructor approval must survive revoke."""
        self.applicant.is_staff = True
        self.applicant.save(update_fields=['is_staff'])
        req = InstructorVerificationRequest.objects.create(
            user=self.applicant.profile,
            organization=self.org,
            request_class=self.klass,
            reason='already staff',
            state='A',
            granted_class_admin=True,
            granted_staff=False,
        )
        self.klass.admins.add(self.applicant.profile)

        self.client.force_login(self.users['superuser'])
        response = self.client.post(
            reverse('organization_instructor_requests') + '?status=A',
            {'action': 'reject', 'request_ids': [str(req.id)]},
        )
        self.assertEqual(response.status_code, 302)
        self.assertFalse(self.klass.admins.filter(id=self.applicant.profile.id).exists())
        self.applicant.refresh_from_db()
        self.assertTrue(self.applicant.is_staff)
