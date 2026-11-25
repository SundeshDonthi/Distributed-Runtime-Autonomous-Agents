"""Public GitHub evidence collection and deterministic comparison.

Only api.github.com is contacted. Snapshots are explicit offline examples,
never a silent substitute for live data. Tools remain stateless across workers.
"""
from __future__ import annotations

import base64
import json
import os
import re
from datetime import datetime, timezone

import httpx

from runtime.models.task import Task

REPOSITORIES = ("encode/httpx", "psf/requests", "aio-libs/aiohttp")
REPO_PATTERN = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")
# Illustrative fixtures: intentionally no invented popularity statistics.
SNAPSHOTS = {
    "encode/httpx": {"description": "A next generation HTTP client for Python.", "language": "Python", "license": {"spdx_id": "BSD-3-Clause"}, "topics": ["http", "async", "python"]},
    "psf/requests": {"description": "A simple, yet elegant, HTTP library.", "language": "Python", "license": {"spdx_id": "Apache-2.0"}, "topics": ["http", "python"]},
    "aio-libs/aiohttp": {"description": "Asynchronous HTTP client/server framework.", "language": "Python", "license": {"spdx_id": "Apache-2.0"}, "topics": ["async", "http", "python"]},
}


def validate_repo(repo: str) -> str:
    if not REPO_PATTERN.fullmatch(repo) or any(part in {".", ".."} for part in repo.split("/")):
        raise ValueError("repository must be an owner/name identifier")
    return repo


def _get(path: str) -> dict:
    headers = {"Accept": "application/vnd.github+json", "User-Agent": "Agent-Runtime-Explorer"}
    token = os.environ.get("GITHUB_TOKEN")
    if token:
        headers["Authorization"] = f"Bearer {token}"
    with httpx.Client(timeout=12, follow_redirects=False) as client:
        with client.stream("GET", f"https://api.github.com/repos/{path}", headers=headers) as response:
            if response.status_code in (403, 429):
                raise RuntimeError("GitHub rate limit reached; try an offline example or retry later")
            if response.status_code == 404:
                raise ValueError("public repository or README not found")
            response.raise_for_status()
            content = bytearray()
            for chunk in response.iter_bytes():
                content.extend(chunk)
                if len(content) > 2_000_000:
                    raise ValueError("GitHub response exceeded the evidence size limit")
    return json.loads(content)


def fetch_repository(args: dict) -> dict:
    repo = validate_repo(args["repository"])
    mode = args.get("source", "live")
    if mode == "snapshot":
        data = SNAPSHOTS[repo]
    else:
        data = _get(repo)
    return {
        "kind": "repository", "repository": repo, "source": mode,
        "url": f"https://github.com/{repo}",
        "collected_at": datetime.now(timezone.utc).isoformat(),
        "description": data.get("description"), "language": data.get("language"),
        "license": (data.get("license") or {}).get("spdx_id"),
        "stars": data.get("stargazers_count"), "forks": data.get("forks_count"),
        "open_issues": data.get("open_issues_count"), "pushed_at": data.get("pushed_at"),
        "topics": data.get("topics", []), "archived": data.get("archived", False),
    }


def fetch_readme(args: dict) -> dict:
    repo = validate_repo(args["repository"])
    source = args.get("source", "live")
    data = {"html_url": f"https://github.com/{repo}#readme"} if source == "snapshot" else _get(f"{repo}/readme")
    excerpt = None
    if source == "live" and data.get("encoding") == "base64":
        excerpt = base64.b64decode(data.get("content", "")).decode("utf-8", errors="replace")[:800]
    return {"kind": "readme", "repository": repo, "source": source,
            "url": data.get("html_url"), "sha": data.get("sha"), "excerpt": excerpt,
            "verified": source == "live"}


def compare_repositories(args: dict) -> dict:
    from runtime.tools.registry import MissingPrerequisiteError

    evidence = list(args.get("dependency_outputs", {}).values())
    repositories = [row for row in evidence if row.get("kind") == "repository"]
    if len(repositories) != len(args["repositories"]):
        raise ValueError("comparison requires evidence for every requested repository")
    required = args.get("require_readme")
    readmes = [row for row in evidence if row.get("kind") == "readme"]
    if required and not any(row["repository"] == required for row in readmes):
        raise MissingPrerequisiteError(json.dumps({"type": "repository_readme", "repository": required, "source": args["source"]}))
    return {
        "kind": "comparison_report", "title": "Python HTTP clients · evidence brief",
        "source": args["source"], "repositories": repositories, "additional_evidence": readmes,
        "findings": [
            {"repository": row["repository"], "summary": row["description"] or "No description provided.",
             "evidence": row["url"], "license": row["license"], "archived": row["archived"]}
            for row in repositories
        ],
        "methodology": "Fetched public metadata independently, then assembled this report from completed prerequisite outputs. Popularity does not measure software quality. Offline examples omit changing statistics.",
    }


def build_plan(workflow_id: str, source: str, recover: bool = False) -> list[Task]:
    fetches = [Task(id=f"{workflow_id}_repo_{i}", workflow_id=workflow_id,
                    description=f"Collect {repo}", required_capabilities=["repository_metadata"],
                    input={"tool": "github.repository", "args": {"repository": repo, "source": source}})
               for i, repo in enumerate(REPOSITORIES)]
    report = Task(id=f"{workflow_id}_report", workflow_id=workflow_id,
                  description="Assemble evidence brief", dependencies=[task.id for task in fetches],
                  input={"tool": "github.compare", "context_from_dependencies": True,
                         "args": {"repositories": list(REPOSITORIES), "source": source,
                                  "require_readme": REPOSITORIES[0] if recover else None}})
    return [*fetches, report]


def prerequisite_plan(workflow_id: str, blocked_task_id: str, replan_index: int, context: str) -> list[Task] | None:
    try:
        request = json.loads(context)
    except (ValueError, TypeError):
        return None
    if not isinstance(request, dict) or request.get("type") != "repository_readme":
        return None
    repo = validate_repo(request["repository"])
    return [Task(id=f"{blocked_task_id}_readme_{replan_index}", workflow_id=workflow_id,
                 description=f"Resolve missing README: {repo}",
                 input={"tool": "github.readme", "args": {"repository": repo, "source": request["source"]}})]
