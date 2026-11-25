from datetime import timedelta

import pytest
from pydantic import ValidationError

from runtime.investigations.evidence import collect
from runtime.investigations.evaluate import evaluate
from runtime.investigations.models import InvestigationSpec
from runtime.investigations.plans import build_plan, reuse_checkpoints, collection_key
from runtime.scheduler.scheduler import run_workflow
from runtime.state import repository
from runtime.state.orm import utcnow


def run_investigation(session, workflow_id, spec, parent=None):
    tasks = build_plan(workflow_id, spec, parent_id=parent.id if parent else None)
    repository.create_workflow(session, workflow_id, 'investigate')
    repository.save_tasks(session, tasks)
    if parent:
        reuse_checkpoints(session, tasks, parent.tasks)
    run_workflow(session, workflow_id)
    return repository.get_workflow(session, workflow_id)


def report(workflow):
    return next(t.output for t in workflow.tasks if t.input['tool']=='investigation.assemble')


def test_requirement_change_reuses_collection_but_recomputes_findings(db_session):
    original = run_investigation(db_session, 'original', InvestigationSpec())
    assert original.status.value == 'COMPLETED'
    revised = run_investigation(db_session, 'revised', InvestigationSpec(requirements=['http2']), original)
    assert revised.status.value == 'COMPLETED'
    reused = [t for t in revised.tasks if t.input.get('reused_from')]
    assert len(reused) == 6
    assert all(t.fencing_token == 0 for t in reused)  # no worker execution
    assert all(t.fencing_token == 1 for t in revised.tasks if t not in reused)
    assert report(original)['requirements'] == ['async_client', 'permissive_license']
    assert report(revised)['requirements'] == ['http2']
    assert report(revised)['candidates_meeting_all_requirements'] == ['encode/httpx']
    assert all(f['citations'] for e in report(revised)['evaluations'] for f in e['findings'] if f['status']=='met')


def findings_for(text):
    metadata = collect({'repository':'encode/httpx','source':'snapshot','kind':'metadata'})
    document = collect({'repository':'encode/httpx','source':'snapshot','kind':'documentation'})
    document['payload']['text'] = text
    return evaluate({'repository':'encode/httpx','source':'snapshot','requirements':['http2'],
                     'dependency_outputs': {'m':metadata,'d':document}})['findings'][0]


@pytest.mark.parametrize('text,status', [
    ('A popular HTTP library.', 'not_established'),
    ('Supports HTTP/2.', 'met'),
    ('Does not support HTTP/2.', 'not_met'),
    ('No HTTP/2 support.', 'not_met'),
    ('Supports HTTP/2.\nDoes not support HTTP/2.', 'not_established'),
])
def test_conservative_findings_and_negation(text, status):
    finding = findings_for(text)
    assert finding['status'] == status
    assert all(c['excerpt'] in text for c in finding['citations'])


def test_invalid_and_duplicate_specifications_are_rejected():
    for data in ({'requirements':['magic']}, {'requirements':['http2','http2']}, {'candidates':['evil/repo']}):
        with pytest.raises(ValidationError):
            InvestigationSpec(**data)


def test_source_switch_does_not_reuse_offline_evidence(db_session):
    original = run_investigation(db_session, 'offline', InvestigationSpec())
    tasks = build_plan('live', InvestigationSpec(source='live'))
    repository.create_workflow(db_session,'live','live')
    repository.save_tasks(db_session,tasks)
    assert reuse_checkpoints(db_session,tasks,original.tasks) == 0


def test_expired_live_evidence_is_not_reused(db_session):
    original = run_investigation(db_session,'parent',InvestigationSpec())
    now = utcnow()
    for task in original.tasks:
        if task.input['tool'] != 'investigation.collect':
            continue
        args = task.input['args']
        task.input['collection_key'] = collection_key(args['repository'], 'live', args['kind'])
        task.output['source'] = 'live'
        task.output['collected_at'] = (now-timedelta(minutes=16)).isoformat()
    tasks = build_plan('later',InvestigationSpec(source='live'))
    repository.create_workflow(db_session,'later','later')
    repository.save_tasks(db_session,tasks)
    assert reuse_checkpoints(db_session,tasks,original.tasks,now=now) == 0


def test_tampered_evidence_is_not_reused(db_session):
    original = run_investigation(db_session,'parent',InvestigationSpec())
    first = original.tasks[0]
    first.output['payload']['license'] = 'tampered'
    tasks = build_plan('revision',InvestigationSpec())
    repository.create_workflow(db_session,'revision','revision')
    repository.save_tasks(db_session,tasks)
    assert reuse_checkpoints(db_session,tasks,original.tasks) == 5


def test_document_404_is_missing_evidence_not_a_negative_claim(monkeypatch):
    import runtime.investigations.evidence as evidence_module
    from runtime.workloads.repositories import GitHubResourceNotFound
    def missing(path):
        raise GitHubResourceNotFound('not found')
    monkeypatch.setattr(evidence_module, '_get', missing)
    result = collect({'repository':'encode/httpx','source':'live','kind':'documentation'})
    assert result['payload']['available'] is False
    assert result['payload']['text'] == ''


def test_other_collection_failures_are_not_disguised_as_missing_evidence(monkeypatch):
    import runtime.investigations.evidence as evidence_module
    def failed(path):
        raise RuntimeError('rate limit')
    monkeypatch.setattr(evidence_module, '_get', failed)
    with pytest.raises(RuntimeError, match='rate limit'):
        collect({'repository':'encode/httpx','source':'live','kind':'documentation'})
