// src/plugins/replay.js
// 复盘导出（规范便捷功能 P2-8）。机制：把对局事件（Debug 事件流 + 手动 add）录制成时间线，
// 导出为 JSON / CSV。纯函数 normalizeRecord/toCSV/toJSON（可测）；createReplayController
// 收集/录制/导出/清空；createReplayPanel 计数 + 导出按钮 + 清空。
import { el, panel, button, toggle } from '../core/ui.js';

const SKIP_TYPES = new Set(['log', 'info', 'warn', 'error', 'clear', 'replay_add', 'debug_render']);

export function normalizeRecord(r) {
  if (!r || typeof r !== 'object') return null;
  const ts = r.ts != null ? Number(r.ts) : Date.now();
  if (!isFinite(ts)) return null;
  const round = r.round != null ? Number(r.round) : null;
  const type = String(r.type || 'info');
  const text = String(r.text != null ? r.text : '');
  return { ts, round: isFinite(round) ? round : null, type, text };
}

export function normalizeRecords(list) {
  const out = [];
  for (const r of (list || [])) { const n = normalizeRecord(r); if (n) out.push(n); }
  out.sort((a, b) => a.ts - b.ts);
  return out;
}

function csvCell(s) {
  if (/[",\n\r]/.test(s)) return '"' + String(s).replace(/"/g, '""') + '"';
  return String(s);
}

export function toCSV(records, cols) {
  const columns = cols || ['ts', 'round', 'type', 'text'];
  const header = columns.join(',');
  const rows = records.map((r) => columns.map((c) => csvCell(r[c] != null ? r[c] : '')).join(','));
  return [header].concat(rows).join('\n');
}

export function toJSON(records) {
  return JSON.stringify({ type: 'sgs-chess-replay', version: 1, count: records.length, records }, null, 2);
}

export function createReplayController(deps) {
  const Debug = deps.Debug || { info() {}, warn() {}, error() {}, event() {}, subscribe() {} };
  const download = deps.download || (() => false); // 注入下载器（测试替换，避免真触发浏览器下载）
  let records = [];

  function add(record) {
    const n = normalizeRecord(record);
    if (!n) return false;
    records.push(n);
    records.sort((a, b) => a.ts - b.ts);
    Debug.event('replay_add', n.type);
    return true;
  }
  function setAll(list) { records = normalizeRecords(list); }
  function clear() { records = []; }
  function count() { return records.length; }
  function exportJSON() { const data = toJSON(records); download('replay.json', data, 'application/json'); return data; }
  function exportCSV() { const data = toCSV(records); download('replay.csv', data, 'text/csv'); return data; }

  // 订阅 Debug 事件自动录制（过滤调试噪声），返回取消订阅函数
  function startRecording() {
    if (typeof Debug.subscribe !== 'function') return () => {};
    return Debug.subscribe((type, ...args) => {
      if (SKIP_TYPES.has(type)) return;
      const text = args.map((a) => (typeof a === 'object' ? JSON.stringify(a) : String(a))).join(' ');
      add({ ts: Date.now(), round: null, type, text });
    });
  }

  return {
    add, setAll, clear, count, exportJSON, exportCSV, startRecording,
    get records() { return records; },
  };
}

export function createReplayPanel(deps) {
  const ui = deps.ui || { el, panel, button, toggle };
  const ctrl = deps.ctrl;

  const p = ui.panel('自走棋助手 · 复盘导出');
  const status = ui.el('div', { style: { marginBottom: '4px' } });
  p.add(status);
  const list = ui.el('div', { style: { fontSize: '11px', maxHeight: '160px', overflow: 'auto' } });
  p.add(list);

  const row = ui.el('div');
  row.appendChild(ui.button('导出JSON', () => ctrl.exportJSON()));
  row.appendChild(ui.button('导出CSV', () => ctrl.exportCSV()));
  row.appendChild(ui.button('清空', () => { ctrl.clear(); render(); }));
  p.add(row);

  function render() {
    status.textContent = '记录数: ' + ctrl.count();
    list.textContent = '';
    ctrl.records.slice(-50).forEach((r) => {
      list.appendChild(ui.el('div', { text: '[' + (r.round != null ? r.round : '-') + '] ' + r.type + ': ' + r.text }));
    });
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
  id: 'replay',
  name: '复盘导出',
  enabled: true,
  load(ctx) {
    if (!hasDOM) return { unload() {} };
    const download = (name, content, mime) => {
      try {
        const blob = new Blob([content], { type: mime });
        const url = URL.createObjectURL(blob);
        const a = document.createElement('a');
        a.href = url; a.download = name; a.click();
        URL.revokeObjectURL(url);
        return true;
      } catch (e) { return false; }
    };
    const ctrl = createReplayController({ Debug: ctx.Debug, download });
    const view = createReplayPanel({ ui: { el, panel, button, toggle }, ctrl });
    const unsub = ctrl.startRecording();
    return { unload() { if (typeof unsub === 'function') unsub(); view.destroy(); } };
  },
};
