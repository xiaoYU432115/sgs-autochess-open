// src/plugins/plugin-manager.js
// 插件管理器（规范便捷功能 P2-10）。复用 core/registry.js 的 register/load/unload/toggle/hotReload，
// 此插件只提供运行时 UI：列出插件、启停、热重载，并把开关持久化到 Config（与 bootstrap 的
// 'plugin_<id>_enabled' 读取一致）。纯函数 normalizePluginList（可测）。
import { el, panel, button, toggle } from '../core/ui.js';

export function normalizePluginList(list) {
  const out = [];
  for (const p of (list || [])) {
    if (!p || typeof p !== 'object') continue; // 跳过 null / 非对象
    const id = String(p.id != null ? p.id : '');
    if (!id) continue;
    out.push({ id, name: String(p.name != null ? p.name : id), loaded: !!p.loaded });
  }
  return out;
}

export function createPluginManagerController(deps) {
  const Registry = deps.Registry;
  const Config = deps.Config || { get() { return null; }, set() {} };
  const Debug = deps.Debug || { info() {}, warn() {}, error() {}, event() {} };

  function list() { return normalizePluginList(Registry.list()); }
  function isLoaded(id) { const p = Registry.get(id); return !!(p && p.loaded); }
  async function toggle(id, on) {
    await Registry.toggle(id, on);
    Config.set('plugin_' + id + '_enabled', on !== false); // 持久化，重启后保持
    Debug.event('plugin_toggle', id, on !== false);
  }
  async function hotReload(id) {
    await Registry.hotReload(id);
    Debug.event('plugin_hotreload', id);
  }
  return { list, isLoaded, toggle, hotReload };
}

export function createPluginManagerPanel(deps) {
  const ui = deps.ui || { el, panel, button, toggle };
  const ctrl = deps.ctrl;

  const p = ui.panel('自走棋助手 · 插件管理');
  const status = ui.el('div', { style: { marginBottom: '4px' } });
  p.add(status);
  const listEl = ui.el('div', { style: { fontSize: '11px', maxHeight: '200px', overflow: 'auto' } });
  p.add(listEl);

  function render() {
    listEl.textContent = '';
    ctrl.list().forEach((pl) => {
      const row = ui.el('div', { style: { margin: '2px 0' } });
      row.appendChild(ui.el('span', { text: pl.name + (pl.loaded ? ' · 已启用' : ' · 已停用') + '  ' }));
      row.appendChild(ui.button(pl.loaded ? '停用' : '启用', () => ctrl.toggle(pl.id, !pl.loaded).then(render)));
      row.appendChild(ui.button('重载', () => ctrl.hotReload(pl.id).then(render)));
      listEl.appendChild(row);
    });
    status.textContent = '插件数: ' + ctrl.list().length;
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
  id: 'plugin-manager',
  name: '插件管理',
  enabled: true,
  load(ctx) {
    if (!hasDOM) return { unload() {} };
    const ctrl = createPluginManagerController({ Registry: ctx.Registry, Config: ctx.Config, Debug: ctx.Debug });
    const view = createPluginManagerPanel({ ui: { el, panel, button, toggle }, ctrl });
    return { unload() { view.destroy(); } };
  },
};
