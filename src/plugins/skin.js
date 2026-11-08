// src/plugins/skin.js
// 换肤（规范便捷功能 P2-6）。机制：资源 URL 改写（pattern→url），hook XHR/Image/fetch/CSS。
// 纯函数 normalizeRule/applyRules/matchRule（可测）；createSkinController 管理规则与 swap；
// createSkinPanel 提供规则列表 + 添加 + 从助手拉取/清空/暂停。规则源：Config 手动规则 或 助手 /rules。
import { el, panel, button, toggle } from '../core/ui.js';

const SKIN_GM_KEY = 'sgs_skin_v1';

export function isLocalUrl(u) {
  return /^https?:\/\/(127\.0\.0\.1|localhost)(:\d+)?\//i.test(String(u || ''));
}

export function normalizeRule(r) {
  if (!r) return null;
  const p = String(r.pattern != null ? r.pattern : r.p != null ? r.p : '').trim();
  const u = String(r.url != null ? r.url : r.target != null ? r.target : '').trim();
  if (!p || !u) return null;
  return { pattern: p, url: u, kind: r.kind || '', name: r.name || '' };
}

// 归一化并【长 pattern 优先】排序（避免短规则先吃掉长路径）
export function applyRules(list) {
  const out = [];
  for (const r of (list || [])) { const n = normalizeRule(r); if (n) out.push(n); }
  out.sort((a, b) => b.pattern.length - a.pattern.length);
  return out;
}

// 命中返回替换 URL，否则空串（本地 URL / 无规则 / 未启用由调用方决定）
export function matchRule(url, rules) {
  const s = String(url || '');
  if (!s || isLocalUrl(s)) return '';
  for (const r of (rules || [])) { if (s.indexOf(r.pattern) >= 0) return r.url; }
  return '';
}

export function createSkinController(deps) {
  const Config = deps.Config || { get() { return null; } };
  const Debug = deps.Debug || { info() {}, warn() {}, error() {}, event() {} };
  const state = { enabled: true, rules: [], rev: '', port: 8765, source: 'none', hits: 0 };

  function load() {
    const c = Config.get(SKIN_GM_KEY, null) || {};
    if (c.enabled != null) state.enabled = !!c.enabled;
    if (Array.isArray(c.rules)) state.rules = applyRules(c.rules);
    if (c.port) state.port = c.port;
  }
  function setRules(list, rev, source) {
    state.rules = applyRules(list);
    if (rev != null) state.rev = String(rev);
    if (source) state.source = source;
    Debug.event('skin_rules', state.rules.length);
    return state.rules.length;
  }
  function addRule(pattern, url) {
    const n = normalizeRule({ pattern, url });
    if (!n) return false;
    state.rules = applyRules(state.rules.concat(n));
    return true;
  }
  function clearRules() { state.rules = []; state.rev = ''; state.source = 'none'; }
  function setEnabled(b) { state.enabled = !!b; Debug.event('skin_enabled', b); }
  function setPort(p) { const n = parseInt(p, 10); if (n > 0) { state.port = n; return n; } return state.port; }
  function swap(url) {
    if (!state.enabled || !state.rules.length) return '';
    const r = matchRule(url, state.rules);
    if (r) state.hits++;
    return r;
  }
  function pull() {
    if (typeof fetch !== 'function') return;
    const url = 'http://127.0.0.1:' + state.port + '/rules';
    fetch(url, { cache: 'no-store' })
      .then((r) => r.json())
      .then((d) => { if (d && d.rules) setRules(d.rules, d.rev, 'server'); })
      .catch(() => {});
  }
  // 仅浏览器环境安装 hook（node 无 window/document → 跳过，避免改全局原型）
  function installHooks(doc) {
    const d = doc || (typeof document !== 'undefined' ? document : null);
    if (!d) return false;
    try {
      const XHR = (typeof window !== 'undefined' && window.XMLHttpRequest);
      if (XHR) {
        const proto = XHR.prototype; const origOpen = proto.open;
        proto.open = function (method, u) {
          try { const n = swap(u); if (n) u = n; } catch (e) {}
          return origOpen.apply(this, arguments.length > 2 ? [method, u].concat([].slice.call(arguments, 2)) : [method, u]);
        };
      }
    } catch (e) {}
    try {
      if (typeof window !== 'undefined' && window.HTMLImageElement) {
        const desc = Object.getOwnPropertyDescriptor(window.HTMLImageElement.prototype, 'src');
        if (desc && desc.set) {
          Object.defineProperty(window.HTMLImageElement.prototype, 'src', {
            configurable: true, enumerable: desc.enumerable,
            get: function () { return desc.get.call(this); },
            set: function (v) { try { const n = swap(v); if (n) v = n; } catch (e) {} return desc.set.call(this, v); },
          });
        }
      }
    } catch (e) {}
    return true;
  }
  load();
  return { swap, setRules, addRule, clearRules, setEnabled, setPort, pull, installHooks, get state() { return state; } };
}

export function createSkinPanel(deps) {
  const ui = deps.ui || { el, panel, button, toggle };
  const ctrl = deps.ctrl;

  const p = ui.panel('自走棋助手 · 换肤');
  const status = ui.el('div', { style: { marginBottom: '4px' } });
  p.add(status);
  const list = ui.el('div', { style: { fontSize: '11px', maxHeight: '160px', overflow: 'auto' } });
  p.add(list);

  const pInput = ui.el('input', { type: 'text', placeholder: '匹配串(路径片段)' });
  const uInput = ui.el('input', { type: 'text', placeholder: '替换URL(本机http)' });
  const addBtn = ui.button('添加', () => { if (ctrl.addRule(pInput.value, uInput.value)) { pInput.value = ''; uInput.value = ''; render(); } });
  const row = ui.el('div');
  row.appendChild(pInput); row.appendChild(uInput); row.appendChild(addBtn);
  p.add(row);

  const ctrlRow = ui.el('div');
  ctrlRow.appendChild(ui.button('从助手拉取', () => ctrl.pull()));
  ctrlRow.appendChild(ui.button('清空', () => { ctrl.clearRules(); render(); }));
  ctrlRow.appendChild(ui.button('暂停/恢复', () => { ctrl.setEnabled(!ctrl.state.enabled); render(); }));
  p.add(ctrlRow);

  function render() {
    const s = ctrl.state;
    status.textContent = '状态: ' + (s.enabled ? '启用' : '暂停') + ' · 规则 ' + s.rules.length + ' 条 · 命中 ' + s.hits;
    list.textContent = '';
    s.rules.forEach((r) => list.appendChild(ui.el('div', { text: r.pattern + ' → ' + r.url })));
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
  id: 'skin',
  name: '换肤',
  enabled: true,
  load(ctx) {
    if (!hasDOM) return { unload() {} };
    const ctrl = createSkinController({ Config: ctx.Config, Debug: ctx.Debug });
    const view = createSkinPanel({ ui: { el, panel, button, toggle }, ctrl });
    ctrl.installHooks();
    return { unload() { view.destroy(); } };
  },
};
