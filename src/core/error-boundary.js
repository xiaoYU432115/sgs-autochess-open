// src/core/error-boundary.js
// 每个插件的 load/run 都被 try/catch 包裹；错误上报到 Debug 与角标（规范 Q1 报错反馈）。
import Debug from './debug.js';

const ErrorBoundary = {
  safe(label, fn, ...args) {
    try { return fn(...args); } catch (e) {
      Debug.error('[ERR:' + label + ']', e && e.message ? e.message : String(e));
      return undefined;
    }
  },
  async safeAsync(label, fn, ...args) {
    try { return await fn(...args); } catch (e) {
      Debug.error('[ERR:' + label + ']', e && e.message ? e.message : String(e));
      return undefined;
    }
  },
};

export default ErrorBoundary;
