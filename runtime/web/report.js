'use strict';
/** A portable report retains provenance and limitations when it leaves the UI. */
function investigationMarkdown(report, runUrl) {
  const labels = {async_client: 'Async client', http2: 'HTTP/2', permissive_license: 'Permissive license'};
  const statuses = {met: 'Supported', not_met: 'Does not meet rule', not_established: 'Not established'};
  const lines = ['# Python HTTP client comparison', '', `Run: ${runUrl}`, '',
    `Evidence: ${report.source === 'live' ? 'Live GitHub collection' : 'Illustrative example data (not current repository facts)'}`, '',
    report.conclusion, ''];
  for (const evaluation of report.evaluations) {
    lines.push(`## ${evaluation.repository}`, '');
    for (const finding of evaluation.findings) {
      lines.push(`### ${labels[finding.requirement]}: ${statuses[finding.status]}`, '', finding.explanation, '');
      for (const citation of finding.citations) {
        lines.push(...citation.excerpt.split('\n').map(line => `> ${line}`), '', `Source: ${citation.url}`,
          `Provenance: ${citation.source === 'snapshot' ? 'Illustrative fixture; linked page is a reference, not the source of this quotation' : 'Live collection'}`);
        if (citation.revision) lines.push(`Document revision: ${citation.revision}`);
        lines.push('');
      }
    }
  }
  lines.push('## Limitations', '', ...report.limitations.map(limit => `- ${limit}`), '');
  return lines.join('\n');
}
if (typeof module !== 'undefined') module.exports = {investigationMarkdown};
