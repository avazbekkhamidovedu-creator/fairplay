import copy
import json
import time
from datetime import timedelta

import httpx
import pytest
from sqlalchemy import select

from app.integrations.dkuoj import client as ojclient, signing, sync
from app.integrations.dkuoj.isolation import BINDING, audience_valid
from app.integrations.dkuoj.models import OjState
from app.integrations.dkuoj.source import SOURCE
from app.models import Attempt, ClassMember, Exam, SchoolClass, User
from app import app_client as ac
from app.security import hash_password
from app.timeutil import iso, utcnow

from .conftest import login
from .test_dkuoj_bridge import INSTALLATION_ID, KEY, db, oj_exam, run
from .test_dkuoj_bridge_v2 import V1, call, fake, oj  # noqa: F401


def test_former_owner_loses_exam_access_when_all_editors_are_removed(oj, fake):
    run(oj)
    teacher = login(oj, 'teacher')
    eid = oj_exam(oj, teacher)['id']
    fake.editors = []
    run(oj)
    assert oj.get(f'/api/exams/{eid}', headers=teacher).status_code == 404
    assert eid not in [e['id'] for e in oj.get('/api/exams', headers=teacher).json()]
    assert db(oj, lambda s: s.scalar(select(Exam.id).where(Exam.id == eid))) == eid
    native = oj.post('/api/classes', json={'name': 'Native class'}, headers=teacher).json()
    added = oj.post(f"/api/classes/{native['id']}/members", json={'usernames': ['ax19']}, headers=teacher)
    assert added.status_code == 200 and added.json()['unknown'] == ['ax19']
    assert oj.get('/api/topics', headers=teacher).json() == []
    assert oj.get('/api/grading', headers=teacher).json() == []
    gradebook = oj.get('/api/gradebook', headers=teacher)
    assert gradebook.status_code == 200 and gradebook.json()['exams'] == []


def test_external_mirrors_are_internal_and_do_not_expand_teacher_scope(oj):
    run(oj)
    teacher = login(oj, 'teacher')

    async def mirror(s):
        row = (await s.execute(select(SchoolClass).where(SchoolClass.external_ref == 'dkuoj:31'))).scalar_one()
        count = len((await s.execute(select(ClassMember.id).where(ClassMember.class_id == row.id))).all())
        return row.id, row.join_code, count
    cid, code, count = db(oj, mirror)
    assert count == 0
    assert cid not in [c['id'] for c in oj.get('/api/classes', headers=teacher).json()]
    assert oj.get(f'/api/classes/{cid}', headers=teacher).status_code == 404
    assert oj.put(f'/api/classes/{cid}', json={'name': 'Take over'}, headers=teacher).status_code == 404
    student = login(oj, 'alice')
    assert oj.post('/api/student/classes/join', json={'code': code}, headers=student).status_code == 404


@pytest.mark.parametrize('audience', [None, {'mode': 'classes', 'confirmed': False, 'class_ids': [31],
    'organization_ids': [], 'selected_only': False}, {'mode': 'classes', 'confirmed': True, 'class_ids': [],
    'organization_ids': [12], 'selected_only': False}])
def test_missing_unconfirmed_or_empty_class_audience_does_not_expand_membership(oj, fake, audience):
    fake.audience = audience
    run(oj)
    teacher = login(oj, 'teacher')
    e = oj_exam(oj, teacher)
    student = login(oj, 'alice')
    response = oj.post(f"/api/student/exams/{e['id']}/start", json={'start_code': '1234'}, headers=student)
    assert response.status_code in (403, 409)
    assert not audience_valid({'audience': audience})


def test_removed_frozen_exam_allows_only_the_existing_students_resume(oj, fake):
    run(oj)
    teacher = login(oj, 'teacher')
    e = oj_exam(oj, teacher)
    opened = oj.post(f"/api/exams/{e['id']}/open", headers=teacher)
    assert opened.status_code == 200, opened.text
    code = opened.json()['start_code']
    alice, carol = login(oj, 'alice'), login(oj, 'carol')
    path = f"/api/student/exams/{e['id']}/start"
    first = oj.post(path, json={'start_code': code}, headers=alice)
    assert first.status_code == 200, first.text
    fake.removed = True
    run(oj)
    denied = oj.post(path, json={'start_code': code}, headers=carol)
    assert denied.status_code in (403, 409), denied.text
    resumed = oj.post(path, json={'start_code': code}, headers=alice)
    assert resumed.status_code == 200, resumed.text
    assert resumed.json()['attempt_id'] == first.json()['attempt_id']
    assert db(oj, lambda s: s.scalar(select(Attempt.id).where(Attempt.id == first.json()['attempt_id'])))


@pytest.mark.parametrize('reconfigure', [False, True])
def test_installation_mismatch_blocks_sync_and_bridge_mutations(oj, fake, reconfigure):
    run(oj)
    fake.installation_id = '59acb67c-e008-4e0b-80c0-c9ae8e69299f'
    if reconfigure:
        SOURCE.config.installation_id = fake.installation_id
    result = run(oj)
    assert 'installation' in result and 'error' in result['installation']
    response = call(oj, 'POST', f'{V1}/sync', {'exam_key': KEY})
    assert response.status_code in (409, 503)
    async def pin(s):
        return json.loads((await s.get(OjState, BINDING)).value)['installation_id']
    assert db(oj, pin) == INSTALLATION_ID


def test_origin_change_needs_explicit_verified_alias(oj):
    run(oj)
    original = SOURCE.config.url
    SOURCE.config.url = 'https://other-oj.test'
    try:
        assert 'installation' in run(oj)
        SOURCE.config.verified_url_aliases = ['https://other-oj.test']
        assert 'installation' not in run(oj)
        async def binding(s):
            return json.loads((await s.get(OjState, BINDING)).value)
        assert db(oj, binding)['url'] == original
    finally:
        SOURCE.config.url = original
        SOURCE.config.verified_url_aliases = []


def test_legacy_data_requires_explicit_operator_binding_confirmation(oj):
    SOURCE.config.confirm_existing_installation_id = ''
    result = run(oj)
    assert 'DKUOJ_CONFIRM_EXISTING_INSTALLATION_ID' in result['installation']['error']
    SOURCE.config.confirm_existing_installation_id = INSTALLATION_ID
    assert 'installation' not in run(oj)


def test_unsigned_authority_response_does_not_remove_existing_exams(oj, fake):
    run(oj)
    def unsigned(req):
        if req.url.path.endswith('/exams'):
            return httpx.Response(200, json={'exams': [], 'active_keys': [], 'active_since': '2000-01-01T00:00:00Z'})
        return fake.signed_handle(req)
    ojclient.set_transport_factory(lambda base: httpx.MockTransport(unsigned))
    result = run(oj)
    assert 'error' in result['exams']
    async def removed(s):
        return (await s.execute(select(Exam.external_removed).where(Exam.source == 'dkuoj'))).scalars().all()
    assert db(oj, removed) == [False]


@pytest.mark.parametrize('tamper', ['status', 'body', 'request_nonce'])
def test_authority_response_signature_binds_status_body_and_request_nonce(oj, fake, tamper):
    run(oj)
    def altered(req):
        response = fake.signed_handle(req)
        if req.url.path.endswith('/exams'):
            if tamper == 'status':
                response.status_code = 404
            elif tamper == 'body':
                response = httpx.Response(200, content=b'{"exams":[]}', headers=response.headers)
            else:
                response.headers[signing.H_ACTING] = '0' * 32
        return response
    ojclient.set_transport_factory(lambda base: httpx.MockTransport(altered))
    assert 'error' in run(oj)['exams']


def test_authoritative_missing_target_revokes_authority_but_preserves_history(oj, fake, monkeypatch):
    run(oj)
    teacher = login(oj, 'teacher')
    eid = oj_exam(oj, teacher)['id']
    original = fake.handle
    def missing(req):
        if req.url.path.endswith('/exams'):
            return httpx.Response(200, json={'exams': [], 'next_page': None})
        return original(req)
    monkeypatch.setattr(fake, 'handle', missing)
    async def reconcile():
        from app.db import get_database
        async with ojclient.DkuojClient.from_config(SOURCE.config) as client:
            return await sync.sync_exams(get_database().sessionmaker, client, SOURCE.config,
                                         utcnow(), full=True, targets={KEY})
    oj.portal.call(reconcile)
    assert oj.get(f'/api/exams/{eid}', headers=teacher).status_code == 404
    assert db(oj, lambda s: s.scalar(select(Exam.external_removed).where(Exam.id == eid))) is True


def test_two_teachers_two_classes_and_shared_student_do_not_share_edit_authority(oj, fake):
    fake.editors = fake.editors[:1]
    run(oj)
    teacher = login(oj, 'teacher')
    first = oj_exam(oj, teacher)['id']
    item = copy.deepcopy(fake.listing())
    item.update(key='other-exam', classes=[{'id': 32, 'name': 'Other class'}],
                editors=[{'username': 'other-teacher', 'first_name': 'Other', 'last_name': 'Teacher'}])
    item['audience']['class_ids'] = [32]
    async def add(s):
        s.add(User(username='other-teacher', netid='other-teacher', name='Other Teacher', role='teacher',
                   password_hash=hash_password('test-only-password', 1000)))
        await s.flush()
        e = await sync.apply_listing(s, item, SOURCE.config, utcnow())
        await s.commit()
        return e.id
    second = db(oj, add)
    other = login(oj, 'other-teacher', 'test-only-password')
    assert oj.get(f'/api/exams/{second}', headers=teacher).status_code == 404
    assert oj.get(f'/api/exams/{first}', headers=other).status_code == 404
    assert [e['id'] for e in oj.get('/api/exams', headers=other).json()] == [second]


def test_targeted_reconcile_keeps_other_exams_and_the_global_cursor_unchanged(oj, fake):
    run(oj)
    teacher = login(oj, 'teacher')
    eid = oj_exam(oj, teacher)['id']
    async def cursor(s):
        return await sync.get_state(s, sync.CURSOR)
    before = db(oj, cursor)
    fake.editors = []
    fake.listing_updated = iso(utcnow() + timedelta(seconds=1))
    async def reconcile():
        from app.db import get_database
        async with ojclient.DkuojClient.from_config(SOURCE.config) as client:
            await sync.sync_exams(get_database().sessionmaker, client, SOURCE.config, utcnow(),
                                   guard=False, full=True, targets={'missing-other-exam'})
    oj.portal.call(reconcile)
    assert oj.get(f'/api/exams/{eid}', headers=teacher).status_code == 200
    assert db(oj, cursor) == before


@pytest.mark.parametrize('remove_exam_afterward', [False, True])
def test_individually_revoked_active_student_cannot_resume_cached_session(oj, fake, remove_exam_afterward):
    run(oj)
    teacher, alice = login(oj, 'teacher'), login(oj, 'alice')
    eid = oj_exam(oj, teacher)['id']
    opened = oj.post(f'/api/exams/{eid}/open', headers=teacher).json()
    device, key = 'ISOLATION-DEVICE-A', 'test-app-key'
    def proof():
        nonce = oj.get(f'/api/student/exams/{eid}/challenge', headers=alice).json()['nonce']
        return {'device_id': device, 'nonce': nonce, 'version': '0.7.8',
                'signature': ac.sign(key, ac.start_message(nonce, device))}
    first = oj.post(f'/api/student/exams/{eid}/start',
                    json={'start_code': opened['start_code'], 'client': proof()}, headers=alice)
    assert first.status_code == 200, first.text
    attempt = first.json()
    aid, session = attempt['attempt_id'], attempt['session']
    headers = {**alice, ac.SESSION_HEADER: session}
    answer = {attempt['questions'][0]['id']: 1}
    assert oj.put(f'/api/student/attempts/{aid}/answers',
                  json={'answers': answer, 'client_seq': 1}, headers=headers).status_code == 200
    stamp = int(time.time() * 1000)
    def beat(ts):
        checks = 'vm=0,displays=1,tap=1,input=1'
        return {'session': session, 'device_id': device, 'ts': ts, 'locked': True, 'checks': checks,
                'signature': ac.sign(key, ac.beat_message(aid, session, device, ts, True, checks))}
    assert oj.post(f'/api/student/attempts/{aid}/app-beat', json=beat(stamp), headers=alice).status_code == 200
    resume_proof = proof()
    fake.roster = [p for p in fake.roster if p['username'] != 'alice']
    run(oj)
    if remove_exam_afterward:
        fake.removed = True
        run(oj)
    denied = oj.post(f'/api/student/exams/{eid}/start',
                    json={'start_code': opened['start_code'], 'client': resume_proof}, headers=alice)
    assert denied.status_code in (403, 404, 409), denied.text
    assert oj.get(f'/api/student/exams/{eid}/challenge', headers=alice).status_code in (403, 404)
    assert eid not in [e['id'] for e in oj.get('/api/student/exams', headers=alice).json()]
    assert oj.get(f'/api/student/attempts/{aid}', headers=headers).status_code in (403, 404, 409)
    assert oj.put(f'/api/student/attempts/{aid}/answers', json={'answers': {}, 'client_seq': 2},
                  headers=headers).status_code in (403, 404, 409)
    assert oj.post(f'/api/student/attempts/{aid}/app-beat', json=beat(stamp + 1),
                   headers=alice).status_code in (403, 404, 409)
    async def saved(s):
        row = await s.get(Attempt, aid)
        return row.status, row.answers, row.session_id
    assert db(oj, saved) == ('in_progress', answer, session)
