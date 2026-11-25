"""Bounded collection from repository-owned documentation, never arbitrary URLs."""
import base64
import hashlib
import json
from datetime import datetime, timezone

from runtime.workloads.repositories import REPOSITORIES, GitHubResourceNotFound, _get, fetch_repository

DOCUMENTS = {'encode/httpx': 'docs/index.md', 'psf/requests': 'README.md', 'aio-libs/aiohttp': 'README.rst'}
# Teaching fixtures, explicitly labelled; never claimed as current documentation.
EXAMPLES = {
    'encode/httpx': 'A synchronous and asynchronous HTTP client. Supports HTTP/1.1 and HTTP/2.',
    'psf/requests': 'An HTTP library for Python.',
    'aio-libs/aiohttp': 'An asynchronous HTTP client/server framework.',
}
COLLECTOR_VERSION = 'repository-evidence-v1'


def collect(args: dict) -> dict:
    repo, source, kind = args['repository'], args['source'], args['kind']
    if repo not in REPOSITORIES or source not in {'snapshot', 'live'} or kind not in {'metadata', 'documentation'}:
        raise ValueError('unsupported evidence request')
    if kind == 'metadata':
        payload = fetch_repository({'repository': repo, 'source': source})
    else:
        path = DOCUMENTS[repo]
        if source == 'snapshot':
            text, revision, available = EXAMPLES[repo], None, True
        else:
            try:
                data = _get(f'{repo}/contents/{path}')
            except GitHubResourceNotFound:
                data = None
            available = data is not None
            if data is not None and data.get('encoding') != 'base64':
                raise ValueError('unsupported documentation encoding')
            text = base64.b64decode(data['content']).decode('utf-8', errors='replace')[:64000] if data else ''
            revision = data.get('sha') if data else None
        payload = {'text': text, 'revision': revision, 'available': available,
                   'url': f'https://api.github.com/repos/{repo}/git/blobs/{revision}' if revision else f'https://github.com/{repo}/blob/HEAD/{path}'}
    return {'kind': kind, 'repository': repo, 'source': source,
            'collector_version': COLLECTOR_VERSION,
            'collected_at': datetime.now(timezone.utc).isoformat(),
            'payload': payload,
            'content_hash': hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()}
