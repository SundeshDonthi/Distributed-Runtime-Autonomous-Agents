from __future__ import annotations

import pytest

from runtime.models.workflow import WorkflowStatus
from runtime.scheduler.scheduler import run_workflow
from runtime.state import repository
from runtime.workloads.repositories import REPOSITORIES, build_plan, fetch_repository, validate_repo


@pytest.mark.parametrize("recover", [False, True])
def test_report_consumes_durable_evidence_and_replans_without_repeating_collection(db_session, recover):
    workflow_id = f"wf_repository_{recover}"
    repository.create_workflow(db_session, workflow_id, "compare")
    repository.save_tasks(db_session, build_plan(workflow_id, "snapshot", recover))
    assert run_workflow(db_session, workflow_id) == WorkflowStatus.COMPLETED
    workflow = repository.get_workflow(db_session, workflow_id)
    report_task = next(t for t in workflow.tasks if t.id.endswith('_report'))
    assert {r['repository'] for r in report_task.output['repositories']} == set(REPOSITORIES)
    assert report_task.output['source'] == 'snapshot'
    assert workflow.plan_version == (2 if recover else 1)
    assert len(workflow.tasks) == (5 if recover else 4)
    assert report_task.replan_count == int(recover)
    for task in workflow.tasks:
        if task.input.get('tool') == 'github.repository':
            assert task.fencing_token == 1  # collection executed once
            assert task.retry_count == 0
            assert task.output['stars'] is None
    if recover:
        assert len(report_task.output['additional_evidence']) == 1
        assert repository.get_replan_events(db_session, workflow_id)


@pytest.mark.parametrize('repo', ['../x', 'https://evil.test/x', 'owner/name/extra', 'owner/..'])
def test_repository_identifiers_cannot_be_urls_or_traversals(repo):
    with pytest.raises(ValueError):
        validate_repo(repo)


def test_live_metadata_uses_actual_transport_response(monkeypatch):
    import httpx
    import runtime.workloads.repositories as workload

    calls = []
    def respond(request):
        calls.append(str(request.url))
        return httpx.Response(200, json={'description':'Real fixture', 'stargazers_count':12, 'forks_count':2})
    real_client = httpx.Client
    monkeypatch.setattr(workload.httpx, 'Client', lambda **kw: real_client(transport=httpx.MockTransport(respond), **kw))
    result = fetch_repository({'repository':'encode/httpx', 'source':'live'})
    assert calls == ['https://api.github.com/repos/encode/httpx']
    assert result['stars'] == 12
    assert result['source'] == 'live'


def test_live_failure_never_silently_substitutes_snapshot(monkeypatch):
    import runtime.workloads.repositories as workload
    def fail(path):
        raise RuntimeError('rate limit')
    monkeypatch.setattr(workload, '_get', fail)
    with pytest.raises(RuntimeError, match='rate limit'):
        fetch_repository({'repository':'encode/httpx', 'source':'live'})


def test_sqlite_workflow_timestamps_retain_utc_for_browser_elapsed_time(db_session):
    from datetime import timezone
    repository.create_workflow(db_session, "clock_test", "clock")
    db_session.commit()
    db_session.expire_all()
    workflow = repository.get_workflow(db_session, "clock_test")
    assert workflow.created_at.utcoffset() == timezone.utc.utcoffset(None)
