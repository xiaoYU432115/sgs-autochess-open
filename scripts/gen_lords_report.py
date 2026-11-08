import json
import os
import sys
import time

# v4.22★ 修复硬编码绝对路径(原为 r"d:\todesk\三国杀\自走棋助手\data")。
#   改成从 core.config 推导, 换目录/换机器都不会静默失效。
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from core.config import DATA_DIR


def generate_report():
    data_dir = DATA_DIR
    lords_path = os.path.join(data_dir, "tavern_lords.json")
    report_path = os.path.join(data_dir, "tavern_lords_report.txt")

    if not os.path.exists(lords_path):
        print(f"Error: {lords_path} not found")
        return

    try:
        with open(lords_path, "r", encoding="utf-8") as f:
            data = json.load(f)
        
        lines = [
            "// ============================================================",
            "// 自走棋主公/技能 库分层报告",
            f"// 生成时间: {time.strftime('%Y-%m-%d %H:%M:%S')}",
            "// ============================================================\n",
            "// [ID]     | [名称]       | [描述]",
            "// ------------------------------------------------------------"
        ]
        
        # 按 SeasonID 分组
        seasons = {}
        for lid, info in data.items():
            if not isinstance(info, dict): continue
            season = str(info.get("SeasonID") or info.get("season") or "Unknown")
            if season not in seasons: seasons[season] = []
            seasons[season].append((lid, info))
        
        for season in sorted(seasons.keys(), reverse=True):
            lines.append(f"\n// --- 赛季 {season} ---")
            items = sorted(seasons[season], key=lambda x: (len(x[0]), x[0]))
            
            # 分类: 主公(4位) vs 棋子效果/技能(6位)
            lords_group = [it for it in items if len(it[0]) == 4]
            effects_group = [it for it in items if len(it[0]) == 6]
            
            if lords_group:
                lines.append("// [主公角色]")
                for lid, info in lords_group:
                    name = info.get("n") or info.get("name") or "?"
                    desc = info.get("desc") or info.get("SkillDesc") or ""
                    desc = desc.replace("\n", " ").replace("\r", "")
                    lines.append(f"{str(lid).ljust(8)} | {str(name).ljust(12)} | {desc}")
            
            if effects_group:
                lines.append("\n// [棋子效果/技能描述]")
                for lid, info in effects_group:
                    name = info.get("n") or info.get("name") or "?"
                    desc = info.get("desc") or info.get("SkillDesc") or ""
                    desc = desc.replace("\n", " ").replace("\r", "")
                    lines.append(f"{str(lid).ljust(8)} | {str(name).ljust(12)} | {desc}")
        
        with open(report_path, "w", encoding="utf-8") as f:
            f.write("\n".join(lines))
        print(f"Report successfully generated at {report_path}")
        
    except Exception as e:
        print(f"Error generating report: {e}")

if __name__ == "__main__":
    generate_report()
