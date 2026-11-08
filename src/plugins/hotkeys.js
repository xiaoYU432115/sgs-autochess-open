// src/plugins/hotkeys.js
// 快捷键（规范便捷功能 P2-4）。纯函数：normalizeCombo / matchAction（可测）；createHotkeyController
// 监听全局 keydown → 查 keymap（Config）→ 调注入的 action 回调。createHotkeyPanel 列出当前绑定。
import { el, panel, button, toggle } from '../core/ui.js';

const HOTKEY_GM_KEY = 'sgs_hotkeys_v1';

// 键盘事件 → 组合键字符串，如 "Ctrl+Shift+S" / "Alt+Enter" / "Space"
export function normalizeCombo(e) {
  const parts = [];
  if (e.ctrlKey || e.metaKey) parts.push('Ctrl');
  if (e.altKey) parts.push('Alt');
  if (e.shiftKey) parts.push('Shift');
  let k = e.key;
  if (k == null) k = '';
  else if (k === ' ') k = 'Space';
  else if (k.length === 1) k = k.toUpperCase();
  parts.push(k);
  return parts.join('+');
}

// keymap: { 'Ctrl+S': 'sellAll' }；命中返回动作名，否则 null
export function matchAction(keymap, combo) {
  if (!keymap) return null;
  return keymap[combo] != null ? keymap[combo] : null;
}

export function createHotkeyController(deps) {
  const Config = deps.Config || { get() { return null; } };
  const Debug = deps.Debug || { info() {}, warn() {}, error() {}, event() {} };
  const actions = deps.actions || {};
  let handler = null;

  function getKeymap() { return Config.get(HOTKEY_GM_KEY, null) || {}; }
  function dispatch(combo) {
    const action = matchAction(getKeymap(), combo);
    if (!action) return false;
    const fn = actions[action];
    if (typeof fn !== 'function') { Debug.warn('hotkeys: 未注册动作', action); return false; }
    Debug.event('hotkey', combo, action);
    try { fn(); } catch (err) { Debug.error('hotkeys: 动作执行失败', action, err && err.message); }
    return true;
  }
  function onKeyDown(e) { dispatch(normalizeCombo(e)); }
  function attach(doc) {
    const d = doc || (typeof document !== 'undefined' ? document : null);
    if (!d || typeof d.addEventListener !== 'function') return false;
    handler = (e) => onKeyDown(e);
    d.addEventListener('keydown', handler);
    return true;
  }
  function detach(doc) {
    const d = doc || (typeof document !== 'undefined' ? document : null);
    if (d && handler && typeof d.removeEventListener === 'function') d.removeEventListener('keydown', handler);
    handler = null;
  }
  return { normalizeCombo, matchAction, dispatch, attach, detach, getKeymap };
}

export function createHotkeyPanel(deps) {
  const ui = deps.ui || { el, panel, button, toggle };
  const ctrl = deps.ctrl;
  const actions = deps.actions || {};

  const p = ui.panel('自走棋助手 · 快捷键');
  const list = ui.el('div', { style: { fontSize: '11px', maxHeight: '160px', overflow: 'auto' } });
  p.add(list);
  const hint = ui.el('div', { text: `已注册动作: ${Object.keys(actions).join(', ') || '（无）'}`, style: { marginTop: '4px', color: '#888' } });
  p.add(hint);

  function render() {
    const km = ctrl.getKeymap();
    list.textContent = '';
    const keys = Object.keys(km);
    if (!keys.length) { list.appendChild(ui.el('div', { text: '（无快捷键绑定）' })); return; }
    keys.forEach((k) => list.appendChild(ui.el('div', { text: `${k} → ${km[k]}` })));
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
  id: 'hotkeys',
  name: '快捷键',
  enabled: true,
  load(ctx) {
    if (!hasDOM) return { unload() {} };
    const actions = ctx.actions || {};
    const ctrl = createHotkeyController({ Config: ctx.Config, Debug: ctx.Debug, actions });
    const attached = ctrl.attach();
    const view = createHotkeyPanel({ ui: { el, panel, button, toggle }, ctrl, actions });
    return {
      unload() { ctrl.detach(); view.destroy(); },
      attached,
    };
  },
};
