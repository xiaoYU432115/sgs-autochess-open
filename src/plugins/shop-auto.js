// src/plugins/shop-auto.js
// 商店自动化（规范便捷功能）：升级 / 买将 / 刷新，按经济决策。decide 为纯函数（可测），createShopRunner 负责节流与下单。
const SHOP_GM_KEY = 'sgs_shop_auto_v1';

// 决策优先级：升级到目标等级 → 买最便宜可负担（手牌未满）→ 刷新（开启且留足保底）。
export function decide(st, cfg) {
  // 1. 升级到目标等级（金币足够）
  if (cfg.autoLevel && st.level != null && cfg.targetLevel != null && st.level < cfg.targetLevel) {
    const need = st.levelUpCost != null ? st.levelUpCost : 0;
    if (st.coins == null || st.coins >= need) return { type: 'level' };
  }
  // 2. 买将（手牌未满 + 买得起，按策略选）
  if (cfg.autoBuy && st.shopCards && st.shopCards.length) {
    const handOk = st.handLimit == null || (st.handCount == null ? true : st.handCount < st.handLimit);
    if (handOk) {
      const affordable = st.shopCards.filter(
        (c) => c.cost != null && (st.coins == null || c.cost <= st.coins) && (cfg.buyMaxCost === 0 || c.cost <= cfg.buyMaxCost),
      );
      if (affordable.length) {
        affordable.sort((a, b) => (cfg.buyStrategy === 'best' ? b.cost - a.cost : a.cost - b.cost));
        return { type: 'buy', id: affordable[0].id };
      }
    }
  }
  // 3. 刷新（开启且刷新后留足保底金币）
  if (cfg.autoReroll && st.shopCards && st.shopCards.length) {
    const rc = st.rerollCost != null ? st.rerollCost : 0;
    if (st.coins != null && st.coins - rc >= cfg.keepCoins) return { type: 'reroll' };
  }
  return { type: 'none' };
}

export function createShopRunner(deps) {
  const Runtime = deps.Runtime;
  const Config = deps.Config || { get() { return null; } };
  const Debug = deps.Debug || { info() {}, warn() {}, error() {}, event() {} };
  const clock = deps.clock || { now: () => Date.now() };
  const EVT = [
    'UI_OPEN_SHOP', 'UI_UPDATE_COIN_NUM', 'UI_UPDATE_SHOP_LEVEL', 'UI_UPDATE_SHOP_LEVELUP_COST',
    'UI_UPDATE_SHOP_REFRESH_COST', 'UI_UPDATE_HAND_CARD_NUM', 'UI_UPDATE_FREE_REFRESH_END_TIME',
  ];
  const state = { cooldownUntil: 0 };

  function cfg() {
    const c = Config.get(SHOP_GM_KEY, null) || {};
    return {
      autoLevel: c.autoLevel !== false,
      autoBuy: c.autoBuy !== false,
      autoReroll: !!c.autoReroll,
      keepCoins: c.keepCoins != null ? c.keepCoins : 0,
      targetLevel: c.targetLevel != null ? c.targetLevel : 999,
      buyMaxCost: c.buyMaxCost != null ? c.buyMaxCost : 0,
      buyStrategy: c.buyStrategy || 'cheapest',
      cooldownMs: c.cooldownMs != null ? c.cooldownMs : 800,
    };
  }

  function act() {
    const st = Runtime.getShopState();
    if (!st) { Debug.warn('[shop] 无法读取商店状态'); return; }
    const d = decide(st, cfg());
    if (!d || d.type === 'none') return;
    if (d.type === 'level') { Debug.event('shop_level', st.level); Runtime.reqShopLevelUp(); }
    else if (d.type === 'buy') { Debug.event('shop_buy', d.id); Runtime.reqShopBuyChess(d.id); }
    else if (d.type === 'reroll') { Debug.event('shop_reroll', st.coins); Runtime.shopRefresh(); }
  }

  function onEvt() {
    const c = cfg();
    const now = clock.now();
    if (now < state.cooldownUntil) return;
    state.cooldownUntil = now + c.cooldownMs;
    act();
  }

  const runner = {
    act,
    attach() { EVT.forEach((e) => Runtime.on(e, runner, onEvt)); },
    detach() { EVT.forEach((e) => Runtime.off(e, runner, onEvt)); },
    getState: () => state,
  };
  return runner;
}

export default {
  id: 'shop-auto',
  name: '商店自动（升级/买将/刷新）',
  enabled: true,
  load(ctx) {
    if (!ctx.Runtime) return { unload() {} };
    const runner = createShopRunner({ Runtime: ctx.Runtime, Config: ctx.Config, Debug: ctx.Debug });
    runner.attach();
    return { unload() { runner.detach(); } };
  },
};
