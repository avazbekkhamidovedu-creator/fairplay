from django.urls import reverse

from judge.models import ContestParticipation
from judge.utils.fairplay import fairplay_exams, fairplay_hidden_for
from judge.views.fairplay_launch import student_can_launch


def discovery_context(request, now, filter_rows=False):
    rows, ids = [], []
    if not request.user.is_authenticated:
        return {'fairplay_discovery': rows, 'fairplay_discovery_ids': ids}
    from judge.views.problem import user_can_see_problem_author_metadata
    can_see_authors = user_can_see_problem_author_metadata(request.user)
    exams = list(fairplay_exams().filter(is_visible=True).prefetch_related(
        'tags', 'organizations', 'classes', 'authors'))
    participations = {}
    for participation in ContestParticipation.objects.filter(
            contest__in=exams, user=request.profile, virtual=ContestParticipation.LIVE
    ).select_related('contest').order_by('pk'):
        participations.setdefault(participation.contest_id, participation)
    for exam in exams:
        if not fairplay_hidden_for(request.user, exam) or not student_can_launch(request, exam):
            continue
        ids.append(exam.pk)
        status = ('upcoming' if exam.start_time > now else
                  'past' if exam.end_time is not None and exam.end_time < now else 'ongoing')
        participation = participations.get(exam.pk)
        if participation is not None and not participation.ended:
            status = 'active'
        row = {'key': exam.key, 'name': exam.name, 'start': exam.start_time, 'end': exam.end_time,
               'url': reverse('contest_fairplay_launch', args=[exam.key]), 'status': status,
               'orgs': [organization.name for organization in exam.organizations.all()],
               'classes': [klass.name for klass in exam.classes.all()],
               'tags': [tag.name for tag in exam.tags.all() if not tag.name.startswith('fp-aud-')],
               'contest': exam,
               'authors': [author.id for author in exam.authors.all()] if can_see_authors else []}
        if filter_rows and not _matches(request.GET, row, can_see_authors):
            continue
        rows.append(row)
    return {'fairplay_discovery': rows, 'fairplay_discovery_ids': ids}


def _matches(params, row, can_see_authors):
    for param, field in (('org', 'orgs'), ('class', 'classes'), ('tag', 'tags'), ('author', 'authors')):
        if param == 'author' and not can_see_authors:
            continue
        chosen = {part.strip() for raw in params.getlist(param) for part in raw.split(',') if part.strip()}
        available = {str(value) for value in row[field]}
        if chosen and (not chosen.issubset(available) if param == 'tag' else not chosen & available):
            return False
    if params.get('time') in ('upcoming', 'ongoing', 'active', 'past') and params['time'] != row['status']:
        return False
    haystack = (row['name'] + ' ' + row['key']).lower()
    return all(term.strip().lower() in haystack for term in params.getlist('search'))
