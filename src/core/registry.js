// src/core/registry.js
// 插件注册表：register / enable / disable / hotReload。每个 load/unload 经错误隔离。
import ErrorBoundary from './error-boundary.js';

let ctx = null;
const plugins = new Map(); // id -> { meta, instance, loaded }

const Registry = {
  init(context) { ctx = context; },
  register(meta) {
    if (!meta || !meta.id) throw new Error('plugin 缺少 id');
    plugins.set(meta.id, { meta, instance: null, loaded: false });
  },
  async load(id) {
    const p = plugins.get(id);
    if (!p || p.loaded) return;
    await ErrorBoundary.safeAsync('load:' + id, async () => {
      p.instance = p.meta.load ? await p.meta.load(ctx) : null;
      p.loaded = true;
    });
  },
  async unload(id) {
    const p = plugins.get(id);
    if (!p || !p.loaded) return;
    await ErrorBoundary.safeAsync('unload:' + id, async () => {
      if (p.meta.unload) await p.meta.unload(ctx, p.instance);
      p.loaded = false; p.instance = null;
    });
  },
  async toggle(id, on) {
    if (on === undefined) on = !plugins.get(id).loaded;
    if (on) await this.load(id); else await this.unload(id);
  },
  // 热插拔：unload + load 重新生效（规范 Q1）
  async hotReload(id) { await this.unload(id); await this.load(id); },
  get(id) { return plugins.get(id); },
  list() {
    return [...plugins.values()].map((p) => ({
      id: p.meta.id, name: p.meta.name, loaded: p.loaded, enabled: p.meta.enabled !== false,
    }));
  },
};

export default Registry;
