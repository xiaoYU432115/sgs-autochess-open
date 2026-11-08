// src/plugins/scenario.js
// 剧本（规范便捷功能 P2-7）。机制：预设操作序列（steps:[{action,args}]）按顺序执行，
// 每个 action 映射到宿主注入的 actions 回调（如 chooseLord/buy/recycle/useSpell/setSpeed）。
// 纯函数 normalizeScenario/decideNextStep（可测）；createScenarioRunner 载入/单步/播放/暂停；
// createScenarioPanel 列出剧本 + 播放/单步/重置。
import { el, panel, button, toggle } from '../core/ui.js';

const SCENARIO_GM_KEY = 'sgs_scenario_v1';

// 校验 + 归一化剧本：{name, steps:[{action, args}]}；缺 name/无有效 step 返回 null
export function normalizeScenario(s) {
  if (!s || typeof s !== 'object') return null;
  const name = String(s.name || '').trim();
  if (!name) return null;
  const raw = Array.isArray(s.steps) ? s.steps : [];
  const steps = [];
  for (const st of raw) {
    if (!st || typeof st !== 'object') continue;
    const action = String(st.action || '').trim();
    if (!action) continue;
    steps.push({ action, args: st.args || null });
  }
  if (!steps.length) return null;
  return { name, steps };
}

// 返回 cursor 处的 step，越界返回 null（done 信号）
export function decideNextStep(scenario, cursor) {
  if (!scenario || !scenario.steps) return null;
  if (cursor < 0 || cursor >= scenario.steps.length) return null;
  return scenario.steps[cursor];
}

export function createScenarioRunner(deps) {
  const Config = deps.Config || { get() { return null; } };
  const Debug = deps.Debug || { info() {}, warn() {}, error() {}, event() {} };
  const actions = deps.actions || {};
  let scenarios = {};
  let cursor = 0;
  let current = null;
  let running = false;

  function load() { const c = Config.get(SCENARIO_GM_KEY, null) || {}; scenarios = c.scenarios || {}; }
  function list() { return Object.keys(scenarios); }
  function get(name) { return scenarios[name] || null; }

  function execStep(step) {
    const fn = actions[step.action];
    if (typeof fn !== 'function') { Debug.warn('scenario: 未注册动作', step.action); return { ok: false, error: 'unknown-action' }; }
    try { const r = fn(step.args); Debug.event('scenario_step', step.action, step.args); return { ok: true, result: r }; }
    catch (e) { Debug.error('scenario: 步骤执行失败', step.action, e && e.message); return { ok: false, error: e && e.message }; }
  }

  // 手动单步：填 name 则先切换并重置光标；返回 {step,result,done} 或 null（已结束）
  function step(name) {
    if (name) { current = normalizeScenario(scenarios[name]); cursor = 0; }
    const s = decideNextStep(current, cursor);
    if (!s) { current = null; return null; }
    const r = execStep(s);
    cursor++;
    return { step: s, result: r, done: cursor >= current.steps.length };
  }

  // 连续播放：同步执行全部步骤；某步失败即停
  function play(name) {
    const sc = normalizeScenario(scenarios[name]);
    if (!sc) { Debug.warn('scenario: 未找到', name); return false; }
    current = sc; cursor = 0; running = true;
    while (running && cursor < sc.steps.length) {
      const r = execStep(sc.steps[cursor]);
      cursor++;
      if (!r.ok) { running = false; break; }
    }
    running = false;
    const done = cursor >= sc.steps.length;
    Debug.event('scenario_done', name, done);
    return done;
  }
  function pause() { running = false; }
  function reset() { current = null; cursor = 0; running = false; }

  load();
  return {
    step, play, pause, reset, load, list, get,
    get state() { return { current: current && current.name, cursor, running }; },
  };
}

export function createScenarioPanel(deps) {
  const ui = deps.ui || { el, panel, button, toggle };
  const ctrl = deps.ctrl;

  const p = ui.panel('自走棋助手 · 剧本');
  const status = ui.el('div', { style: { marginBottom: '4px' } });
  p.add(status);
  const list = ui.el('div', { style: { fontSize: '11px', maxHeight: '160px', overflow: 'auto' } });
  p.add(list);

  const nameInput = ui.el('input', { type: 'text', placeholder: '剧本名' });
  const row = ui.el('div');
  row.appendChild(nameInput);
  row.appendChild(ui.button('播放', () => { ctrl.play(nameInput.value); render(); }));
  row.appendChild(ui.button('单步', () => { ctrl.step(nameInput.value); render(); }));
  row.appendChild(ui.button('重置', () => { ctrl.reset(); render(); }));
  p.add(row);

  function render() {
    const st = ctrl.state;
    status.textContent = '当前: ' + (st.current || '（无）') + ' · 步 ' + st.cursor + (st.running ? ' · 播放中' : '');
    list.textContent = '';
    ctrl.list().forEach((n) => list.appendChild(ui.el('div', { text: n + ' (' + (ctrl.get(n) ? ctrl.get(n).steps.length : 0) + ' 步)' })));
  }
  render();

  return {
    panel: p,
    refresh: render,
    destroy() { if (p.root && p.root.parentNode) p.root.parentNode.removeChild(p.root); },
  };
}

const hasDOM = typeof document !== 'undefined' && typeof document.createElement === 'function';

export default {
  id: 'scenario',
  name: '剧本',
  enabled: true,
  load(ctx) {
    if (!hasDOM) return { unload() {} };
    const actions = ctx.actions || {};
    const ctrl = createScenarioRunner({ Config: ctx.Config, Debug: ctx.Debug, actions });
    const view = createScenarioPanel({ ui: { el, panel, button, toggle }, ctrl });
    return { unload() { view.destroy(); } };
  },
};
