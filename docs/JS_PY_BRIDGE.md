# JS 扩展 ↔ Python 助手 桥接契约（P4 对齐文档）

> 自动生成于 2026-11-04 自动驾驶第 18 批。目的：明确浏览器扩展（`src/`，由 `build.mjs` 产出油猴 `.user.js` 与 MV3 `extension/`）和桌面助手（Python `core/`+`ui/`+`tools/`）之间的数据契约，避免两边各写一套。
>
> **结论**：`core/` `ui/` `dev/` `tools/` 均为活跃生产代码（`main.py` 直接 import `ui.app`），**不要删除**。新 JS 架构只复用 Python 产出的**数据**，不重写其逻辑。

## 一、两条桥接通道

1. **GM 存储**：JS 插件在页面内通过 `Config`（封装 `GM_getValue/GM_setValue`）持久化；Python 侧通过"资源替换 / 配置推送"页面把数据写入对应 GM key，扩展下次加载即生效。
2. **HTTP 接口**：`serve_userscript.py`（本地 8766）负责脚本安装/更新；换肤 `skin` 插件的 `pull()` 直接 `fetch` 助手的 `/rules` 端点拉取资源替换规则（已在 `src/plugins/skin.js` 实现）。

## 二、JS 插件消费的 Config key ↔ Python 应写入的内容

| GM key | 消费插件 | 期望形状 | Python 生产方 |
|---|---|---|---|
| `sgs_season_v1` | `season-data` | `{season,version,updated_at\|fetched_at,counts{chess,spell,chess_this_season,spell_this_season},source,json_md5,config_mtime}` | `tools/season_config.py` 产出 `data/season/s<季>/meta.json`（字段一致，可直搬） |
| `sgs_debug_enabled` | `bootstrap` | `true/false` | 助手"调试"开关页 |
| `plugin_<id>_enabled` | `bootstrap` + `plugin-manager` | `true/false` | 插件管理页（运行时也可由插件自身写回） |
| `sgs-ext-scripts` | 脚本注册表（`bridge/sgs_ext/lib/scriptreg.js`） | `{type:'sgs-ext-scripts',version,scripts:[...]}` | 资源替换/用户脚本页 |

> ⚠ 对齐缺口：Python 侧"配置推送"目前未显式写 `sgs_season_v1`。建议在 `season_config.py` / `season_update_tab.py` 跑完换季后，把 `meta.json` 内容原样写入该 GM key（形状已兼容 `normalizeSeasonInfo`）。

## 三、JS 侧复用（不重写）的 Python 能力

- **Config.sgs 解密**：`core/sgs_decrypt_util.py`（`decrypt_sgs_file`，wasmtime OFB 解密 + 解压）。JS 无等价实现，靠 Python 产出 `data/season/s<季>/TavernChessConfig.json`。
- **ID / 费用 / 品质映射**：`core/id_map.py`（`name_of` / `chess_cost_of` / `qualities_of` / `expand_qualities`）。JS 决策层（经济/选将）复用派生 JSON（`chess.json`/`tavern_chess_s<季>.json`），不直接调 id_map。
- **赛季配置抓取 / 体检**：`tools/season_config.py`、`tools/season_check.py`。JS `season-data` 只读结果，不参与抓取。

## 四、可归档候选（**勿自动删除**，交人工决定）

- `自走棋助手_备份_20261102_203335_before_debug/`、`自走棋助手_备份_20261102_224811_pre_full/`：整目录快照。
- `archive/`、`extension.legacy/`、`_bili/`、`_frames2/`~`_frames5/` 及 `*_log/`、`_frames3_log_ocr.txt`：调试帧 / OCR / 旧验证脚本。
- `*.bak_stopall` / `*.bak-*` / `instances/**/LOG.old` / `__pycache__` / `*.pyc`：回退备份 / 浏览器 profile 日志 / 编译缓存（可随版本管理或清理脚本处理）。
