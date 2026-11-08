// src/plugins/season-data.js
// 赛季数据（规范便捷功能 P2-9）。数据模型取自 data/season/season.json 与 data/season/s<季>/meta.json：
//   {season,version,updated_at,fetched_at,counts{chess,spell,chess_this_season,spell_this_season},source,json_md5,config_mtime}
// 纯函数 normalizeSeasonInfo/formatSeason（可测）；createSeasonController 读取/刷新/summary；createSeasonPanel 展示。
import { el, panel, button, toggle } from '../core/ui.js';

const SEASON_GM_KEY = 'sgs_season_v1';

export function normalizeSeasonInfo(raw) {
  if (!raw || typeof raw !== 'object') return null;
  const season = String(raw.season != null ? raw.season : '').trim();
  if (!season) return null;
  const version = String(raw.version != null ? raw.version : season).trim();
  const updated_at = String(raw.updated_at || raw.fetched_at || '').trim();
  const c = raw.counts || {};
  const num = (v) => { const n = Number(v); return isFinite(n) ? n : 0; };
  const counts = {
    chess: num(c.chess),
    spell: num(c.spell),
    chess_this_season: num(c.chess_this_season),
    spell_this_season: num(c.spell_this_season),
  };
  const meta = {
    source: String(raw.source || ''),
    json_md5: String(raw.json_md5 || ''),
    config_mtime: String(raw.config_mtime || ''),
  };
  return { season, version, updated_at, counts, meta };
}

export function formatSeason(info) {
  if (!info) return '（无赛季数据）';
  const c = info.counts;
  return '赛季 ' + info.season +
    ' · 武将 ' + c.chess_this_season + '/' + c.chess +
    ' · 锦囊 ' + c.spell_this_season + '/' + c.spell +
    (info.updated_at ? ' · 更新 ' + info.updated_at : '');
}

export function createSeasonController(deps) {
  const Config = deps.Config || { get() { return null; } };
  const Debug = deps.Debug || { info() {}, warn() {}, error() {}, event() {} };
  const fetchInfo = deps.fetchInfo || null; // 可选：远程/异步拉取赛季信息
  let info = null;

  function load() { info = normalizeSeasonInfo(Config.get(SEASON_GM_KEY, null) || {}); return info; }
  function get() { return info; }
  function set(raw) { info = normalizeSeasonInfo(raw); return info; }
  function summary() {
    if (!info) return { ok: false, reason: 'no-data' };
    return { ok: true, season: info.season, counts: info.counts, format: formatSeason(info) };
  }
  // 刷新：优先 fetchInfo（注入），否则 reload 自 Config
  async function refresh() {
    if (typeof fetchInfo === 'function') {
      try {
        const raw = await fetchInfo();
        set(raw);
        Debug.event('season_refresh', info && info.season);
      } catch (e) { Debug.warn('season: 拉取失败', e && e.message); }
    } else {
      load();
    }
    return info;
  }
  load();
  return { get, summary, set, refresh, load };
}

export function createSeasonPanel(deps) {
  const ui = deps.ui || { el, panel, button, toggle };
  const ctrl = deps.ctrl;

  const p = ui.panel('自走棋助手 · 赛季数据');
  const status = ui.el('div', { style: { marginBottom: '4px' } });
  p.add(status);
  const detail = ui.el('div', { style: { fontSize: '11px' } });
  p.add(detail);
  const row = ui.el('div');
  row.appendChild(ui.button('刷新', () => ctrl.refresh().then(() => render())));
  p.add(row);

  function render() {
    const s = ctrl.summary();
    if (!s.ok) { status.textContent = '（无赛季数据）'; detail.textContent = ''; return; }
    status.textContent = s.format;
    const m = ctrl.get().meta;
    detail.textContent = '版本 ' + (ctrl.get().version || '-') + (m.source ? ' · ' + m.source : '') + (m.config_mtime ? ' · 配置 ' + m.config_mtime : '');
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
  id: 'season-data',
  name: '赛季数据',
  enabled: true,
  load(ctx) {
    if (!hasDOM) return { unload() {} };
    const fetchInfo = ctx.fetchSeasonInfo || null;
    const ctrl = createSeasonController({ Config: ctx.Config, Debug: ctx.Debug, fetchInfo });
    const view = createSeasonPanel({ ui: { el, panel, button, toggle }, ctrl });
    return { unload() { view.destroy(); } };
  },
};
