// src/plugins/lord.js
// 主公选择（规范便捷功能 P2-3）。decideLord 纯函数（可测），createLordRunner 监听选将事件并下单，
// createLordPanel 提供 DOM 面板。候选源：选将窗 DataProvider / manager.GeneralList（runtime.getLordCandidates）。
import { el, panel, button, toggle } from '../core/ui.js';

const LORD_GM_KEY = 'sgs_lord_v1';

// 给定候选主公与配置，返回应选 generalID；无候选返回 null。
// cfg.priority: 优先顺序的 lordID 列表，命中第一个在候选里的即选；否则选候选首个。
export function decideLord(candidates, cfg) {
  const list = (candidates || []).filter((c) => c && c.id != null);
  if (!list.length) return null;
  const priority = (cfg && cfg.priority) || [];
  for (const pid of priority) {
    const hit = list.find((c) => c.id === pid);
    if (hit) return hit.id;
  }
  return list[0].id;
}

export function normalizeLords(raw) {
  return (raw || []).map((c) => ({
    id: c.GeneralID != null ? c.GeneralID : (c.id != null ? c.id : c),
    name: c.GeneralName != null ? c.GeneralName : (c.name != null ? c.name : ''),
  })).filter((c) => c.id != null);
}

export function createLordRunner(deps) {
  const Runtime = deps.Runtime;
  const Config = deps.Config || { get() { return null; } };
  const Debug = deps.Debug || { info() {}, warn() {}, error() {}, event() {} };

  function cfg() { return Config.get(LORD_GM_KEY, null) || {}; }
  function choose() {
    const cands = Runtime.getLordCandidates();
    const id = decideLord(cands, cfg());
    if (id == null) { Debug.warn('lord: 无候选主公'); return null; }
    Debug.event('lord_choose', id);
    Runtime.reqChessChooseGeneral(id);
    return id;
  }
  function onShow() { choose(); }
  function attach() {
    Runtime.on('ANI_SELECT_GENERAL_SHOW_LIST', runner, onShow);
    Runtime.on('UI_UPDATE_GENERAL', runner, onShow);
  }
  function detach() {
    Runtime.off('ANI_SELECT_GENERAL_SHOW_LIST', runner, onShow);
    Runtime.off('UI_UPDATE_GENERAL', runner, onShow);
  }
  const runner = { choose, attach, detach };
  return runner;
}

// DOM 面板：列出候选主公 + "选主公"按钮（订阅选将事件实时刷新）。
export function createLordPanel(deps) {
  const Runtime = deps.Runtime;
  const Debug = deps.Debug;
  const ui = deps.ui || { el, panel, button, toggle };
  const runner = deps.runner;

  const p = ui.panel('自走棋助手 · 主公');
  const row = ui.el('div');
  row.appendChild(ui.button('选主公', () => runner.choose()));
  p.add(row);

  const list = ui.el('div', { style: { fontSize: '11px', maxHeight: '160px', overflow: 'auto' } });
  p.add(list);

  function render() {
    const cands = Runtime.getLordCandidates();
    list.textContent = '';
    if (!cands.length) { list.appendChild(ui.el('div', { text: '（无候选主公）' })); return; }
    cands.forEach((c) => list.appendChild(ui.el('div', { text: `ID:${c.id}${c.name ? ' ' + c.name : ''}` })));
  }

  const E = ['ANI_SELECT_GENERAL_SHOW_LIST', 'UI_UPDATE_GENERAL', 'RESP_SELECT_GENERAL'];
  E.forEach((e) => Runtime.on(e, panel, render));
  render();

  return {
    panel: p,
    refresh: render,
    destroy() {
      E.forEach((e) => Runtime.off(e, panel, render));
      if (p.root && p.root.parentNode) p.root.parentNode.removeChild(p.root);
    },
  };
}

const hasDOM = typeof document !== 'undefined' && typeof document.createElement === 'function';

export default {
  id: 'lord',
  name: '主公选择',
  enabled: true,
  load(ctx) {
    if (!hasDOM) return { unload() {} };
    const runner = createLordRunner({ Runtime: ctx.Runtime, Config: ctx.Config, Debug: ctx.Debug });
    const view = createLordPanel({ Runtime: ctx.Runtime, Debug: ctx.Debug, ui: { el, panel, button, toggle }, runner });
    runner.attach();
    return { unload() { runner.detach(); view.destroy(); } };
  },
};
