/* Explicit orthogonal browser states. Pure reducer; no network or persistent storage. */
'use strict';
(function (root, factory) {
  const value = factory();
  if (typeof module === 'object' && module.exports) module.exports = value;
  else root.TraceUIState = value;
})(typeof window === 'undefined' ? undefined : window, function () {
  const VIEWS = new Set(['pages', 'health', 'history']);
  const OPERATIONS = new Set(['saving', 'generating', 'feeding', 'navigating', 'deleting', 'restoring']);
  const EDITOR = {
    empty: {PAGE_LOADED:'clean', EDIT:'dirty', PAGE_CLOSED:'empty'},
    clean: {EDIT:'dirty', PAGE_LOADED:'clean', PAGE_CLOSED:'empty'},
    dirty: {EDIT:'dirty', SAVE_START:'saving', DISCARD_DRAFT:'clean', DRAFT_REHOMED:'clean'},
    saving: {SAVE_SUCCESS:'clean', SAVE_CONFLICT:'conflict', SAVE_FAILURE:'error'},
    conflict: {EDIT:'dirty', SAVE_START:'saving', DISCARD_DRAFT:'clean', DRAFT_REHOMED:'clean'},
    error: {EDIT:'dirty', SAVE_START:'saving', DISCARD_DRAFT:'clean', DRAFT_REHOMED:'clean'},
  };
  const initial = () => Object.freeze({view:'pages',editor:'empty',operation:'idle',
    response:'masked',connection:'connecting'});
  function invalid(state, event) {
    throw new Error(`Invalid UI state transition: ${event.type} in ${state.editor}/${state.operation}`);
  }
  function transition(state, event) {
    const next = {...state};
    if (Object.hasOwn(EDITOR[state.editor] || {}, event.type)) {
      next.editor = EDITOR[state.editor][event.type];
      if (event.type === 'PAGE_LOADED' || event.type === 'PAGE_CLOSED') next.response='masked';
    } else if (Object.values(EDITOR).some(row => Object.hasOwn(row, event.type))) {
      invalid(state,event);
    } else if (event.type === 'NAVIGATE') {
      if (!VIEWS.has(event.view)) invalid(state,event);
      next.view=event.view;
    } else if (event.type === 'OP_START') {
      if (state.operation !== 'idle' || !OPERATIONS.has(event.operation)) invalid(state,event);
      next.operation=event.operation;
    } else if (event.type === 'OP_END') {
      if (state.operation !== event.operation || state.operation === 'idle') invalid(state,event);
      next.operation='idle';
    } else if (event.type === 'RESPONSE_REVEALED') {
      next.response='revealed';
    } else if (event.type === 'RESPONSE_HIDDEN' || event.type === 'RESPONSE_SELECTED') {
      next.response='masked';
    } else if (event.type === 'CONNECTED') {
      next.connection='online';
    } else if (event.type === 'DISCONNECTED') {
      next.connection='offline';
    } else {
      invalid(state,event);
    }
    return Object.freeze(next);
  }
  const hasUnsavedContent = state => ['dirty','saving','conflict','error'].includes(state.editor);
  const canNavigateAway = state => !hasUnsavedContent(state);
  const editorLabels = Object.freeze({empty:'ページ未選択',clean:'保存済み',dirty:'未保存',
    saving:'保存中…',conflict:'競合：未保存の内容を保持',error:'保存失敗：内容を保持'});
  const operationLabels=Object.freeze({idle:'',saving:'保存中…',generating:'文章を生成しています…',
    feeding:'学習キューへ登録中…',navigating:'ページを読込中…',
    deleting:'ページを削除中…',restoring:'チェックポイントを復元中…'});
  return {initial,transition,hasUnsavedContent,canNavigateAway,editorLabels,operationLabels};
});
