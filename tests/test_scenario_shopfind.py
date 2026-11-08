import unittest
from unittest.mock import patch

from ui import scenario_data
from ui.scenario_tab import ScenarioTabBuilder


class ScenarioShopFindTests(unittest.TestCase):
    def test_quantity_suffix_is_removed_before_id_lookup(self):
        with patch.object(
            scenario_data.IDMAP,
            "find_ids_by_name",
            side_effect=lambda name: ["21003211", "21003212"] if name == "孙策" else [],
        ) as find_ids:
            step = scenario_data._scenario_fill_target_ids({
                "op": "shopFind",
                "target": ["孙策*3"],
            })

        self.assertEqual(step["targetIds"], ["21003211", "21003212"])
        find_ids.assert_called_once_with("孙策")
        self.assertNotIn("targetGroups", step)

    def test_transport_groups_include_card_plan_ids_without_persisting_them(self):
        ids_by_name = {
            "孙策": ["21003211", "21003212"],
            "陈珪": ["21008081", "21008082"],
        }
        with patch.object(
            scenario_data.IDMAP,
            "find_ids_by_name",
            side_effect=lambda name: ids_by_name.get(name, []),
        ):
            step = scenario_data._scenario_fill_target_ids({
                "op": "shopFind",
                "target": ["孙策*3"],
                "cardPlan": "陈珪 2 off",
            })
            groups = scenario_data._scenario_shopfind_target_groups(step)

        self.assertEqual(step["targetIds"], [
            "21003211", "21003212", "21008081", "21008082",
        ])
        self.assertEqual(groups, [
            {"key": "孙策", "aliases": ["孙策", "21003211", "21003212"]},
            {"key": "陈珪", "aliases": ["陈珪", "21008081", "21008082"]},
        ])
        self.assertNotIn("targetGroups", step)

    def test_duplicate_target_and_card_plan_entries_are_grouped_once(self):
        with patch.object(
            scenario_data.IDMAP,
            "find_ids_by_name",
            return_value=["21003211"],
        ):
            groups = scenario_data._scenario_shopfind_target_groups({
                "op": "shopFind",
                "target": "孙策:3,孙策",
                "cardPlan": "孙策 2 off",
            })

        self.assertEqual(len(groups), 1)
        self.assertEqual(groups[0]["key"], "孙策")

    def test_collection_adds_groups_only_to_transport_payload(self):
        builder = object.__new__(ScenarioTabBuilder)
        builder.scenario_scripts = [{
            "id": "test",
            "name": "shop-find",
            "steps": [{"op": "shopFind", "target": ["孙策*3"]}],
        }]
        builder._scenario_warn_if_script_old = lambda: None
        with patch.object(
            scenario_data.IDMAP,
            "find_ids_by_name",
            return_value=["21003211"],
        ):
            payload = builder._scenario_collect(include_transport_fields=True)
            saved = builder.scenario_scripts[0]["steps"][0]
            exported = builder._scenario_collect()

        self.assertEqual(payload[0]["steps"][0]["targetGroups"], [
            {"key": "孙策", "aliases": ["孙策", "21003211"]},
        ])
        self.assertNotIn("targetGroups", saved)
        self.assertNotIn("targetGroups", exported[0]["steps"][0])

    def test_preview_name_check_ignores_quantity_suffix(self):
        builder = object.__new__(ScenarioTabBuilder)
        with patch.object(
            scenario_data.IDMAP,
            "find_ids_by_name",
            side_effect=lambda name: ["21003211"] if name == "孙策" else [],
        ) as find_ids:
            missing = builder._scenario_missing_names({
                "op": "shopFind",
                "target": ["孙策*3"],
            })

        self.assertEqual(missing, [])
        find_ids.assert_called_once_with("孙策")


if __name__ == "__main__":
    unittest.main()
