"""Build new DAGs and copy only compatible, fresh collection checkpoints."""
import hashlib
import json
from datetime import datetime, timedelta, timezone

from runtime.investigations.evidence import COLLECTOR_VERSION
from runtime.investigations.models import InvestigationSpec
from runtime.models.task import Task, TaskStatus
from runtime.state import repository
from runtime.state.orm import utcnow

MAX_LIVE_AGE = timedelta(minutes=15)


def collection_key(repository_name: str, source: str, kind: str) -> str:
    return hashlib.sha256(json.dumps([COLLECTOR_VERSION, repository_name, source, kind]).encode()).hexdigest()


def build_plan(workflow_id: str, spec: InvestigationSpec, parent_id: str | None = None) -> list[Task]:
    tasks, evaluations = [], []
    for index, repo in enumerate(spec.candidates):
        collectors = []
        for kind in ('metadata', 'documentation'):
            task_id = f'{workflow_id}_{index}_{kind}'
            collectors.append(task_id)
            tasks.append(Task(id=task_id, workflow_id=workflow_id, description=f'Collect {kind}: {repo}',
                              input={'tool':'investigation.collect', 'collection_key':collection_key(repo, spec.source, kind),
                                     'args':{'repository':repo, 'source':spec.source, 'kind':kind}}))
        task_id = f'{workflow_id}_{index}_evaluate'
        evaluations.append(task_id)
        tasks.append(Task(id=task_id, workflow_id=workflow_id, description=f'Evaluate requirements: {repo}', dependencies=collectors,
                          input={'tool':'investigation.evaluate', 'context_from_dependencies':True,
                                 'args':{'repository':repo, 'source':spec.source, 'requirements':spec.requirements}}))
    tasks.append(Task(id=f'{workflow_id}_brief', workflow_id=workflow_id, description='Assemble decision brief', dependencies=evaluations,
                      input={'tool':'investigation.assemble', 'context_from_dependencies':True,
                             'spec':spec.model_dump(), 'parent_workflow_id':parent_id,
                             'args':{'source':spec.source, 'requirements':spec.requirements}}))
    return tasks


def reuse_checkpoints(session, tasks: list[Task], parent_tasks: list[Task], *, now: datetime | None = None) -> int:
    """Persist independent copies. Parent task rows and results are never edited."""
    now = now or utcnow()
    cached = {(task.input or {}).get('collection_key'):task for task in parent_tasks
              if task.status == TaskStatus.COMPLETED and task.output}
    reused = 0
    for task in tasks:
        key = (task.input or {}).get('collection_key')
        original = cached.get(key) if key else None
        if original is None:
            continue
        output = original.output
        args = task.input['args']
        if any(output.get(k) != args[k] for k in ('repository','source','kind')):
            continue
        if output.get('collector_version') != COLLECTOR_VERSION:
            continue
        try:
            collected = datetime.fromisoformat(output['collected_at'])
            if collected.tzinfo is None or collected > now:
                continue
            if args['source'] == 'live' and now-collected > MAX_LIVE_AGE:
                continue
        except (ValueError, KeyError, TypeError):
            continue
        if output.get('content_hash') != hashlib.sha256(json.dumps(output['payload'],sort_keys=True).encode()).hexdigest():
            continue
        task.input = {**task.input, 'reused_from': {'workflow_id':original.workflow_id, 'task_id':original.id}}
        task.output = json.loads(json.dumps(output))
        task.status = TaskStatus.COMPLETED
        task.started_at = task.completed_at = now
        repository.update_task(session, task)
        repository.record_outbox_event(session, task.id, 'task.checkpoint_reused', task.input['reused_from'])
        reused += 1
    return reused
