from django.core.exceptions import ValidationError
from django.test import TestCase
from django.urls import reverse

from judge.models.tests.util import create_user
from judge.utils.fairplay_audience import audience_descriptor
from judge.utils.fairplay_editor import apply_fairplay_editor, parse_fairplay_payload
from judge.views.tests.fairplay_base import FAIRPLAY_ON
from judge.views.tests.test_fairplay_editor import FairPlayEditorMixin


@FAIRPLAY_ON
class FairPlayAudienceEditorTestCase(FairPlayEditorMixin, TestCase):
    def clear_confirmation(self):
        self.exam.tags.remove(*self.exam.tags.filter(name__startswith='fp-aud-'))

    def can_launch(self, user):
        from django.test import Client
        client = Client()
        client.force_login(user)
        return client.get(reverse('contest_fairplay_launch', args=[self.exam.key])).status_code

    def test_form_has_no_duplicate_audience_question_or_js_payload(self):
        response = self.client.get(reverse('contest_edit', args=[self.exam.key]))
        for removed in ('FairPlay audience intent', 'fairplay_audience_mode', 'fairplay_audience_confirm'):
            self.assertNotContains(response, removed)
        self.assertContains(response, 'id="id_fairplay_required"')

    def test_existing_scope_is_confirmed_by_normal_authorized_save(self):
        self.clear_confirmation()
        self.assertEqual(self.can_launch(self.student), 404)
        response = self.save()
        self.assertEqual(response.status_code, 200, response.content)
        descriptor = audience_descriptor(self.reload_exam())
        self.assertTrue(descriptor['confirmed'])
        self.assertEqual(descriptor['mode'], 'classes')
        self.assertEqual(self.can_launch(self.student), 200)
        self.assertEqual(self.can_launch(self.outsider), 404)

    def test_mode_comes_from_saved_relations_not_submitted_choice(self):
        self.clear_confirmation()
        response = self.save(fairplay_audience_mode='organizations')
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(audience_descriptor(self.reload_exam())['mode'], 'classes')
        self.assertEqual(self.can_launch(self.outsider), 404)

    def test_explicit_authorized_class_removal_reconciles_organization_scope(self):
        response = self.save(classes=[])
        self.assertEqual(response.status_code, 200, response.content)
        descriptor = audience_descriptor(self.reload_exam())
        self.assertEqual(descriptor['mode'], 'organizations')
        self.assertTrue(descriptor['confirmed'])
        self.assertEqual(self.can_launch(self.outsider), 200)
        self.outsider.profile.organizations.clear()
        self.assertEqual(self.can_launch(self.outsider), 404)

    def test_out_of_band_deletion_and_settings_only_apply_remain_blocked(self):
        self.exam.classes.clear()
        self.assertFalse(audience_descriptor(self.reload_exam())['confirmed'])
        exam = self.reload_exam()
        plan = parse_fairplay_payload(exam, {'fairplay_required': True,
                                            'fairplay_settings': {'open_book': True}}, set())
        apply_fairplay_editor(exam, plan)
        self.assertFalse(audience_descriptor(self.reload_exam())['confirmed'])
        self.assertEqual(self.can_launch(self.outsider), 404)

    def test_private_subset_intersects_class_membership(self):
        response = self.save(vis_mode=Ellipsis, is_visible=True, is_private=True, is_organization_private=True,
                             private_contestants=[self.student.profile.pk, self.outsider.profile.pk])
        self.assertEqual(response.status_code, 200, response.content)
        descriptor = audience_descriptor(self.reload_exam())
        self.assertTrue(descriptor['confirmed'])
        self.assertTrue(descriptor['selected_only'])
        self.assertEqual(descriptor['mode'], 'classes')
        self.assertEqual(self.can_launch(self.student), 200)
        self.assertEqual(self.can_launch(self.classmate), 404)
        self.assertEqual(self.can_launch(self.outsider), 404)

    def test_selected_only_scope_is_derived_from_private_assignment(self):
        response = self.save(vis_mode='private', private_contestants=[self.student.profile.pk])
        self.assertEqual(response.status_code, 200, response.content)
        descriptor = audience_descriptor(self.reload_exam())
        self.assertTrue(descriptor['confirmed'])
        self.assertEqual(descriptor['mode'], 'selected')
        self.assertEqual(self.can_launch(self.student), 200)
        self.assertEqual(self.can_launch(self.classmate), 404)

    def test_invalid_empty_scope_rolls_back_scope_and_markers(self):
        before = audience_descriptor(self.reload_exam())
        for overrides in ({'classes': [], 'organizations': []},
                          {'vis_mode': 'private', 'private_contestants': []}):
            response = self.save(**overrides)
            self.assertEqual(response.status_code, 400, response.content)
            self.assertEqual(audience_descriptor(self.reload_exam()), before)
        self.assertEqual(self.can_launch(self.student), 200)

    def test_restricted_editor_cannot_reconfirm_via_scope_or_legacy_flag(self):
        self.clear_confirmation()
        curator = create_user(username='audience_curator',
                              user_permissions=('edit_own_contest', 'contest_seb_requirement'))
        self.exam.curators.add(curator.profile)
        self.client.force_login(curator)
        response = self.save()
        self.assertEqual(response.status_code, 200, response.content)
        self.assertFalse(audience_descriptor(self.reload_exam())['confirmed'])
        response = self.save(fairplay_audience_confirm=True, fairplay_audience_mode='classes')
        self.assertEqual(response.status_code, 400, response.content)
        self.assertFalse(audience_descriptor(self.reload_exam())['confirmed'])
        with self.assertRaises(ValidationError):
            parse_fairplay_payload(self.reload_exam(), {'fairplay_audience_confirm': True,
                                   'fairplay_audience_mode': 'classes'}, {'classes'})
