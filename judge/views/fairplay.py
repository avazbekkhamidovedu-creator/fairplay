"""FairPlay pages for professors: the FAIRPLAY course pages and the assignment's FairPlay tab."""
import re
from time import monotonic

from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin
from django.core.exceptions import ValidationError
from django.http import Http404, HttpResponseRedirect, JsonResponse
from django.shortcuts import get_object_or_404, render
from django.urls import reverse
from django.utils import timezone
from django.utils.translation import gettext as _, gettext_lazy
from django.views import View
from django.views.generic import DetailView

from judge.models import Class, Contest, Organization, Profile
from judge.utils.fairplay import FAIRPLAY_CALCULATOR_CHOICES, FAIRPLAY_REVIEW_CHOICES, FAIRPLAY_REVIEW_HELP, \
    fairplay_enabled, is_fairplay_exam
from judge.utils.fairplay_client import FairPlayClient, FairPlayError, FairPlayUnreachable, QUICK_READ_TIMEOUT, \
    cached_class_status
from judge.utils.fairplay_delivery import active_hand_in, clear_hand_in, delivery_warnings, record_added_minutes
from judge.utils.fairplay_editor import clean_settings, editor_settings, pop_unsaved_settings, \
    stash_unsaved_settings
from judge.utils.fairplay_package import exam_roster
from judge.utils.fairplay_teaching import class_roster, course_classes_for, course_fairplay_exams, \
    courses_taught_by, mark_class_login, show_fairplay_menu, user_manages_class, user_teaches_course
from judge.utils.views import TitleMixin
from judge.views.contests import ContestMixin

__all__ = [
    'ContestFairPlay', 'ContestFairPlayAction', 'ContestFairPlayReport', 'ContestFairPlayStatus',
    'FairPlayClassLoginCreate', 'FairPlayClassLoginStop', 'FairPlayCourse', 'FairPlayHome', 'FairPlayStudentReset',
]

NETID = re.compile(r'^[A-Za-z0-9_.@+-]{1,150}$')


def _count(value):
    """A count FairPlay sent, as a non-negative int (0 for anything else)."""
    if isinstance(value, bool):
        return 0
    try:
        return max(0, int(value))
    except (TypeError, ValueError, OverflowError):
        return 0


def _dicts(value):
    return [item for item in value if isinstance(item, dict)] if isinstance(value, list) else []


def _client(request):
    return FairPlayClient(acting_user=request.user.username)


def _no_store(response):
    response['Cache-Control'] = 'no-store'
    return response


class FairPlayAccessMixin(LoginRequiredMixin):
    def dispatch(self, request, *args, **kwargs):
        if not request.user.is_authenticated:
            return self.handle_no_permission()
        if not show_fairplay_menu(request.user):
            raise Http404()
        return super().dispatch(request, *args, **kwargs)


HOME_TIME_BUDGET = 2.0      # seconds the /fairplay/ home may spend waiting for FairPlay in total


class _StatusReader:

    def __init__(self, client, use_cache=True, budget=None):
        self.client, self.use_cache, self.budget = client, use_cache, budget
        self.spent = 0.0
        self.reachable = True
        self.stopped = False

    def get(self, klass):
        if self.stopped:
            return cached_class_status(klass.id) if self.use_cache else None
        started = monotonic()
        try:
            status = self.client.class_status(klass.id, use_cache=self.use_cache, read_timeout=QUICK_READ_TIMEOUT)
            if isinstance(status, dict) and isinstance(status.get('login'), dict):
                # FairPlay may keep asking about a class with an active login.
                mark_class_login(klass.id)
            return status
        except FairPlayUnreachable:
            self.reachable, self.stopped = False, True
            return cached_class_status(klass.id) if self.use_cache else None
        except FairPlayError:
            return None
        finally:
            self.spent += monotonic() - started
            if self.budget is not None and self.spent > self.budget:
                self.stopped = True


def _class_statuses(client, classes, use_cache=True):
    """The tabs skip the cache, so a student who has just set up shows at once."""
    reader = _StatusReader(client, use_cache=use_cache)
    statuses = {klass.id: reader.get(klass) for klass in classes}
    return statuses, reader.reachable


def _set_up_netids(status):
    students = status.get('students') if isinstance(status, dict) else None
    return {str(s.get('netid', '')).lower() for s in _dicts(students) if s.get('set_up')}


class FairPlayHome(FairPlayAccessMixin, TitleMixin, View):
    title = gettext_lazy('FairPlay')

    def get(self, request):
        # A slow FairPlay costs this page about HOME_TIME_BUDGET; after that, cached counts are shown.
        reader = _StatusReader(_client(request), use_cache=True, budget=HOME_TIME_BUDGET)
        now = timezone.now()
        all_reachable = True
        cards = []
        for org in courses_taught_by(request.user).order_by('name'):
            classes = list(Class.objects.filter(organization=org, is_active=True).order_by('name'))
            netids = set()
            for klass in classes:
                netids |= {p.user.username.lower() for p in class_roster(klass)}
            set_up, reachable = None, True
            if classes:
                statuses = [reader.get(klass) for klass in classes]
                if reader.stopped and any(status is None for status in statuses):
                    reachable = False
                else:
                    done = set()
                    for status in statuses:
                        done |= _set_up_netids(status)
                    set_up = len(done & netids)
            all_reachable = all_reachable and reachable
            exams = course_fairplay_exams(org)
            cards.append({
                'org': org,
                'class_count': len(classes),
                'student_count': len(netids),
                'set_up': set_up,
                'reachable': reachable,
                'exam_count': exams.count(),
                'next_exam': exams.filter(end_time__gt=now).order_by('start_time').first(),
            })
        return render(request, 'fairplay/home.html', {
            'title': _('FairPlay'), 'cards': cards, 'fairplay_reachable': all_reachable,
        })


class FairPlayCourseMixin(FairPlayAccessMixin):
    def get_org(self):
        org = get_object_or_404(Organization, pk=self.kwargs['pk'])
        if not user_teaches_course(self.request.user, org):
            raise Http404()
        return org

    def get_class(self, org):
        klass = get_object_or_404(Class, pk=self.kwargs['class_id'], organization=org)
        if not user_manages_class(self.request.user, klass):
            raise Http404()
        return klass

    def tab_url(self, org, tab):
        return reverse({'exams': 'fairplay_course', 'students': 'fairplay_course_students',
                        'login': 'fairplay_course_login'}[tab], args=[org.id, org.slug])


class FairPlayCourse(FairPlayCourseMixin, View):
    tab = 'exams'

    def get(self, request, pk, slug):
        org = self.get_org()
        return self.render_tab(org)

    def render_tab(self, org, status=200):
        """Never cached by the browser: the Class login tab carries the class login passwords."""
        request = self.request
        context = {'org': org, 'tab': self.tab, 'title': org.name, 'fairplay_reachable': True}
        if self.tab == 'exams':
            now = timezone.now()
            context['exams'] = [{
                'contest': contest, 'can_edit': contest.is_editable_by(request.user),
                'state': (_('Ended') if contest.end_time <= now else
                          _('Running') if contest.start_time <= now else _('Upcoming')),
            } for contest in course_fairplay_exams(org)]
        else:
            classes = list(course_classes_for(request.user, org))
            statuses, reachable = _class_statuses(_client(request), classes, use_cache=False)
            context['fairplay_reachable'] = reachable
            rows = []
            for klass in classes:
                class_status = statuses.get(klass.id)
                login = (class_status or {}).get('login')
                if self.tab != 'login' and isinstance(login, dict):
                    login = {k: v for k, v in login.items() if k != 'password'}
                history = (class_status or {}).get('history') if self.tab == 'login' else None
                rows.append({'klass': klass, 'status': class_status,
                             'login': login if isinstance(login, dict) else None,
                             'history': [h for h in history if isinstance(h, dict)] if isinstance(history, list)
                             else [],
                             'students': self._students(klass, class_status) if self.tab == 'students' else None})
            context['classes'] = rows
        return _no_store(render(request, 'fairplay/course.html', context, status=status))

    @staticmethod
    def _students(klass, status):
        known = {}
        for entry in _dicts(status.get('students') if isinstance(status, dict) else None):
            netid = str(entry.get('netid') or '')
            if netid:
                known[netid.lower()] = entry
        rows = []
        for profile in class_roster(klass):
            username = profile.user.username
            entry = known.pop(username.lower(), None) or {}
            rows.append({'netid': username, 'name': ('%s %s' % (profile.user.first_name, profile.user.last_name))
                         .strip(), 'in_class': True, 'set_up': bool(entry.get('set_up')),
                         'username': entry.get('username') or '', 'set_up_at': entry.get('set_up_at') or ''})
        for netid, entry in sorted(known.items()):
            # Off the roster: only former members who set up through this class's login, not other classes.
            if entry.get('via_this_class') is not True:
                continue
            rows.append({'netid': str(entry.get('netid')), 'name': '', 'in_class': False,
                         'set_up': bool(entry.get('set_up')), 'username': entry.get('username') or '',
                         'set_up_at': entry.get('set_up_at') or ''})
        return rows


class FairPlayCourseStudents(FairPlayCourse):
    tab = 'students'


class FairPlayCourseLogin(FairPlayCourse):
    tab = 'login'


class FairPlayClassLoginCreate(FairPlayCourseMixin, View):
    """DKU OJ never stores the password; a redirect, so a reload can't create another login."""

    def post(self, request, pk, slug, class_id):
        org = self.get_org()
        klass = self.get_class(org)
        # Before the call: FairPlay reads this class's roster when the first student sets up.
        mark_class_login(klass.id)
        try:
            _client(request).create_class_login(klass)
        except FairPlayError as e:
            messages.error(request, e.detail)
        else:
            messages.success(request, _('New class login for %s. The old one no longer works.') % klass.name)
        return HttpResponseRedirect(self.tab_url(org, 'login') + '#fairplay-login-%d' % klass.id)


class FairPlayClassLoginStop(FairPlayCourseMixin, View):
    def post(self, request, pk, slug, class_id):
        org = self.get_org()
        klass = self.get_class(org)
        try:
            _client(request).stop_class_login(klass)
        except FairPlayError as e:
            messages.error(request, e.detail)
        else:
            messages.success(request, _('The class login of %s is stopped.') % klass.name)
        return HttpResponseRedirect(self.tab_url(org, 'login'))


class FairPlayStudentReset(FairPlayCourseMixin, View):
    """Only students of this class; FairPlay gets the canonical NetID, never the URL's spelling."""

    def post(self, request, pk, slug, class_id, netid):
        org = self.get_org()
        klass = self.get_class(org)
        client = _client(request)
        wanted = netid.lower()
        canonical = next((p.user.username for p in class_roster(klass) if p.user.username.lower() == wanted), None)
        if canonical is None:
            try:
                status = client.class_status(klass.id, use_cache=False, read_timeout=QUICK_READ_TIMEOUT)
            except FairPlayError as e:
                messages.error(request, e.detail)
                return HttpResponseRedirect(self.tab_url(org, 'students'))
            canonical = next((str(s.get('netid')) for s in _dicts(status.get('students'))
                              if str(s.get('netid') or '').lower() == wanted and s.get('via_this_class') is True),
                             None)
            if canonical is None:
                raise Http404()
        try:
            client.reset_student(canonical, klass.id)
        except FairPlayError as e:
            messages.error(request, e.detail)
        else:
            messages.success(request, _('%s was reset. They set up FairPlay again with the class login.') % canonical)
        return HttpResponseRedirect(self.tab_url(org, 'students'))


def _fairplay_contest(request, key):
    contest = get_object_or_404(Contest, key=key)
    if not fairplay_enabled() or not is_fairplay_exam(contest) or not contest.is_editable_by(request.user):
        raise Http404()
    return contest


def _roster_usernames(contest):
    return {p.user.username.lower(): p.user.username for p in exam_roster(contest)}


class ContestFairPlay(LoginRequiredMixin, ContestMixin, TitleMixin, DetailView):
    template_name = 'contest/fairplay.html'

    def get_title(self):
        return _('%s FairPlay') % self.object.name

    def dispatch(self, request, *args, **kwargs):
        # Checked before ContestMixin.dispatch, which would turn a 404 into a "No such contest" page.
        if request.user.is_authenticated:
            _fairplay_contest(request, kwargs['contest'])
        return super().dispatch(request, *args, **kwargs)

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        contest = self.object
        context['tab'] = 'fairplay'
        context['fp'] = None
        context['fp_error'] = None
        try:
            context['fp'] = _client(self.request).exam(contest.key, read_timeout=QUICK_READ_TIMEOUT)
        except FairPlayError as e:
            context['fp_error'] = e.detail
        fp = context['fp'] or {}
        context['fp_status'] = fp.get('status') if fp.get('synced', True) else None
        # The settings live in FairPlay, so they are shown only while it answers.
        fp_settings = fp.get('settings') if isinstance(fp.get('settings'), dict) else None
        reachable = context['fp'] is not None
        form = editor_settings(fp_settings) if reachable else None
        unsaved = pop_unsaved_settings(self.request, contest.key)
        if unsaved and isinstance(unsaved.get('settings'), dict) and unsaved['settings']:
            # Settings FairPlay didn't take from the editor are offered again.
            form = dict(form or editor_settings(None), **unsaved['settings'])
            if isinstance(form.get('websites'), list):
                form['websites'] = '\n'.join(form['websites'])
        context['fp_unsaved'] = unsaved
        context['settings_form'] = form
        context['settings_editable'] = reachable and context['fp_status'] != 'closed'
        context['fp_websites_blocked'] = editor_settings(fp_settings)['websites_blocked'] if reachable else []
        context['fp_companions'] = [
            {'name': str(item.get('name') or item.get('for') or ''),
             'sites': [str(site) for site in item.get('sites') or [] if isinstance(site, str)][:20]
             if isinstance(item.get('sites'), list) else []}
            for item in _dicts((fp_settings or {}).get('companions'))][:10]
        context['calculator_choices'] = FAIRPLAY_CALCULATOR_CHOICES
        raw_counts = fp.get('counts') if isinstance(fp.get('counts'), dict) else {}
        counts = {name: _count(value) for name, value in raw_counts.items()}
        context['fp_counts'] = counts
        roster = list(exam_roster(contest))
        context['roster'] = roster
        context['not_set_up'] = max(0, counts.get('roster', 0) - counts.get('set_up', 0))
        open_to = fp.get('open_to')
        context['open_to_all'] = open_to in (None, 'all')
        context['open_to_set'] = {str(n).lower() for n in open_to} if isinstance(open_to, list) else set()
        students = _dicts(fp.get('students'))
        context['fp_students'] = students
        context['fp_waiting'] = [s for s in students if s.get('status') == 'waiting']
        # A hidden (not visible) assignment is a draft: FairPlay is not told about it.
        context['fp_not_listed'] = not contest.is_visible
        context['review_choices'] = FAIRPLAY_REVIEW_CHOICES
        context['review_help'] = FAIRPLAY_REVIEW_HELP
        context['review_value'] = editor_settings(fp_settings)['review']
        context['delivery_warnings'] = delivery_warnings(contest)
        return context

    def render_to_response(self, context, **kwargs):
        return _no_store(super().render_to_response(context, **kwargs))


class ContestFairPlayStatus(LoginRequiredMixin, View):
    """Polled by the live student table; the short timeout keeps a slow FairPlay from holding workers."""

    def get(self, request, contest):
        contest = _fairplay_contest(request, contest)
        try:
            data = _client(request).exam(contest.key, read_timeout=QUICK_READ_TIMEOUT)
        except FairPlayError as e:
            return _no_store(JsonResponse({'ok': False, 'detail': e.detail}, status=200))
        return _no_store(JsonResponse({'ok': True, 'exam': data}))


class ContestFairPlayAction(LoginRequiredMixin, View):
    """The FairPlay tab's buttons, one POST per action."""
    ACTIONS = {
        'open': '_open', 'close': '_close', 'add-time': '_add_time', 'new-code': '_new_code',
        'approve': '_approve', 'review': '_review', 'settings': '_settings', 'clear-hand-in': '_clear_hand_in',
    }

    def post(self, request, contest, action):
        contest = _fairplay_contest(request, contest)
        if action not in self.ACTIONS:
            raise Http404()
        back = HttpResponseRedirect(reverse('contest_fairplay', args=[contest.key]))
        return getattr(self, self.ACTIONS[action])(request, contest, back)

    @staticmethod
    def _call(request, back, call, success):
        try:
            call(_client(request))
        except FairPlayError as e:
            messages.error(request, e.detail)
        else:
            messages.success(request, success)
        return back

    def _open(self, request, contest, back):
        if request.POST.get('open_to') != 'picked':
            return self._call(request, back, lambda client: client.open_exam(contest.key, 'all'),
                              _('The exam is open.'))
        roster = _roster_usernames(contest)
        picked = []
        for netid in request.POST.getlist('netids'):
            username = roster.get(netid.lower())
            if username is None:
                messages.error(request, _("%s is not on this assignment's student list.") % netid)
                return back
            if username not in picked:
                picked.append(username)
        if not picked:
            messages.error(request, _('Pick at least one student, or let everyone start.'))
            return back
        return self._call(request, back, lambda client: client.open_exam(contest.key, picked), _('The exam is open.'))

    def _close(self, request, contest, back):
        return self._call(request, back, lambda client: client.close_exam(contest.key),
                          _('The exam is closed. Students who were writing handed in what was saved.'))

    def _add_time(self, request, contest, back):
        try:
            minutes = int(request.POST.get('minutes') or 10)
        except ValueError:
            minutes = 0
        if not 1 <= minutes <= 120:
            messages.error(request, _('Add between 1 and 120 minutes.'))
            return back

        def add(client):
            client.add_time(contest.key, minutes)
            # FairPlay closes later; DKU OJ judges lateness and the hand-in window by it.
            record_added_minutes(contest, minutes)
        return self._call(request, back, add, _('%d minutes added.') % minutes)

    def _new_code(self, request, contest, back):
        return self._call(request, back, lambda client: client.new_code(contest.key), _('A new start code is ready.'))

    def _approve(self, request, contest, back):
        netid = request.POST.get('netid') or ''
        username = _roster_usernames(contest).get(netid.lower()) if NETID.match(netid) else None
        if username is None:
            messages.error(request, _("This student is not on this assignment's student list."))
            return back
        return self._call(request, back, lambda client: client.approve(contest.key, username),
                          _('%s may continue.') % username)

    @staticmethod
    def _clear_hand_in(request, contest, back):
        netid = request.POST.get('netid') or ''
        profile = Profile.objects.filter(user__username=netid).first() if NETID.match(netid) else None
        if profile is None or not clear_hand_in(contest, profile):
            messages.error(request, _('There is no FairPlay hand-in of this student to clear.'))
            return back
        messages.success(request, _('The FairPlay hand-in of %s is cleared. FairPlay can now deliver another '
                                    'attempt; the submissions already filed stay.') % profile.user.username)
        return HttpResponseRedirect(reverse('contest_fairplay_report', args=[contest.key, profile.user.username]))

    @staticmethod
    def _review(request, contest, back):
        """Unlike the other settings, changeable at any time, even after the exam."""
        value = request.POST.get('review') or ''
        if value not in dict(FAIRPLAY_REVIEW_CHOICES):
            messages.error(request, _('Pick what students can see after handing in.'))
            return back
        try:
            _client(request).set_settings(contest.key, {'review': value})
        except FairPlayError as e:
            messages.error(request, _('Not saved: %s') % e.detail)
        else:
            messages.success(request, _('Saved. Students now see: %s.') % dict(FAIRPLAY_REVIEW_CHOICES)[value])
        return back

    @staticmethod
    def _settings(request, contest, back):
        """What FairPlay doesn't take is kept for this page instead of being lost."""
        post = request.POST
        raw = {'calculator': post.get('calculator') or 'off', 'websites': post.get('websites') or '',
               'upload_opens_days': post.get('upload_opens_days') or 0}
        for field in ('open_book', 'strong_lock', 'resume_needs_approval'):
            raw[field] = field in post
        try:
            settings = clean_settings(raw, partial=True)
        except ValidationError as e:
            stash_unsaved_settings(request, contest.key, dict(raw), ' '.join(e.messages))
            messages.error(request, ' '.join(e.messages))
            return back
        try:
            view = _client(request).set_settings(contest.key, settings)
        except FairPlayError as e:
            stash_unsaved_settings(request, contest.key, settings, e.detail)
            messages.error(request, _('Not saved: %s') % e.detail)
            return back
        blocked = editor_settings((view or {}).get('settings')).get('websites_blocked')
        if blocked:
            messages.warning(request, _('Saved. FairPlay will not open these websites: %s.') % ', '.join(
                item['site'] for item in blocked[:10]))
        else:
            messages.success(request, _('Saved in FairPlay.'))
        return back


class ContestFairPlayReport(LoginRequiredMixin, View):
    def get(self, request, contest, netid):
        contest = _fairplay_contest(request, contest)
        if not NETID.match(netid):
            raise Http404()
        report, error = None, None
        try:
            report = _client(request).student_report(contest.key, netid)
        except FairPlayError as e:
            if e.status == 404:
                raise Http404()
            error = e.detail
        events = report.get('events') if isinstance(report, dict) else None
        profile = Profile.objects.filter(user__username=netid).first()
        return _no_store(render(request, 'contest/fairplay-report.html', {
            'contest': contest, 'report': report or {}, 'events': events if isinstance(events, list) else [],
            'fp_error': error, 'netid': netid,
            'hand_in': active_hand_in(contest, profile) if profile is not None else None,
            'title': _('FairPlay report: %s') % netid,
        }))
