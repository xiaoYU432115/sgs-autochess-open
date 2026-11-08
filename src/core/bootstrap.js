// src/core/bootstrap.js
// 入口：探测运行环境(油猴/MV3) → 初始化 Debug/Config → 注册并加载启用插件 → 安装错误角标。
import Runtime from './runtime.js';
import Config from './config.js';
import Debug from './debug.js';
import Registry from './registry.js';
import ErrorBoundary from './error-boundary.js';
import { el, panel, button } from './ui.js';
import plugins from '../plugins/index.js';

function installBadge() {
  const b = el('div', {
    style: {
      position: 'fixed', left: '8px', bottom: '8px', zIndex: 100000,
      background: '#c53030', color: '#fff', font: '11px sans-serif',
      padding: '2px 6px', borderRadius: '4px', cursor: 'pointer', display: 'none',
    },
    text: 'SGS 错误',
  });
  b.addEventListener('click', () => alert(Debug.getRecentLogs().map((l) => l.msg).join('\n')));
  if (document.body) document.body.appendChild(b);
  // 任何插件报错 → 红色角标
  const orig = Debug.error.bind(Debug);
  Debug.error = (...a) => { b.style.display = 'block'; orig(...a); };
}

export async function start() {
  await Config.init();
  Debug.setEnabled(Config.get('sgs_debug_enabled', false));
  Debug.setSnapshotProvider(() => {
    const m = Runtime.getManager();
    if (!m) return { ready: false };
    return {
      ready: true, tableID: m.TableID, phase: m.Phase,
      shopLevel: m.selfChessPlayer && m.selfChessPlayer.shopLevel,
      coin: m.selfChessPlayer && m.selfChessPlayer.coin,
      waitSelectCards: !!Runtime.waitSelectCards(),
      waitSelectEquiments: !!Runtime.waitSelectEquiments(),
    };
  });

  Registry.init({ Runtime, Config, Debug, Registry });
  for (const p of plugins) {
    Registry.register(p);
    const on = Config.get('plugin_' + p.id + '_enabled', p.enabled !== false);
    if (on) await ErrorBoundary.safeAsync('load:' + p.id, () => Registry.load(p.id));
  }

  installBadge();
  Debug.info('[bootstrap] 完成，插件数 =', plugins.length);
}

// 油猴直接执行；MV3 content script 也直接执行
if (typeof document !== 'undefined') {
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', start);
  else start();
}
