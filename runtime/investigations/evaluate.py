"""Conservative evidence rules. Absence of a phrase is never a negative finding."""
import re
from runtime.investigations.models import Citation, Finding

PATTERNS = {
    'async_client': (r'(?:does not support|no support for) (?:async|asynchronous)', r'(?:asynchronous HTTP client|async(?:hronous)? client|synchronous and asynchronous HTTP client)'),
    'http2': (r'(?:does not support|no support for) HTTP/2', r'(?:supports? HTTP/2|HTTP/2 support|supports? HTTP/1\.1 and HTTP/2)'),
}
ALLOWED_LICENSES = {'MIT', 'BSD-2-Clause', 'BSD-3-Clause', 'Apache-2.0', 'ISC'}


def evaluate(args: dict) -> dict:
    evidence = list(args['dependency_outputs'].values())
    repo = args['repository']
    if any(item['repository'] != repo or item['source'] != args['source'] for item in evidence):
        raise ValueError('evidence repository/source does not match evaluation')
    metadata = next(item for item in evidence if item['kind'] == 'metadata')
    document = next(item for item in evidence if item['kind'] == 'documentation')
    findings = []
    for requirement in args['requirements']:
        status, explanation, citations = 'not_established', 'The collected sources do not establish this requirement.', []
        if requirement == 'permissive_license':
            license_id = metadata['payload'].get('license')
            if license_id and license_id != 'NOASSERTION':
                status = 'met' if license_id in ALLOWED_LICENSES else 'not_met'
                explanation = 'SPDX identifier checked against the declared license allowlist; not a legal assessment.'
                citations = [Citation(url=metadata['payload']['url'], excerpt=f'SPDX: {license_id}', source=args['source'])]
        else:
            negative, positive = PATTERNS[requirement]
            matches = []
            for line in document['payload']['text'].splitlines():
                if re.search(negative, line, re.I) or re.search(r'\b(?:not|no|without)\b.*'+positive, line, re.I):
                    matches.append(('not_met', line))
                elif re.search(positive, line, re.I):
                    matches.append(('met', line))
            if matches:
                statuses = {item[0] for item in matches}
                status = next(iter(statuses)) if len(statuses) == 1 else 'not_established'
                explanation = 'Conflicting statements require review.' if len(statuses) > 1 else 'Explicit statement in the collected repository documentation.'
                citations = [Citation(url=document['payload']['url'], excerpt=line, source=args['source'], revision=document['payload']['revision']) for line in dict(matches).values()]

        findings.append(Finding(requirement=requirement, status=status, explanation=explanation, citations=citations).model_dump())
    return {'kind': 'candidate_evaluation', 'repository': repo, 'source': args['source'], 'findings': findings,
            'evidence_hashes': [item['content_hash'] for item in evidence]}


def assemble(args: dict) -> dict:
    evaluations = list(args['dependency_outputs'].values())
    eligible = [e['repository'] for e in evaluations if all(f['status'] == 'met' for f in e['findings'])]
    return {'kind': 'investigation_report', 'source': args['source'], 'evaluations': evaluations,
            'requirements': args['requirements'], 'candidates_meeting_all_requirements': eligible,
            'conclusion': 'Candidates with supporting evidence for every requirement are listed; no quality ranking is inferred.' if eligible else 'No candidate has sufficient collected evidence for every requirement.',
            'limitations': ['Snapshot findings use illustrative fixtures.' if args['source'] == 'snapshot' else 'Live findings cover only the collected repository documentation.', 'Rules are conservative text matching, not semantic verification. Missing evidence remains unresolved.']}
