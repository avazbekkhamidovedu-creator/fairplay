from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.validators import MaxValueValidator, MinValueValidator, RegexValidator
from django.db import models, transaction
from django.db.models import CASCADE, Exists, OuterRef, Q
from django.urls import reverse
from django.utils import timezone
from django.utils.functional import cached_property
from django.utils.translation import gettext_lazy as _
from jsonfield import JSONField
from lupa import LuaRuntime
from moss import MOSS_LANG_C, MOSS_LANG_CC, MOSS_LANG_JAVA, MOSS_LANG_PYTHON

from judge import contest_format
from judge.models.problem import Problem
from judge.models.profile import Class, Organization, Profile
from judge.models.submission import Submission
from judge.ratings import rate_contest
from judge.utils.course_roles import teaching_q
from judge.utils.unique_ids import next_prefixed_id

__all__ = [
    'Contest', 'ContestTag', 'ContestParticipation', 'ContestProblem', 'ContestSubmission', 'Rating',
    'contest_problem_display_name',
]


class MinValueOrNoneValidator(MinValueValidator):
    def compare(self, a, b):
        return a is not None and b is not None and super().compare(a, b)


class ContestTag(models.Model):
    color_validator = RegexValidator('^#(?:[A-Fa-f0-9]{3}){1,2}$', _('Invalid colour.'))

    name = models.CharField(max_length=20, verbose_name=_('tag name'), unique=True,
                            validators=[RegexValidator(r'^[a-z-]+$', message=_('Lowercase letters and hyphens only.'))])
    color = models.CharField(max_length=7, verbose_name=_('tag colour'), validators=[color_validator])
    description = models.TextField(verbose_name=_('tag description'), blank=True)

    def __str__(self):
        return self.name

    def get_absolute_url(self):
        return reverse('contest_tag', args=[self.name])

    @property
    def text_color(self, cache={}):
        if self.color not in cache:
            if len(self.color) == 4:
                r, g, b = [ord(bytes.fromhex(i * 2)) for i in self.color[1:]]
            else:
                r, g, b = [i for i in bytes.fromhex(self.color[1:])]
            cache[self.color] = '#000' if 299 * r + 587 * g + 144 * b > 140000 else '#fff'
        return cache[self.color]

    class Meta:
        verbose_name = _('contest tag')
        verbose_name_plural = _('contest tags')


class Contest(models.Model):
    SCOREBOARD_VISIBLE = 'V'
    SCOREBOARD_AFTER_CONTEST = 'C'
    SCOREBOARD_AFTER_PARTICIPATION = 'P'
    SCOREBOARD_HIDDEN = 'H'
    SCOREBOARD_VISIBILITY = (
        (SCOREBOARD_VISIBLE, _('Visible')),
        (SCOREBOARD_AFTER_CONTEST, _('Hidden for duration of contest')),
        (SCOREBOARD_AFTER_PARTICIPATION, _('Hidden for duration of participation')),
        (SCOREBOARD_HIDDEN, _('Hidden permanently')),
    )
    key = models.CharField(max_length=20, verbose_name=_('contest id'), unique=True,
                           validators=[RegexValidator('^[a-z0-9]+$', _('Contest id must be ^[a-z0-9]+$'))])
    name = models.CharField(max_length=100, verbose_name=_('contest name'), db_index=True)
    authors = models.ManyToManyField(Profile, verbose_name=_('authors'),
                                     help_text=_('These users will be able to edit the contest.'),
                                     related_name='authored_contests')
    curators = models.ManyToManyField(Profile, verbose_name=_('curators'),
                                      help_text=_('These users will be able to edit the contest, '
                                                  'but will not be listed as authors.'),
                                      related_name='curated_contests', blank=True)
    testers = models.ManyToManyField(Profile, verbose_name=_('testers'),
                                     help_text=_('These users will be able to view the contest, but not edit it.'),
                                     blank=True, related_name='tested_contests')
    tester_see_scoreboard = models.BooleanField(verbose_name=_('testers see scoreboard'), default=False,
                                                help_text=_('If testers can see the scoreboard.'))
    tester_see_submissions = models.BooleanField(verbose_name=_('testers see submissions'), default=False,
                                                 help_text=_('If testers can see in-contest submissions.'))
    spectators = models.ManyToManyField(Profile, verbose_name=_('spectators'),
                                        help_text=_('These users will be able to spectate the contest, '
                                                    'but not see the problems ahead of time.'),
                                        blank=True, related_name='spectated_contests')
    description = models.TextField(verbose_name=_('description'), blank=True)
    problems = models.ManyToManyField(Problem, verbose_name=_('problems'), through='ContestProblem')
    start_time = models.DateTimeField(verbose_name=_('start time'), db_index=True)
    end_time = models.DateTimeField(verbose_name=_('end time'), db_index=True)
    time_limit = models.DurationField(verbose_name=_('time limit'), blank=True, null=True)
    is_visible = models.BooleanField(verbose_name=_('publicly visible'), default=False,
                                     help_text=_('Should be set even for organization-private contests, where it '
                                                 'determines whether the contest is visible to members of the '
                                                 'specified organizations.'))
    is_rated = models.BooleanField(verbose_name=_('contest rated'), help_text=_('Whether this contest can be rated.'),
                                   default=False)
    view_contest_scoreboard = models.ManyToManyField(Profile, verbose_name=_('view contest scoreboard'), blank=True,
                                                     related_name='view_contest_scoreboard',
                                                     help_text=_('These users will be able to view the scoreboard.'))
    view_contest_submissions = models.ManyToManyField(Profile, verbose_name=_('can see contest submissions'),
                                                      blank=True, related_name='view_contest_submissions',
                                                      help_text=_('These users will be able '
                                                                  'to see in-contest submissions.'))
    # Model / Django admin default is visible (traditional contests). The
    # assignment editor defaults new homework to SCOREBOARD_HIDDEN instead.
    scoreboard_visibility = models.CharField(verbose_name=_('scoreboard visibility'), default=SCOREBOARD_VISIBLE,
                                             help_text=_('Scoreboard visibility through the duration of the contest.'),
                                             max_length=1, choices=SCOREBOARD_VISIBILITY)
    use_clarifications = models.BooleanField(verbose_name=_('no comments'),
                                             help_text=_('Use clarification system instead of comments.'),
                                             default=True)
    rating_floor = models.IntegerField(verbose_name=_('rating floor'),
                                       help_text=_('Do not rate users who have a lower rating.'), null=True, blank=True)
    rating_ceiling = models.IntegerField(verbose_name=_('rating ceiling'),
                                         help_text=_('Do not rate users who have a higher rating.'),
                                         null=True, blank=True)
    performance_ceiling_override = models.IntegerField(verbose_name=_('performance ceiling override'),
                                                       help_text=_('Overrides the performance_ceiling parameter '
                                                                   'used in computing ratings. '
                                                                   "Do not modify unless you know what you're doing!"),
                                                       null=True, blank=True)
    rate_all = models.BooleanField(verbose_name=_('rate all'),
                                   help_text=_('Rate users even if they make no submissions.'),
                                   default=False)
    rate_exclude = models.ManyToManyField(Profile, verbose_name=_('exclude from ratings'), blank=True,
                                          related_name='rate_exclude+')
    is_private = models.BooleanField(verbose_name=_('private to specific users'), default=False)
    private_contestants = models.ManyToManyField(Profile, blank=True, verbose_name=_('private contestants'),
                                                 help_text=_('If non-empty, only these users may see the contest.'),
                                                 related_name='private_contestants+')
    hide_problem_tags = models.BooleanField(verbose_name=_('hide problem tags'),
                                            help_text=_('Whether problem tags should be hidden by default.'),
                                            default=False)
    hide_problem_authors = models.BooleanField(verbose_name=_('hide problem authors'),
                                               help_text=_('Whether problem authors should be hidden by default.'),
                                               default=False)
    run_pretests_only = models.BooleanField(verbose_name=_('run pretests only'),
                                            help_text=_('Whether judges should grade pretests only, versus all '
                                                        'testcases. Commonly set during a contest, then unset '
                                                        'prior to rejudging user submissions when the contest ends.'),
                                            default=False)
    results_finalized = models.BooleanField(verbose_name=_('results finalized'),
                                            help_text=_('Set by the "Finalize results" action when an exam-style '
                                                        'contest ends: it reveals hidden problem results to students '
                                                        'and folds them into the scoreboard. Leave unset while the '
                                                        'contest is running.'),
                                            default=False)
    AI_GRADING_OFF = 'off'
    AI_GRADING_REALTIME = 'realtime'
    AI_GRADING_BATCH = 'batch'
    AI_GRADING_MODE_CHOICES = (
        (AI_GRADING_OFF, _('Off')),
        (AI_GRADING_REALTIME, _('On demand — student requests AI grading')),
        (AI_GRADING_BATCH, _('Batch — latest submits after the contest')),
    )
    ai_grading_mode = models.CharField(
        verbose_name=_('AI grading (legacy mode)'), max_length=16,
        choices=AI_GRADING_MODE_CHOICES, default=AI_GRADING_OFF,
        help_text=_('Legacy field kept for older assignments. Prefer '
                    '“allow student AI grading”. Synced from that checkbox on save: '
                    'on → on-demand, off → off. Teachers can always run AI from the '
                    'Grade page regardless of this value.'),
    )
    allow_student_ai_grading = models.BooleanField(
        verbose_name=_('allow student use AI grading'),
        default=False,
        help_text=_('When enabled, students can request AI grading on the submission '
                    'page (subject to quota). Teachers can always run AI grading from '
                    'the Grade page or submission page.'),
    )
    ai_grading_auto_delay_minutes = models.IntegerField(
        verbose_name=_('auto-grade delay after end (minutes)'),
        default=120,
        help_text=_('Minutes after the contest ends to re-grade each student’s latest '
                    'objective submissions (multiple choice, true/false, exact fill-blank). '
                    'Does not call the LLM. −1 = Off (no auto-run). 0 = run at end time. '
                    'Teachers can still use Batch grading on the Grade page anytime.'),
    )
    ai_grading_scheduled_task_id = models.CharField(
        verbose_name=_('scheduled batch AI task id'), max_length=255, blank=True, default='',
    )
    ai_grading_batch_started_at = models.DateTimeField(
        verbose_name=_('batch AI started'), null=True, blank=True,
    )
    ai_grading_batch_finished_at = models.DateTimeField(
        verbose_name=_('batch AI finished'), null=True, blank=True,
    )
    harvest_scheduled_task_id = models.CharField(
        verbose_name=_('scheduled auto-submit task id'), max_length=255, blank=True, default='',
        help_text=_('Celery task that auto-submits saved answers when this assignment ends. '
                    'Assignments with a personal time limit are scheduled per participation instead.'),
    )
    show_short_display = models.BooleanField(verbose_name=_('show short form settings display'),
                                             help_text=_('Whether to show a section containing contest settings '
                                                         'on the contest page or not.'),
                                             default=False)
    is_organization_private = models.BooleanField(verbose_name=_('private to organizations'), default=False)
    organizations = models.ManyToManyField(Organization, blank=True, verbose_name=_('organizations'),
                                           help_text=_('If non-empty, only these organizations may see the contest.'))
    limit_join_organizations = models.BooleanField(verbose_name=_('limit organizations that can join'), default=False)
    join_organizations = models.ManyToManyField(Organization, blank=True, verbose_name=_('join organizations'),
                                                help_text=_('If non-empty, only these organizations may join '
                                                            'the contest.'), related_name='join_only_contests')
    classes = models.ManyToManyField(Class, blank=True, verbose_name=_('classes'),
                                     help_text=_('If organization private, only these classes may see the contest.'))
    og_image = models.CharField(verbose_name=_('OpenGraph image'), default='', max_length=150, blank=True)
    logo_override_image = models.CharField(verbose_name=_('logo override image'), default='', max_length=150,
                                           blank=True,
                                           help_text=_('This image will replace the default site logo for users '
                                                       'inside the contest.'))
    tags = models.ManyToManyField(ContestTag, verbose_name=_('contest tags'), blank=True, related_name='contests')
    user_count = models.IntegerField(verbose_name=_('the amount of live participants'), default=0)
    summary = models.TextField(blank=True, verbose_name=_('contest summary'),
                               help_text=_('Plain-text, shown in meta description tag, e.g. for social media.'))
    access_code = models.CharField(verbose_name=_('access code'), blank=True, default='', max_length=255,
                                   help_text=_('An optional code to prompt contestants before they are allowed '
                                               'to join the contest. Leave it blank to disable.'))
    banned_users = models.ManyToManyField(Profile, verbose_name=_('personae non gratae'), blank=True,
                                          help_text=_('Bans the selected users from joining this contest.'))
    format_name = models.CharField(verbose_name=_('contest format'), default='default', max_length=32,
                                   choices=contest_format.choices(), help_text=_('The contest format module to use.'))
    format_config = JSONField(verbose_name=_('contest format configuration'), null=True, blank=True,
                              help_text=_('A JSON object to serve as the configuration for the chosen contest format '
                                          'module. Leave empty to use None. Exact format depends on the contest format '
                                          'selected.'))
    problem_label_script = models.TextField(verbose_name=_('contest problem label script'), blank=True,
                                            help_text=_('A custom Lua function to generate problem labels. Requires a '
                                                        'single function with an integer parameter, the zero-indexed '
                                                        'contest problem index, and returns a string, the label.'))
    locked_after = models.DateTimeField(verbose_name=_('contest lock'), null=True, blank=True,
                                        help_text=_('Prevent submissions from this contest '
                                                    'from being rejudged after this date.'))
    points_precision = models.IntegerField(verbose_name=_('precision points'), default=3,
                                           validators=[MinValueValidator(0), MaxValueValidator(10)],
                                           help_text=_('Number of digits to round points to.'))
    allow_virtual_participation = models.BooleanField(
        verbose_name=_('allow virtual join'),
        default=True,
        help_text=_('Allow users to start a virtual participation after the assignment ends.'),
    )
    # Spectating is deliberately separate from virtual participation: virtual
    # joins happen after the deadline, while spectating is an in-progress,
    # non-scoring participation.
    allow_spectate = models.BooleanField(
        verbose_name=_('allow spectate'),
        default=False,
        help_text=_('Allow students to spectate while the assignment is active. Spectator submissions do not count '
                    'toward statistics or rankings.'),
    )
    hide_sandbox_output = models.BooleanField(
        verbose_name=_('Hide IDE / Run Test output'),
        default=False,
        help_text=_('Hide sandbox stdout from students. The verdict, compile errors, and '
                    'runtime exceptions remain visible. This does not close the IDE, lock '
                    'problems, or change Submit.'),
    )
    allow_ai_hints = models.BooleanField(
        verbose_name=_('allow student use AI hints'),
        default=False,
        help_text=_('When this assignment is active, students can use AI Hint on every attached '
                    "problem. Each problem's own AI Hint setting is unchanged outside this assignment."),
    )

    @classmethod
    def generate_unique_key(cls, spread=0):
        """Next unused assignment/contest id in the clone/admin series ``c000000``.

        ``spread`` > 0 picks among the first few free ids (retry after losing a race).
        """
        # Only ids that can collide with the series: not every key on the site.
        return next_prefixed_id(
            cls.objects.filter(key__startswith='c').values_list('key', flat=True),
            'c',
            fallback_prefix='contest',
            spread=spread,
        )

    @cached_property
    def format_class(self):
        return contest_format.formats[self.format_name]

    @cached_property
    def format(self):
        return self.format_class(self, self.format_config)

    @cached_property
    def get_label_for_problem(self):
        if not self.problem_label_script:
            return self.format.get_label_for_problem

        def DENY_ALL(obj, attr_name, is_setting):
            raise AttributeError()
        lua = LuaRuntime(attribute_filter=DENY_ALL, register_eval=False, register_builtins=False)
        return lua.eval(self.problem_label_script)

    def clean(self):
        # Django will complain if you didn't fill in start_time or end_time, so we don't have to.
        if self.start_time and self.end_time and self.start_time >= self.end_time:
            raise ValidationError(_('What is this? An assignment that ended before it starts?'))
        self.format_class.validate(self.format_config)

        try:
            # a contest should have at least one problem, with contest problem index 0
            # so test it to see if the script returns a valid label.
            label = self.get_label_for_problem(0)
        except Exception as e:
            raise ValidationError('Contest problem label script: %s' % e)
        else:
            if not isinstance(label, str):
                raise ValidationError('Contest problem label script: script should return a string.')

    def is_in_contest(self, user):
        if user.is_authenticated:
            profile = user.profile
            return profile and profile.current_contest is not None and profile.current_contest.contest == self
        return False

    def can_see_own_scoreboard(self, user):
        if self.can_see_full_scoreboard(user):
            return True
        if not self.started:
            return False
        if not self.show_scoreboard and not self.is_in_contest(user) and not self.has_completed_contest(user):
            return False
        return True

    def can_see_full_scoreboard(self, user):
        if self.show_scoreboard:
            return True
        if not user.is_authenticated:
            return False
        # Asked once per problem row on the assignment page.
        return self._memo('full_scoreboard', user, lambda: self._can_see_full_scoreboard(user))

    def _can_see_full_scoreboard(self, user):
        if user.has_perm('judge.see_private_contest') or user.has_perm('judge.edit_all_contest'):
            return True
        if user.profile.id in self.editor_ids:
            return True
        if self.tester_see_scoreboard and user.profile.id in self.tester_ids:
            return True
        if self.started and user.profile.id in self.spectator_ids:
            return True
        if self.view_contest_scoreboard.filter(id=user.profile.id).exists():
            return True
        if self.scoreboard_visibility == self.SCOREBOARD_AFTER_PARTICIPATION and self.has_completed_contest(user):
            return True
        return False

    def has_completed_contest(self, user):
        if user.is_authenticated:
            participation = self.users.filter(virtual=ContestParticipation.LIVE, user=user.profile).first()
            if participation and participation.ended:
                return True
        return False

    @cached_property
    def show_scoreboard(self):
        if not self.started:
            return False
        if (self.scoreboard_visibility in (self.SCOREBOARD_AFTER_CONTEST, self.SCOREBOARD_AFTER_PARTICIPATION) and
                not self.ended):
            return False
        return self.scoreboard_visibility != self.SCOREBOARD_HIDDEN

    @property
    def contest_window_length(self):
        return self.end_time - self.start_time

    @cached_property
    def _now(self):
        # This ensures that all methods talk about the same now.
        return timezone.now()

    @cached_property
    def started(self):
        return self.start_time <= self._now

    @property
    def time_before_start(self):
        if self.start_time >= self._now:
            return self.start_time - self._now
        else:
            return None

    @property
    def time_before_end(self):
        if self.end_time >= self._now:
            return self.end_time - self._now
        else:
            return None

    @cached_property
    def performance_ceiling(self):
        if self.performance_ceiling_override is not None:
            return self.performance_ceiling_override
        elif self.rating_ceiling:
            return self.rating_ceiling + settings.DMOJ_CONTEST_PERF_CEILING_INCREMENT
        else:
            return None

    @cached_property
    def ended(self):
        return self.end_time < self._now

    @cached_property
    def author_ids(self):
        return Contest.authors.through.objects.filter(contest=self).values_list('profile_id', flat=True)

    @cached_property
    def editor_ids(self):
        # Lists prefetch authors and curators; use them instead of one query per row.
        prefetched = getattr(self, '_prefetched_objects_cache', {})
        if 'authors' in prefetched and 'curators' in prefetched:
            return {p.id for p in prefetched['authors']} | {p.id for p in prefetched['curators']}
        return self.author_ids.union(
            Contest.curators.through.objects.filter(contest=self).values_list('profile_id', flat=True))

    @cached_property
    def tester_ids(self):
        return Contest.testers.through.objects.filter(contest=self).values_list('profile_id', flat=True)

    @cached_property
    def spectator_ids(self):
        return Contest.spectators.through.objects.filter(contest=self).values_list('profile_id', flat=True)

    def __str__(self):
        return self.name

    def get_absolute_url(self):
        return reverse('contest_view', args=(self.key,))

    def update_user_count(self):
        self.user_count = self.users.filter(virtual=0).count()
        # Only this column: students join while a teacher edits the assignment, and a full save
        # of this (request-old) instance would revert the teacher's saved changes.
        self.save(update_fields=['user_count'])

    update_user_count.alters_data = True

    class Inaccessible(Exception):
        pass

    class PrivateContest(Exception):
        pass

    def access_check(self, user):
        # Do unauthenticated check here so we can skip authentication checks later on.
        if not user.is_authenticated:
            # Unauthenticated users can only see visible, non-private contests
            if not self.is_visible:
                raise self.Inaccessible()
            if self.is_private or self.is_organization_private:
                raise self.PrivateContest()
            return

        # If the user can view or edit all contests
        if user.has_perm('judge.see_private_contest') or user.has_perm('judge.edit_all_contest'):
            return

        # Fast path for the common case, a student of a linked class (2 queries instead of ~12;
        # the contest middleware runs this check on every request of a joined student). Every
        # check below this one either grants access or, once all have failed, refuses it, so
        # granting here first gives the same answer as the class-membership grant further down.
        if self.is_visible and self.is_organization_private and not self.is_private and self._user_in_class(user):
            return

        # User is organizer or curator for contest
        if user.profile.id in self.editor_ids:
            return

        # User is tester for contest
        if user.profile.id in self.tester_ids:
            return

        # User is spectator for contest
        if user.profile.id in self.spectator_ids:
            return

        # Org admins can always see org-private contests
        if self.is_viewable_by_org_admin(user):
            return

        # Course teachers can view org-private assignments in their course, but an
        # unpublished (hidden) one only by its own editors/testers and the course
        # admins of a course it is linked to, not by every teacher who joined the course.
        if self.is_organization_private:
            if self.is_visible and self.is_viewable_by_course_teacher(user):
                return
            if not self.is_visible and self.is_viewable_by_linked_course_admin(user):
                return

        # Contest is not publicly visible
        if not self.is_visible:
            raise self.Inaccessible()

        # Contest is not private
        if not self.is_private and not self.is_organization_private:
            return

        if self.view_contest_scoreboard.filter(id=user.profile.id).exists():
            return

        # Check organization/class membership for students
        if self.is_organization_private:
            # If classes are specified, check class membership
            if self.classes.exists():
                in_class = self._user_in_class(user)
                if not self.is_private and in_class:
                    return
                if self.is_private:
                    in_users = self.private_contestants.filter(id=user.profile.id).exists()
                    if in_class and in_users:
                        return
                raise self.PrivateContest()
            else:
                # No classes specified - check org membership
                in_org = self.organizations.filter(id__in=user.profile.organizations.all()).exists()
                if not self.is_private and in_org:
                    return
                if self.is_private:
                    in_users = self.private_contestants.filter(id=user.profile.id).exists()
                    if in_org and in_users:
                        return
                raise self.PrivateContest()

        if self.is_private and not self.is_organization_private:
            in_users = self.private_contestants.filter(id=user.profile.id).exists()
            if in_users:
                return
            raise self.PrivateContest()

    def _user_in_class(self, user):
        """Whether the user is a member of one of the classes this contest is assigned to."""
        return self._memo('in_class', user,
                          lambda: self.classes.filter(id__in=user.profile.classes.all()).exists())

    # Assumes the user can access, to avoid the cost again
    def is_live_joinable_by(self, user):
        if not self.started:
            return False

        if not user.is_authenticated:
            return False

        # Can be populated using annotate for performance in list views. Editors, testers and
        # spectators may only spectate, never sit the assignment as a live participant.
        editor_or_tester = getattr(self, 'editor_or_tester', None)
        if editor_or_tester is None:
            pid = user.profile.id
            editor_or_tester = pid in self.editor_ids or pid in self.tester_ids or pid in self.spectator_ids

        if editor_or_tester:
            return False

        completed_contest = getattr(self, 'completed_contest', None)
        if completed_contest is None:
            completed_contest = self.has_completed_contest(user)

        if completed_contest:
            return False

        if self.limit_join_organizations:
            return self.join_organizations.filter(id__in=user.profile.organizations.all()).exists()
        return True

    # Also skips access check
    def is_spectatable_by(self, user):
        if not user.is_authenticated:
            return False

        pid = user.profile.id
        if pid in self.editor_ids or pid in self.tester_ids or pid in self.spectator_ids:
            return True

        # Spectating is opt-in for each assignment. Virtual participation uses
        # a separate switch because it is only available after the deadline.
        if not self.allow_spectate:
            return False

        if self.limit_join_organizations:
            return self.join_organizations.filter(id__in=user.profile.organizations.all()).exists()
        return True

    def is_accessible_by(self, user):
        try:
            self.access_check(user)
        except (self.Inaccessible, self.PrivateContest):
            return False
        else:
            return True

    def is_editable_by(self, user):
        # Grade / analytics / in-contest staff tools: edit_all_contest, or being
        # listed as author/curator. This is *not* Django admin's change
        # permission — Teachers often have no ``edit_own_contest``, and the
        # grade page still admits them via membership. Add / Edit / Clone /
        # Delete on the frontend use the admin-aligned helpers below.
        if user.has_perm('judge.edit_all_contest'):
            return True

        # Being listed as an author/curator IS the grant for grading. Sensitive
        # fields inside the assignment editor are still gated by their own
        # permissions via ContestCreate._restricted_fields.
        if user.is_authenticated and user.profile.id in self.editor_ids:
            return True

        return False

    @classmethod
    def can_be_added_by(cls, user):
        """Match ContestAdmin.has_add_permission (Django default: ``add_contest``)."""
        return bool(user.is_authenticated and user.has_perm('judge.add_contest'))

    def is_changeable_by(self, user):
        """Match ContestAdmin.has_change_permission for this contest.

        Admin requires ``edit_own_contest`` first, then ``is_editable_by``
        (``edit_all_contest`` or author/curator).
        """
        if not user.is_authenticated or not user.has_perm('judge.edit_own_contest'):
            return False
        return self.is_editable_by(user)

    def _memo(self, name, user, compute):
        """Remember a per-user answer on this instance for the rest of its life (one request).

        The contest middleware checks access on every request and the views then ask the same
        questions again (G-perf: ~7 queries each time); the answers cannot change in between.
        """
        memo = self.__dict__.setdefault('_per_user_memo', {})
        key = (name, user.pk)
        if key not in memo:
            memo[key] = compute()
        return memo[key]

    def is_viewable_by_org_admin(self, user):
        """Check if user is org admin of any organization this contest belongs to."""
        if not user.is_authenticated:
            return False
        if not self.is_organization_private:
            return False
        return self._memo('org_admin', user, lambda: self.organizations.filter(admins=user.profile).exists())

    @cached_property
    def _linked_organization_ids(self):
        org_ids = set(self.organizations.values_list('id', flat=True))
        org_ids.update(self.classes.values_list('organization_id', flat=True))
        return frozenset(org_ids)

    def linked_organization_ids(self):
        return set(self._linked_organization_ids)

    def is_viewable_by_linked_course_admin(self, user):
        """Course administrators (Organization.admins) of a course linked to this contest."""
        if not user.is_authenticated:
            return False
        org_ids = self.linked_organization_ids()
        if not org_ids:
            return False
        return Organization.objects.filter(id__in=org_ids, admins=user.profile).exists()

    def is_viewable_by_course_teacher(self, user):
        """Course admins, instructors, and class instructors for linked courses."""
        if not user.is_authenticated:
            return False
        return self._memo('course_teacher', user, lambda: self._is_viewable_by_course_teacher(user))

    def _is_viewable_by_course_teacher(self, user):
        org_ids = self.linked_organization_ids()
        if not org_ids:
            return False
        profile = user.profile
        if Organization.objects.filter(id__in=org_ids, instructors=profile).exists():
            return True
        if Organization.objects.filter(id__in=org_ids, admins=profile).exists():
            return True
        return Class.objects.filter(organization_id__in=org_ids, admins=profile).exists()

    def is_general_course_assignment(self):
        """Public assignment with no course/class scope (Assignments list "General course")."""
        if not self.is_visible or self.is_private or self.is_organization_private:
            return False
        return not self.linked_organization_ids()

    def is_cloneable_by_site_teacher(self, user):
        if not user.is_authenticated or not self.is_general_course_assignment():
            return False
        if user.has_perm('judge.add_organization'):
            return True
        from judge.models.notification import user_in_instructors_group
        return user_in_instructors_group(user)

    def is_cloneable_by(self, user):
        """Match the Django ``clone_contest`` permission.

        ContestAdmin has no Clone action; this is the permission that appears
        on the Contest model's auth list and that original DMOJ's clone view
        required. Source-contest access is still enforced by ContestMixin.
        """
        return bool(user.is_authenticated and user.has_perm('judge.clone_contest'))

    def is_deletable_by(self, user):
        """Match Django admin contest delete.

        ``has_delete_permission`` is ``delete_contest``; opening the object in
        admin also requires change permission (``edit_own_contest`` plus
        ``is_editable_by``). Admin still disables *batch* delete via
        ``NoBatchDeleteMixin``; the frontend list may bulk-delete but each
        row is re-checked here.
        """
        if not user.is_authenticated or not user.has_perm('judge.delete_contest'):
            return False
        return self.is_changeable_by(user)

    @classmethod
    def get_visible_contests(cls, user):
        if not user.is_authenticated:
            return cls.objects.filter(is_visible=True, is_organization_private=False, is_private=False) \
                              .defer('description').distinct()

        queryset = cls.objects.defer('description')
        if not (user.has_perm('judge.see_private_contest') or user.has_perm('judge.edit_all_contest')):
            # Every role test is an EXISTS on its own link table. Joining all of them at once
            # (Q(authors=...) | Q(classes__in=...) | ...) made MariaDB build the product of every
            # contest's authors, curators, classes and courses: 3+ s per page at 1,500 assignments.
            profile = user.profile
            contest_orgs = Contest.organizations.through.objects.filter(contest=OuterRef('pk'))
            contest_classes = Contest.classes.through.objects.filter(contest=OuterRef('pk'))

            def linked(through):
                return Exists(through.objects.filter(contest=OuterRef('pk'), profile=profile))

            user_orgs = Profile.organizations.through.objects.filter(profile=profile).values('organization_id')
            user_classes = Class.members.through.objects.filter(profile=profile).values('class_id')
            admin_orgs = Organization.admins.through.objects.filter(profile=profile).values('organization_id')
            teaching_orgs = Organization.objects.filter(teaching_q(profile)).values('id')
            private_contestant = linked(Contest.private_contestants.through)
            # For org-private contests:
            # - If classes are set, only class members can see
            # - If no classes, org members can see
            org_only_check = Q(is_organization_private=True) & ~Exists(contest_classes) & \
                Exists(contest_orgs.filter(organization_id__in=user_orgs))
            class_check = Q(is_organization_private=True) & Exists(contest_classes.filter(class_id__in=user_classes))
            org_check = org_only_check | class_check
            q = Q(is_visible=True)
            q &= (
                linked(Contest.view_contest_scoreboard.through) |
                Q(is_organization_private=False, is_private=False) |
                (Q(is_organization_private=False, is_private=True) & private_contestant) |
                (Q(is_organization_private=True, is_private=False) & org_check) |
                (Q(is_organization_private=True, is_private=True) & private_contestant & org_check)
            )

            q |= linked(Contest.authors.through)
            q |= linked(Contest.curators.through)
            q |= linked(Contest.testers.through)
            q |= linked(Contest.spectators.through)
            # Org admins can see org-private contests
            q |= Q(is_organization_private=True) & Exists(contest_orgs.filter(organization_id__in=admin_orgs))
            # Hidden (unpublished) assignments: course admins only, see access_check.
            q |= Q(is_organization_private=True, is_visible=True) & \
                Exists(contest_orgs.filter(organization_id__in=teaching_orgs))
            q |= Q(is_organization_private=True, is_visible=True) & \
                Exists(contest_classes.filter(class__organization_id__in=teaching_orgs))
            q |= Q(is_organization_private=True) & \
                Exists(contest_classes.filter(class__organization_id__in=admin_orgs))
            queryset = queryset.filter(q)
        return queryset.distinct()

    def rate(self):
        with transaction.atomic():
            Rating.objects.filter(contest__end_time__range=(self.end_time, self._now)).delete()
            for contest in Contest.objects.filter(
                is_rated=True, end_time__range=(self.end_time, self._now),
            ).order_by('end_time'):
                rate_contest(contest)

    class Meta:
        permissions = (
            ('see_private_contest', _('See private contests')),
            ('edit_own_contest', _('Edit own contests')),
            ('edit_all_contest', _('Edit all contests')),
            ('clone_contest', _('Clone contest')),
            ('moss_contest', _('MOSS contest')),
            ('contest_rating', _('Rate contests')),
            ('contest_access_code', _('Contest access codes')),
            ('create_private_contest', _('Create private contests')),
            ('change_contest_visibility', _('Change contest visibility')),
            ('contest_problem_label', _('Edit contest problem label script')),
            ('lock_contest', _('Change lock status of contest')),
            ('override_performance_ceiling', _('Override performance ceiling of contest')),
        )
        verbose_name = _('contest')
        verbose_name_plural = _('contests')


class ContestParticipation(models.Model):
    LIVE = 0
    SPECTATE = -1

    contest = models.ForeignKey(Contest, verbose_name=_('associated contest'), related_name='users', on_delete=CASCADE)
    user = models.ForeignKey(Profile, verbose_name=_('user'), related_name='contest_history', on_delete=CASCADE)
    real_start = models.DateTimeField(verbose_name=_('start time'), default=timezone.now, db_column='start')
    score = models.FloatField(verbose_name=_('score'), default=0, db_index=True)
    cumtime = models.PositiveIntegerField(verbose_name=_('cumulative time'), default=0)
    is_disqualified = models.BooleanField(verbose_name=_('is disqualified'), default=False,
                                          help_text=_('Whether this participation is disqualified.'))
    tiebreaker = models.FloatField(verbose_name=_('tie-breaking field'), default=0.0)
    virtual = models.IntegerField(verbose_name=_('virtual participation id'), default=LIVE,
                                  help_text=_('0 means non-virtual, otherwise the n-th virtual participation.'))
    format_data = JSONField(verbose_name=_('contest format specific data'), null=True, blank=True)
    harvest_scheduled_task_id = models.CharField(
        verbose_name=_('scheduled auto-submit task id'), max_length=255, blank=True, default='',
        help_text=_('Celery task that auto-submits this student’s saved answers when their own '
                    'deadline arrives. Used only when the assignment has a personal time limit.'),
    )

    def recompute_results(self):
        with transaction.atomic():
            # One recompute per participation at a time. Grading saves, Release All and the judge
            # recompute the same student concurrently; unserialized, a recompute that read the
            # submission points before another grade landed could write its stale total last.
            # Holding the row lock, each one reads what the previous one committed.
            list(ContestParticipation.objects.select_for_update().filter(pk=self.pk).values_list('pk', flat=True))
            # The formats save the whole row: take the column that other code sets on its own
            # from the database, so it is not written back stale.
            if self.pk is not None:
                self.refresh_from_db(fields=['harvest_scheduled_task_id'])
            self.contest.format.update_participation(self)
            if self.is_disqualified:
                self.score = -9999
                self.cumtime = 0
                self.tiebreaker = 0
                self.save(update_fields=['score', 'cumtime', 'tiebreaker'])
    recompute_results.alters_data = True

    def set_disqualified(self, disqualified):
        self.is_disqualified = disqualified
        self.recompute_results()
        if self.contest.is_rated and self.contest.ratings.exists():
            self.contest.rate()
        if self.is_disqualified:
            if self.user.current_contest == self:
                self.user.remove_contest()
            self.contest.banned_users.add(self.user)
        else:
            self.contest.banned_users.remove(self.user)
    set_disqualified.alters_data = True

    @property
    def live(self):
        return self.virtual == self.LIVE

    @property
    def spectate(self):
        return self.virtual == self.SPECTATE

    @cached_property
    def start(self):
        contest = self.contest
        return contest.start_time if contest.time_limit is None and (self.live or self.spectate) else self.real_start

    @cached_property
    def end_time(self):
        contest = self.contest
        if self.spectate:
            return contest.end_time
        if self.virtual:
            if contest.time_limit:
                return self.real_start + contest.time_limit
            else:
                return self.real_start + (contest.end_time - contest.start_time)
        return contest.end_time if contest.time_limit is None else \
            min(self.real_start + contest.time_limit, contest.end_time)

    @cached_property
    def _now(self):
        # This ensures that all methods talk about the same now.
        return timezone.now()

    @property
    def ended(self):
        return self.end_time is not None and self.end_time < self._now

    @property
    def time_remaining(self):
        end = self.end_time
        if end is not None and end >= self._now:
            return end - self._now

    def __str__(self):
        if self.spectate:
            return _('%(user)s spectating in %(contest)s') % {'user': self.user.username, 'contest': self.contest.name}
        if self.virtual:
            return _('%(user)s in %(contest)s, v%(id)d') % {
                'user': self.user.username, 'contest': self.contest.name, 'id': self.virtual,
            }
        return _('%(user)s in %(contest)s') % {'user': self.user.username, 'contest': self.contest.name}

    class Meta:
        verbose_name = _('contest participation')
        verbose_name_plural = _('contest participations')

        unique_together = ('contest', 'user', 'virtual')


class ContestProblem(models.Model):
    problem = models.ForeignKey(Problem, verbose_name=_('problem'), related_name='contests', on_delete=CASCADE)
    contest = models.ForeignKey(Contest, verbose_name=_('contest'), related_name='contest_problems', on_delete=CASCADE)
    points = models.IntegerField(verbose_name=_('points'))
    partial = models.BooleanField(default=True, verbose_name=_('partial'))
    is_pretested = models.BooleanField(default=False, verbose_name=_('is pretested'))
    order = models.PositiveIntegerField(db_index=True, verbose_name=_('order'))
    output_prefix_override = models.IntegerField(verbose_name=_('output prefix length override'),
                                                 default=0, null=True, blank=True)
    max_submissions = models.IntegerField(verbose_name=_('max submissions'),
                                          help_text=_('Maximum number of submissions for this problem, '
                                                      'or leave blank for no limit.'),
                                          default=None, null=True, blank=True,
                                          validators=[MinValueOrNoneValidator(1, _('Why include a problem you '
                                                                                   "can't submit to?"))])
    hide_results = models.BooleanField(verbose_name=_('hide results in contest'),
                                       help_text=_("Whether to hide this problem's results from students until the "
                                                   'contest is finalized. Leave unset to use the default for the '
                                                   'question type (hidden for multiple-choice, shown for code). Only '
                                                   'applies to exam-style contests.'),
                                       default=None, null=True, blank=True)
    display_name = models.CharField(max_length=100, blank=True, default='',
                                    verbose_name=_('display name'))

    @property
    def results_hidden(self):
        """Whether this problem's results are hidden during the contest (override, else type default)."""
        if self.hide_results is not None:
            return self.hide_results
        from judge.question_types import get_grader
        return get_grader(self.problem.submission_type).hide_results_in_contest

    @property
    def score_uses_latest(self):
        """Whether scoring uses the latest (vs best) submission for this problem (type policy)."""
        from judge.question_types import get_grader
        return get_grader(self.problem.submission_type).contest_score_uses_latest

    class Meta:
        unique_together = ('problem', 'contest')
        verbose_name = _('contest problem')
        verbose_name_plural = _('contest problems')
        ordering = ('order',)


def contest_problem_display_name(cp, language_code=None) -> str:
    """Assignment-local title for a contest problem.

    A non-empty ``display_name`` wins over the Problem's i18n translation and
    canonical name. Empty string means "use the original Problem name".
    """
    if cp.display_name:
        return cp.display_name
    if language_code:
        return cp.problem.translated_name(language_code)
    return cp.problem.name


class ContestSubmission(models.Model):
    submission = models.OneToOneField(Submission, verbose_name=_('submission'),
                                      related_name='contest', on_delete=CASCADE)
    problem = models.ForeignKey(ContestProblem, verbose_name=_('problem'), on_delete=CASCADE,
                                related_name='submissions', related_query_name='submission')
    participation = models.ForeignKey(ContestParticipation, verbose_name=_('participation'), on_delete=CASCADE,
                                      related_name='submissions', related_query_name='submission')
    points = models.FloatField(default=0.0, verbose_name=_('points'))
    is_pretest = models.BooleanField(verbose_name=_('is pretested'),
                                     help_text=_('Whether this submission was ran only on pretests.'),
                                     default=False)

    class Meta:
        verbose_name = _('contest submission')
        verbose_name_plural = _('contest submissions')


class Rating(models.Model):
    user = models.ForeignKey(Profile, verbose_name=_('user'), related_name='ratings', on_delete=CASCADE)
    contest = models.ForeignKey(Contest, verbose_name=_('contest'), related_name='ratings', on_delete=CASCADE)
    participation = models.OneToOneField(ContestParticipation, verbose_name=_('participation'),
                                         related_name='rating', on_delete=CASCADE)
    rank = models.IntegerField(verbose_name=_('rank'))
    rating = models.IntegerField(verbose_name=_('rating'))
    mean = models.FloatField(verbose_name=_('raw rating'))
    performance = models.FloatField(verbose_name=_('contest performance'))
    last_rated = models.DateTimeField(db_index=True, verbose_name=_('last rated'))

    class Meta:
        unique_together = ('user', 'contest')
        verbose_name = _('contest rating')
        verbose_name_plural = _('contest ratings')


class ContestMoss(models.Model):
    LANG_MAPPING = [
        ('C', MOSS_LANG_C),
        ('C++', MOSS_LANG_CC),
        ('Java', MOSS_LANG_JAVA),
        ('Python', MOSS_LANG_PYTHON),
    ]

    contest = models.ForeignKey(Contest, verbose_name=_('contest'), related_name='moss', on_delete=CASCADE)
    problem = models.ForeignKey(Problem, verbose_name=_('problem'), related_name='moss', on_delete=CASCADE)
    language = models.CharField(max_length=10)
    submission_count = models.PositiveIntegerField(default=0)
    url = models.URLField(null=True, blank=True)

    class Meta:
        unique_together = ('contest', 'problem', 'language')
        verbose_name = _('contest moss result')
        verbose_name_plural = _('contest moss results')
