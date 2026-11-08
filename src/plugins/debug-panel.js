// src/plugins/debug-panel.js
// 调试面板（规范 §3）：消费 core/debug 的 UI——开关、80 条日志环缓冲查看、2000 事件时间线、
// 快照复制 / 导出 / 清除。createDebugPanel 与 DOM 解耦以便单测；默认导出为插件入口。
import { el, panel, button, toggle } from '../core/ui.js';

function download(text, name, doc) {
  try {
    const a = doc.createElement('a');
    a.href = 'data:application/json;charset=utf-8,' + encodeURIComponent(text);
    a.download = name;
    doc.body.appendChild(a);
    a.click();
    doc.body.removeChild(a);
  } catch (e) { /* 浏览器外忽略 */ }
}

export function createDebugPanel(deps) {
  const Debug = deps.Debug;
  const doc = deps.document;
  const ui = deps.ui || { el, panel, button, toggle };

  const p = ui.panel('自走棋助手 · 调试面板');
  p.add(ui.toggle('启用调试', () => Debug.isEnabled(), (v) => Debug.setEnabled(v)));
  const btnRow = ui.el('div');
  btnRow.appendChild(ui.button('复制快照', () => Debug.copySnapshot()));
  btnRow.appendChild(ui.button('导出全部', () => download(Debug.exportAll(), 'sgs-debug.json', doc)));
  btnRow.appendChild(ui.button('清除', () => Debug.clear()));
  p.add(btnRow);

  const logView = ui.el('pre', { style: { whiteSpace: 'pre-wrap', fontSize: '11px', maxHeight: '160px', overflow: 'auto', margin: '4px 0' } });
  const tlView = ui.el('div', { style: { fontSize: '11px', maxHeight: '110px', overflow: 'auto' } });
  p.add(logView);
  p.add(tlView);

  function render() {
    const logs = Debug.getRecentLogs();
    logView.textContent = logs.length
      ? logs.slice().reverse().map((l) => `[${new Date(l.t).toLocaleTimeString()}][${l.level}] ${l.msg}`).join('\n')
      : '（暂无日志）';
    const tl = Debug.getTimeline();
    tlView.textContent = `时间线 ${tl.length} 条` + (tl.length ? '\n最近: ' + tl.slice(-6).map((e) => e.name).join(' · ') : '');
  }

  Debug.subscribe(render);
  render();

  return {
    panel: p,
    render,
    destroy() { Debug.unsubscribe(render); if (p.root && p.root.parentNode) p.root.parentNode.removeChild(p.root); },
  };
}

const hasDOM = typeof document !== 'undefined' && typeof document.createElement === 'function';

export default {
  id: 'debug-panel',
  name: '调试面板',
  enabled: true,
  load(ctx) {
    if (!hasDOM) return { unload() {} };
    const view = createDebugPanel({ Debug: ctx.Debug, document, ui: { el, panel, button, toggle } });
    return { unload() { view.destroy(); } };
  },
};
