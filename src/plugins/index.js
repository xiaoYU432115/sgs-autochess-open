// src/plugins/index.js
// 插件聚合清单。每个功能一个单独文件（规范 Q1：单功能单 JS 文件）。
import select3Reward from './select3-reward.js';
import select3Equip from './select3-equip.js';
import debugPanel from './debug-panel.js';
import shopAuto from './shop-auto.js';
import dismiss from './dismiss.js';
import lord from './lord.js';
import hotkeys from './hotkeys.js';
import speed from './speed.js';
import skin from './skin.js';
import scenario from './scenario.js';
import replay from './replay.js';
import seasonData from './season-data.js';
import pluginManager from './plugin-manager.js';

const plugins = [
  select3Reward,
  select3Equip,
  debugPanel,
  shopAuto,
  dismiss,
  lord,
  hotkeys,
  speed,
  skin,
  scenario,
  replay,
  seasonData,
  pluginManager,
];

export default plugins;
