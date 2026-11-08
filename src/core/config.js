// src/core/config.js
// 统一配置层：Tampermonkey(GM_setValue) / MV3(chrome.storage.local) / localStorage 三适配。
// 内存同步缓存 + 持久化 + 订阅。顺序：GM_* > chrome.storage > localStorage > memory。
// 这样不管油猴还是 MV3 MAIN 世界都能持久化（MV3 MAIN 世界未必能拿到 chrome.storage，localStorage 兜底）。

const GM = (typeof GM_setValue !== 'undefined')
  ? { get: GM_getValue, set: GM_setValue, del: GM_deleteValue, list: GM_listValues }
  : null;

const memory = {};
const subs = {};

function lsGet(key) {
  try { const r = localStorage.getItem(key); return r == null ? undefined : JSON.parse(r); } catch (e) { return undefined; }
}
function lsSet(key, val) {
  try { localStorage.setItem(key, JSON.stringify(val)); } catch (e) {}
}

const Config = {
  get(key, def = null) {
    if (key in memory) return memory[key];
    if (GM) { const v = GM.get(key, def); memory[key] = v; return v; }
    return def;
  },
  set(key, val) {
    memory[key] = val;
    if (GM) {
      GM.set(key, val);
    } else if (typeof chrome !== 'undefined' && chrome.storage && chrome.storage.local) {
      chrome.storage.local.set({ [key]: val });
    }
    if (typeof localStorage !== 'undefined') lsSet(key, val);
    (subs[key] || []).forEach((fn) => { try { fn(val); } catch (e) {} });
  },
  subscribe(key, fn) {
    (subs[key] = subs[key] || []).push(fn);
  },
  // 异步载入：把 chrome.storage.local + localStorage 读入内存缓存（MV3 启动时调用）
  async init() {
    if (GM) return;
    if (typeof chrome !== 'undefined' && chrome.storage && chrome.storage.local) {
      await new Promise((resolve) => {
        chrome.storage.local.get(null, (items) => { Object.assign(memory, items || {}); resolve(); });
      });
    }
    if (typeof localStorage !== 'undefined') {
      for (let i = 0; i < localStorage.length; i++) {
        const k = localStorage.key(i);
        if (k && !(k in memory)) {
          const v = lsGet(k);
          if (v !== undefined) memory[k] = v;
        }
      }
    }
  },
};

export default Config;
