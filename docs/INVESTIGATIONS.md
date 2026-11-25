# Technical investigations

The bounded investigation workspace uses existing task scheduling and
persistence. Select requirements, inspect cited findings, and revise a completed
investigation to reuse compatible collection checkpoints. The runtime walkthrough
and its controlled benchmark remain available separately.

## Endpoints

- POST /demo/investigations: create an investigation.
- GET /demo/investigations/{id}: specification, run state, report and reuse count.
- POST /demo/investigations/{id}/revisions: submit a changed specification.

Example request:

```json
{
  "source": "snapshot",
  "candidates": ["encode/httpx", "psf/requests", "aio-libs/aiohttp"],
  "requirements": ["async_client", "permissive_license"]
}
```

Use the returned workflow id for existing graph/trace endpoints. Submit the
same candidates and source with requirements `["http2"]` as a revision.
The original report stays intact. Six compatible collection checkpoints are
copied into the new run with explicit parent task references; three candidate
evaluations and the final report execute again. Copied tasks carry zero
worker attempts, and the trace includes task.checkpoint_reused events.

## Evidence rules and limitations

Each candidate collects metadata and one bounded repository-owned document.
Findings use explicit text patterns or a declared SPDX allowlist. Every
positive/negative finding has a source excerpt; absent or contradictory
statements remain not_established. This is conservative lexical evaluation,
not general semantic reasoning or a legal license assessment. No overall
quality ranking is inferred from popularity or from the number of matches.

Offline text is illustrative and labelled snapshot. Live requests use the
public GitHub API; documentation HTTP 404 is missing evidence, not a negative
feature claim. Other collection errors remain failures. Document hashes,
collection timestamps and content revision identifiers travel with evidence.
Live document citations address GitHub's immutable blob API for that revision.

## Revision boundaries

Reuse requires a successful collection task with a matching versioned key,
repository, source and evidence kind, a verified content hash, and an aware
collection timestamp. Live evidence expires after 15 minutes. Future-dated,
malformed, failed, stale or incompatible evidence is not reused. A source
switch invalidates all collection checkpoints. Evaluation/report results are
always recomputed, so a changed requirement cannot inherit an old conclusion.
This is within-parent reuse, not a global cache or a content freshness claim
against GitHub. Parent investigations must be terminal before revision.

## Next chunks

1. Missing-evidence recovery driven by real investigation requirements, with
   tests that persist/restart across independent worker processes.
2. Hosted walkthrough and regression checks of the complete experience.

Keep the earlier bounded comparison and benchmark intact while these chunks
are verified. Existing measured performance claims do not automatically apply
to this new workload.

## Interface verification

The browser flow covers creation, adding HTTP/2 as a revision, six reused
checkpoints, before/after findings, and restoration from the run URL. Citation
links are restricted to HTTPS GitHub hosts. Empty requirements are rejected before
submission. Offline excerpts are explicitly illustrative, not quotations from the
linked live document. On a source switch, the backend recollects all evidence.

## Comparison workspace

The default view focuses on library selection, requirements, and a results table.
Source excerpts and runtime details expand on demand. Markdown export preserves
the run URL, finding statuses, citations, document revisions, and evidence limits.
It labels example excerpts as illustrative even after export. Browser sharing
uses the current run URL; free-hosted history remains ephemeral.

Export formatting checks run with `node --test tests/web/*.test.cjs` and are
included in CI. No frontend build step or third-party browser library is needed.
