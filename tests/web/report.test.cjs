const {test} = require('node:test');
const assert = require('node:assert/strict');
const {investigationMarkdown} = require('../../runtime/web/report.js');

function report(source) {
  return {source,conclusion:'No candidate has sufficient collected evidence for every requirement.',
    evaluations:[{repository:'encode/httpx',findings:[
      {requirement:'http2',status:'not_established',explanation:'Conflicting statements require review.',citations:[
        {url:'https://api.github.com/repos/encode/httpx/git/blobs/abc',excerpt:'First line\nSecond line',source,revision:source==='live'?'abc':null}]}]}],
    limitations:['Rules use text matching, not semantic verification.']};
}

test('export keeps unresolved findings, multiline quotations, source revisions, and limits',()=>{
  const text=investigationMarkdown(report('live'),'https://relay.example/?run=123');
  for(const required of ['https://relay.example/?run=123','HTTP/2: Not established','Conflicting statements require review.',
    '> First line\n> Second line','Document revision: abc','Rules use text matching, not semantic verification.']) assert.ok(text.includes(required),required);
});

test('example exports identify illustrative excerpts rather than claiming live quotations',()=>{
  const text=investigationMarkdown(report('snapshot'),'http://localhost/');
  assert.match(text,/Illustrative example data \(not current repository facts\)/);
  assert.match(text,/linked page is a reference, not the source of this quotation/);
  assert.doesNotMatch(text,/Document revision:/);
});
