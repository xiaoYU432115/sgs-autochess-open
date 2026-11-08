// src/plugins/speed.js
// 游戏加速（规范便捷功能 P2-5）。clampToPreset/nextPreset 纯函数（可测），createSpeedController
// 调 runtime.setSpeed/getSpeed，createSpeedPanel 提供倍率按钮 + 循环切换。
import { el, panel, button, toggle } from '../core/ui.js';

const SPEED_GM_KEY = 'sgs_speed_v1';
export const SPEED_PRESETS = [1, 2, 3, 4, 8];

function presetsOf(cfg) {
  return (cfg && Array.isArray(cfg.presets) && cfg.presets.length) ? cfg.presets : SPEED_PRESETS;
}

// 把任意数值夹到最近的合法预设倍率
export function clampToPreset(v, presets) {
  const ps = presetsOf({ presets: presets });
  const num = Number(v);
  if (!isFinite(num)) return ps[0];
  if (num <= ps[0]) return ps[0];
  if (num >= ps[ps.length - 1]) return ps[ps.length - 1];
  let best = ps[0], bestD = Infinity;
  for (const p of ps) {
    const d = Math.abs(p - num);
    if (d < bestD) { bestD = d; best = p; }
  }
  return best;
}

// 在当前预设里取下一个（循环）；当前值不在列表则返回首个
export function nextPreset(current, presets) {
  const ps = presetsOf({ presets: presets });
  const i = ps.indexOf(Number(current));
  if (i < 0) return ps[0];
  return ps[(i + 1) % ps.length];
}

export function createSpeedController(deps) {
  const Runtime = deps.Runtime;
  const Config = deps.Config || { get() { return null; } };
  const Debug = deps.Debug || { info() {}, warn() {}, error() {}, event() {} };

  function presets() { return presetsOf(Config.get(SPEED_GM_KEY, null)); }
  function set(v) {
    const target = clampToPreset(v, presets());
    Runtime.setSpeed(target);
    Debug.event('speed_set', target);
    return target;
  }
  function cycle() {
    const nxt = nextPreset(Runtime.getSpeed(), presets());
    set(nxt);
    return nxt;
  }
  function get() { return Runtime.getSpeed(); }
  return { set, cycle, get, presets };
}

export function createSpeedPanel(deps) {
  const ui = deps.ui || { el, panel, button, toggle };
  const ctrl = deps.ctrl;

  const p = ui.panel('自走棋助手 · 加速');
  const cur = ui.el('div', { style: { marginBottom: '4px' } });
  p.add(cur);
  const row = ui.el('div', { style: {} });
  p.add(row);

  function render() {
    const ps = ctrl.presets();
    cur.textContent = '当前速度: ' + ctrl.get();
    row.textContent = '';
    ps.forEach((s) => row.appendChild(ui.button('×' + s, () => { ctrl.set(s); render(); })));
    row.appendChild(ui.button('切换', () => { ctrl.cycle(); render(); }));
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
  id: 'speed',
  name: '游戏加速',
  enabled: true,
  load(ctx) {
    if (!hasDOM) return { unload() {} };
    const ctrl = createSpeedController({ Runtime: ctx.Runtime, Config: ctx.Config, Debug: ctx.Debug });
    const view = createSpeedPanel({ ui: { el, panel, button, toggle }, ctrl });
    const cfg = (ctx.Config && ctx.Config.get(SPEED_GM_KEY, null)) || {};
    // 可选：启用且 autoApply 时立即套用默认倍率（避免无提示突变，默认关）
    if (cfg.enabled !== false && cfg.autoApply) ctrl.set(cfg.default != null ? cfg.default : ctrl.presets()[0]);
    return { unload() { view.destroy(); } };
  },
};
