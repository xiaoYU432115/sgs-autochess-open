// src/plugins/dismiss.js
// 一键卖牌 / 弃锦囊（规范便捷功能 P2-2）。decideSell 纯函数（可测），createDismissController 负责下单，
// createDismissPanel 提供 DOM 面板（武将/锦囊分流，呼应 RUNTIME_NOTES §5/§10.1：发错协议服务器静默不处理）。
import { el, panel, button, toggle } from '../core/ui.js';

const DISMISS_GM_KEY = 'sgs_dismiss_v1';

// 给定手牌武将与规则，返回应卖出（回收）的 goodsID 列表。
// 规则：keepLocked 默认保留锁定；maxCost 限制只卖价 <= maxCost（null 表示不限）。
export function decideSell(pieces, cfg) {
  const keepLocked = cfg.keepLocked !== false;
  const maxCost = cfg.maxCost != null ? cfg.maxCost : Infinity;
  return (pieces || [])
    .filter((p) => {
      if (p == null || p.id == null) return false;
      if (keepLocked && p.locked) return false;
      if (p.cost != null && p.cost > maxCost) return false;
      return true;
    })
    .map((p) => p.id);
}

export function createDismissController(deps) {
  const Runtime = deps.Runtime;
  const Config = deps.Config || { get() { return null; } };
  const Debug = deps.Debug || { info() {}, warn() {}, error() {}, event() {} };

  function cfg() { return Config.get(DISMISS_GM_KEY, null) || {}; }
  function sell(id) { if (id == null) return; Debug.event('dismiss_sell', id); Runtime.reqShopRecycleChess(id); }
  function discardSpell(id) { if (id == null) return; Debug.event('dismiss_spell', id); Runtime.reqChessDeleteSpell(id); }
  function sellAll() {
    const ids = decideSell(Runtime.getHandChess(), cfg());
    ids.forEach(sell);
    return ids;
  }
  return { sell, discardSpell, sellAll };
}

// DOM 面板：列出手牌武将与"卖"按钮 + 一键卖出可卖 + 弃锦囊输入。
export function createDismissPanel(deps) {
  const Runtime = deps.Runtime;
  const Debug = deps.Debug;
  const ui = deps.ui || { el, panel, button, toggle };
  const ctrl = deps.ctrl;

  const p = ui.panel('自走棋助手 · 遣散');
  const btnRow = ui.el('div');
  btnRow.appendChild(ui.button('一键卖出可卖', () => ctrl.sellAll()));
  p.add(btnRow);

  const list = ui.el('div', { style: { fontSize: '11px', maxHeight: '200px', overflow: 'auto' } });
  p.add(list);

  const spellRow = ui.el('div', { style: { marginTop: '4px' } });
  const input = ui.el('input', { type: 'text', placeholder: '锦囊ID', style: { width: '60px' } });
  spellRow.appendChild(input);
  spellRow.appendChild(ui.button('弃锦囊', () => { const id = Number(input.value); if (id) ctrl.discardSpell(id); input.value = ''; }));
  p.add(spellRow);

  function render() {
    const pieces = Runtime.getHandChess();
    list.textContent = '';
    if (!pieces.length) { list.appendChild(ui.el('div', { text: '（手牌为空）' })); return; }
    pieces.forEach((pc) => {
      const label = ui.el('span', { text: `ID:${pc.id} 价:${pc.cost}${pc.locked ? '(锁)' : ''} ` });
      const sellBtn = ui.button('卖', () => ctrl.sell(pc.id));
      list.appendChild(ui.el('div', {}, [label, sellBtn]));
    });
  }

  const E = ['UI_UPDATE_HAND_CARD', 'ANI_ADD_HAND_CARD', 'ANI_CHESS_RECYCLE', 'DELETE_CHESS', 'UI_UPDATE_HAND_CARD_NUM'];
  E.forEach((e) => Runtime.on(e, panel, render));
  render();

  return {
    panel: p,
    refresh: render,
    destroy() {
      E.forEach((e) => Runtime.off(e, panel, render));
      if (p.root && p.root.parentNode) p.root.parentNode.removeChild(p.root);
    },
  };
}

const hasDOM = typeof document !== 'undefined' && typeof document.createElement === 'function';

export default {
  id: 'dismiss',
  name: '一键卖牌/弃锦囊',
  enabled: true,
  load(ctx) {
    if (!hasDOM) return { unload() {} };
    const ctrl = createDismissController({ Runtime: ctx.Runtime, Config: ctx.Config, Debug: ctx.Debug });
    const view = createDismissPanel({ Runtime: ctx.Runtime, Debug: ctx.Debug, ui: { el, panel, button, toggle }, ctrl });
    return { unload() { view.destroy(); } };
  },
};
