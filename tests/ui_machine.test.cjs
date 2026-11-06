const test = require('node:test');
const assert = require('node:assert/strict');
const ui = require('../src/trace_llm/static/ui_machine.js');

function send(machine, events) { return events.reduce((s, event) => ui.transition(s, event), machine); }

test('editor is a true state machine; conflict retains dirty semantics', () => {
  const a = send(ui.initial(), [
    {type:'PAGE_LOADED'}, {type:'EDIT'}, {type:'SAVE_START'}, {type:'SAVE_CONFLICT'}
  ]);
  assert.equal(a.editor, 'conflict');
  assert.equal(ui.hasUnsavedContent(a), true);
  assert.equal(ui.canNavigateAway(a), false);
  const b = send(a, [{type:'EDIT'}, {type:'SAVE_START'}, {type:'SAVE_SUCCESS'}]);
  assert.equal(b.editor, 'clean');
  assert.equal(ui.canNavigateAway(b), true);
});

test('invalid save transition fails closed', () => {
  assert.throws(() => ui.transition(ui.initial(), {type:'SAVE_SUCCESS'}), /invalid/i);
  assert.throws(() => ui.transition(ui.initial(), {type:'SAVE_START'}), /invalid/i);
});

test('long operation has explicit exclusive states and releases reliably', () => {
  const start=ui.transition(ui.initial(), {type:'OP_START', operation:'generating'});
  assert.equal(start.operation,'generating');
  assert.throws(() => ui.transition(start,{type:'OP_START', operation:'feeding'}),/invalid/i);
  assert.equal(ui.transition(start,{type:'OP_END',operation:'generating'}).operation,'idle');
  assert.throws(()=>ui.transition(start,{type:'OP_END',operation:'feeding'}),/invalid/i);
});

test('view routing and masked response are independent', () => {
  const a = send(ui.initial(),[{type:'PAGE_LOADED'}, {type:'RESPONSE_REVEALED'},
    {type:'NAVIGATE', view:'health'}]);
  assert.equal(a.view,'health');
  assert.equal(a.response,'revealed');
  assert.equal(ui.transition(a,{type:'RESPONSE_SELECTED'}).response,'masked');
  assert.equal(ui.transition(a,{type:'PAGE_LOADED'}).response,'masked');
});

test('connection can become stale without destroying loaded page', () => {
  const a=send(ui.initial(),[{type:'PAGE_LOADED'}, {type:'EDIT'}, {type:'DISCONNECTED'}]);
  assert.equal(a.connection,'offline');
  assert.equal(a.editor,'dirty');
  assert.equal(ui.transition(a,{type:'CONNECTED'}).connection,'online');
});

test('a dirty or conflicted draft cannot be silently replaced by a page load', () => {
  const edited=send(ui.initial(),[{type:'PAGE_LOADED'},{type:'EDIT'}]);
  assert.throws(()=>ui.transition(edited,{type:'PAGE_LOADED'}),/invalid/i);
  const conflicted=send(edited,[{type:'SAVE_START'},{type:'SAVE_CONFLICT'}]);
  assert.throws(()=>ui.transition(conflicted,{type:'PAGE_LOADED'}),/invalid/i);
  const confirmed=ui.transition(conflicted,{type:'DISCARD_DRAFT'});
  assert.equal(ui.transition(confirmed,{type:'PAGE_LOADED'}).editor,'clean');
});

test('closing a draft requires a persisted replacement or explicit discard', () => {
  const dirty=send(ui.initial(),[{type:'PAGE_LOADED'},{type:'EDIT'}]);
  assert.throws(()=>ui.transition(dirty,{type:'PAGE_CLOSED'}),/invalid/i);
  const conflict=send(dirty,[{type:'SAVE_START'},{type:'SAVE_CONFLICT'}]);
  assert.throws(()=>ui.transition(conflict,{type:'PAGE_CLOSED'}),/invalid/i);
  const safe=ui.transition(conflict,{type:'DRAFT_REHOMED'});
  assert.equal(ui.transition(safe,{type:'PAGE_CLOSED'}).editor,'empty');
});
