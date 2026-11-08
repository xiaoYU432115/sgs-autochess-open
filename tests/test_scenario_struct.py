import unittest

from ui import scenario_struct as scenario


class ScenarioStructureTests(unittest.TestCase):
    def test_mixed_block_types_pair_with_their_own_closers(self):
        steps = [
            {"op": "ifStart"},
            {"op": "loopStart"},
            {"op": "ifEnd"},
            {"op": "wait"},
            {"op": "loopEnd"},
        ]

        depth, parent, close_of, _, _ = scenario.scenario_step_depths(steps)

        self.assertEqual(close_of[:2], [2, 4])
        self.assertEqual(parent[2], 0)
        self.assertEqual(parent[3], 1)
        self.assertEqual(parent[4], 1)
        self.assertEqual(depth[3], 1)
        self.assertEqual(scenario.scenario_validate(steps), [])

    def test_outline_uses_plain_indentation_not_tree_connectors(self):
        rows = scenario.scenario_outline_rows([
            {"op": "loopStart"},
            {"op": "ifStart"},
            {"op": "ifEnd"},
            {"op": "loopEnd"},
        ])

        self.assertEqual(rows[1]["prefix"], "  ")
        self.assertTrue(all(not any(mark in row["prefix"] for mark in "|─├└")
                            for row in rows))

    def test_else_pairs_with_if_when_a_loop_is_also_open(self):
        steps = [
            {"op": "ifStart"},
            {"op": "loopStart"},
            {"op": "else"},
            {"op": "ifEnd"},
            {"op": "loopEnd"},
        ]

        _, parent, _, _, _ = scenario.scenario_step_depths(steps)

        self.assertEqual(parent[2], 0)
        self.assertEqual(parent[3], 0)

    def test_repair_closes_mixed_unclosed_blocks_in_reverse_open_order(self):
        steps = [{"op": "loopStart"}, {"op": "ifStart"}]

        repaired, warnings = scenario.scenario_repair(steps)

        self.assertEqual([step["op"] for step in repaired],
                         ["loopStart", "ifStart", "ifend", "loopend"])
        self.assertEqual(len(warnings), 2)
        self.assertEqual(scenario.scenario_validate(repaired), [])

    def test_repair_leaves_a_valid_script_unchanged(self):
        steps = [
            {"op": "ifStart", "check": "gold", "cmp": ">=", "target": 3},
            {"op": "loopStart", "count": 2},
            {"op": "loopEnd"},
            {"op": "ifEnd"},
        ]

        repaired, warnings = scenario.scenario_repair(steps)

        self.assertEqual(repaired, steps)
        self.assertEqual(warnings, [])
        self.assertEqual(scenario.scenario_validate(steps), [])


if __name__ == "__main__":
    unittest.main()
