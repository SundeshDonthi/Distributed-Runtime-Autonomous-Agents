"""Controlled experiment, not a production or live-GitHub performance claim.

Compare sequential execution without replanning against concurrent execution
with replanning on the SAME seeded sequence of input plans. Fixed 80 ms
fixture reads model external I/O; seeded missing-README cases test recovery.
Timing compares only successful, fault-free paired runs. Reliability compares
all runs. This is a bundled-policy comparison, not proof concurrency causes
higher success. Raw observations and settings are written for auditing.

Run: python -m benchmarks.verify_product_claims --output /tmp/relay-benchmark.json
"""
from __future__ import annotations

import argparse
import json
import random
import platform
from datetime import datetime, timezone
import statistics
import time
from dataclasses import replace
from pathlib import Path

import redis
from redis.backoff import NoBackoff
from redis.retry import Retry
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from runtime.models.workflow import WorkflowStatus
from runtime.scheduler.scheduler import run_workflow
from runtime.state import repository
from runtime.state.orm import Base
from runtime.tools.registry import get_tool_metadata, register_tool
from runtime.workloads.repositories import build_plan


def experiment(n: int = 100, seed: int = 731) -> dict:
    if n < 2:
        raise ValueError("at least two workflow pairs are required")
    rng = random.Random(seed)
    faults = [rng.random() < 0.2 for _ in range(n)]
    original = get_tool_metadata('github.repository')
    def measured_fixture(args):
        time.sleep(0.08)
        return original.fn(args)
    register_tool(replace(original, fn=measured_fixture))
    records = []
    unavailable = redis.Redis(host='127.0.0.1', port=1, socket_connect_timeout=0.01,
                              socket_timeout=0.01, retry=Retry(NoBackoff(), 0))
    try:
        for i, fault in enumerate(faults):
            # Alternate policy order to reduce systematic warm-up effects.
            policies = [('sequential', 1, 0), ('runtime', 3, 2)]
            if i % 2:
                policies.reverse()
            for policy, concurrency, replans in policies:
                engine = create_engine('sqlite:///:memory:')
                Base.metadata.create_all(engine)
                try:
                    with sessionmaker(bind=engine, expire_on_commit=False)() as session:
                        workflow_id = f'benchmark_{i}_{policy}'
                        tasks = build_plan(workflow_id, 'snapshot', fault)
                        for task in tasks:
                            task.max_replans = replans
                        repository.create_workflow(session, workflow_id, 'controlled benchmark')
                        repository.save_tasks(session, tasks)
                        started = time.perf_counter()
                        status = run_workflow(session, workflow_id, max_concurrency=concurrency, redis_client=unavailable)
                        elapsed = time.perf_counter() - started
                        records.append({'pair':i, 'policy':policy, 'missing_readme':fault,
                                        'success':status == WorkflowStatus.COMPLETED, 'seconds':elapsed})
                finally:
                    engine.dispose()
    finally:
        register_tool(original)
        unavailable.close()
    clean = [i for i, fault in enumerate(faults) if not fault]
    if not clean:
        raise ValueError('no fault-free pairs; increase sample size')
    totals = {policy: sum(row['success'] for row in records if row['policy']==policy) for policy in ('sequential','runtime')}
    times = {policy: statistics.median(row['seconds'] for row in records if row['policy']==policy and row['pair'] in clean) for policy in totals}
    return {
        'scope':'controlled_offline_fixture', 'seed':seed, 'workflow_pairs':n,
        'recorded_at':datetime.now(timezone.utc).isoformat(),
        'environment':{'python':platform.python_version(), 'os':platform.system(), 'architecture':platform.machine()},
        'fault_probability':0.2, 'injected_faults':sum(faults), 'fixture_latency_seconds':0.08,
        'baseline':'sequential concurrency=1, max_replans=0',
        'runtime':'concurrency=3, max_replans=2',
        'latency_reduction_percent':100*(1-times['runtime']/times['sequential']),
        'success_improvement_percentage_points':100*(totals['runtime']-totals['sequential'])/n,
        'success_counts':totals, 'fault_free_median_seconds':times,
        'limitations':['Synthetic fixed I/O latency; not a live API result.',
                      'Replanning policy differs between arms; success gain cannot be attributed to concurrency.',
                      'No worker-process crash is injected in this experiment.',
                      'Point estimates only; repeat on target hosting hardware and report uncertainty.'],
        'observations':records,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--workflows', type=int, default=100)
    parser.add_argument('--seed', type=int, default=731)
    parser.add_argument('--output', type=Path, default=Path('/tmp/relay-benchmark.json'))
    args = parser.parse_args()
    result = experiment(args.workflows, args.seed)
    args.output.write_text(json.dumps(result, indent=2)+'\n')
    print(json.dumps({k:v for k,v in result.items() if k!='observations'}, indent=2))
    print(f'Raw observations: {args.output}')


if __name__ == '__main__':
    main()
