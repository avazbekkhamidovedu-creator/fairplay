from django.test import TestCase
from django.urls import reverse

from judge.models import Class
from judge.models.tests.util import CommonDataMixin, create_organization


class OrganizationSelect2ScopeTest(CommonDataMixin, TestCase):
    """The org select2 (admin problem form) only offers orgs the user administers."""

    @classmethod
    def setUpTestData(self):
        super().setUpTestData()
        # 'open' is admined by staff_organization_admin (from CommonDataMixin); 'other' by nobody.
        self.organizations['other'] = create_organization(name='other', admins=())

    def _ids(self, user):
        if user == 'anonymous':
            self.client.logout()
        else:
            self.client.force_login(self.users[user])
        results = self.client.get(reverse('organization_select2')).json()['results']
        return {r['id'] for r in results}

    def test_superuser_sees_all_orgs(self):
        ids = self._ids('superuser')
        self.assertIn(self.organizations['open'].id, ids)
        self.assertIn(self.organizations['other'].id, ids)

    def test_org_admin_sees_only_administered_org(self):
        self.assertEqual(self._ids('staff_organization_admin'), {self.organizations['open'].id})

    def test_normal_user_sees_no_orgs(self):
        self.assertEqual(self._ids('normal'), set())

    def test_anonymous_sees_no_orgs(self):
        self.assertEqual(self._ids('anonymous'), set())


class ClassSelect2ScopeTest(CommonDataMixin, TestCase):
    """The class select2 is org-first: it returns the chosen org's classes, empty otherwise."""

    @classmethod
    def setUpTestData(self):
        super().setUpTestData()
        self.open_class = Class.objects.create(
            organization=self.organizations['open'], name='Open Class', slug='open-class')

    def _ids(self, user, query=''):
        self.client.force_login(self.users[user])
        results = self.client.get(reverse('class_select2') + query).json()['results']
        return {r['id'] for r in results}

    def test_classes_returned_for_selected_org(self):
        org_id = self.organizations['open'].id
        self.assertEqual(self._ids('staff_organization_admin', '?organizations=%d' % org_id),
                         {self.open_class.id})
        self.assertEqual(self._ids('superuser', '?organizations=%d' % org_id), {self.open_class.id})

    def test_empty_without_org(self):
        # Org-first by design: with no organization chosen the class list is empty.
        self.assertEqual(self._ids('superuser'), set())

    def test_org_admin_does_not_see_other_orgs_classes(self):
        other = create_organization(name='lonely', admins=())
        self.assertEqual(self._ids('staff_organization_admin', '?organizations=%d' % other.id), set())
