// src/core/debug.js
// 规范 §3：80 条日志环缓冲 + 整局 2000 条关键事件时间线 + 快照 + 导出。
const MAX_LOG = 80;
const MAX_TIMELINE = 2000;
const KEY = 'sgs_debug';

const logs = [];
const timeline = [];
let enabled = false;
const subs = [];
let snapshotProvider = null; // 由 bootstrap 注入（取游戏状态）

function fmt(x) {
  if (x instanceof Error) return x.message;
  try { return typeof x === 'object' ? JSON.stringify(x) : String(x); } catch (e) { return String(x); }
}
function persist() {
  try { sessionStorage.setItem(KEY, JSON.stringify({ logs, timeline, enabled })); } catch (e) {}
}
function notify() { subs.forEach((f) => f()); }

const Debug = {
  setSnapshotProvider(fn) { snapshotProvider = fn; },
  setEnabled(v) { enabled = !!v; persist(); notify(); },
  isEnabled() { return enabled; },

  log(level, ...args) {
    const entry = { t: Date.now(), level, msg: args.map(fmt).join(' ') };
    logs.push(entry);
    if (logs.length > MAX_LOG) logs.shift();
    if (enabled) console.log('[SGS][' + level + ']', ...args);
    persist();
    notify();
  },
  info(...a) { this.log('INFO', ...a); },
  warn(...a) { this.log('WARN', ...a); },
  error(...a) { this.log('ERROR', ...a); },

  // 关键事件进时间线（规范 §3.2 整局 2000 条）
  event(name, data) {
    timeline.push({ t: Date.now(), name, data });
    if (timeline.length > MAX_TIMELINE) timeline.shift();
    persist();
    notify();
  },

  getRecentLogs() { return logs.slice(); },
  getTimeline() { return timeline.slice(); },

  snapshot() {
    const state = snapshotProvider ? snapshotProvider() : null;
    return { ts: Date.now(), enabled, logs: logs.slice(), timeline: timeline.slice(), state };
  },
  copySnapshot() {
    const txt = JSON.stringify(this.snapshot(), null, 2);
    if (navigator.clipboard) navigator.clipboard.writeText(txt).catch(() => {});
    return txt;
  },
  exportAll() { return JSON.stringify(this.snapshot(), null, 2); },
  clear() { logs.length = 0; timeline.length = 0; persist(); notify(); },
  subscribe(fn) { subs.push(fn); },
  unsubscribe(fn) { const i = subs.indexOf(fn); if (i >= 0) subs.splice(i, 1); },
};

export default Debug;
