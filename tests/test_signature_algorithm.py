import unittest

from tenx.signature_algorithm import ablation, build_gridweave_topology, sensitivity


class SignatureAlgorithmTests(unittest.TestCase):
    def setUp(self):
        self.demand = [5, 8, 6]
        self.candidates = [
            {"id": "A", "capex": 5, "covers": [0, 1]},
            {"id": "B", "capex": 4, "covers": [1, 2]},
            {"id": "C", "capex": 7, "covers": [0, 2]},
        ]

    def test_topology_covers_demand(self):
        result = build_gridweave_topology(self.demand, self.candidates, [1])
        self.assertIn("B", result)

    def test_empty_inputs_raise(self):
        with self.assertRaises(ValueError):
            build_gridweave_topology([], self.candidates, [])
        with self.assertRaises(ValueError):
            build_gridweave_topology(self.demand, [], [])

    def test_unavailable_critical_corridor_is_controlled(self):
        result = build_gridweave_topology([5], [{"id": "A", "capex": 1, "covers": [0]}], [99])
        self.assertEqual(result, ["A"])

    def test_single_coverer_does_not_loop_forever(self):
        result = build_gridweave_topology([5], [{"id": "A", "capex": 1, "covers": [0]}], [0])
        self.assertEqual(result, ["A"])

    def test_n_minus_one_ablation_is_executable(self):
        self.assertIsInstance(ablation(self.demand, self.candidates, [1]), list)

    def test_demand_sensitivity_is_reproducible(self):
        self.assertEqual(sensitivity(self.demand, self.candidates, [1], 1.2), sensitivity(self.demand, self.candidates, [1], 1.2))


if __name__ == "__main__":
    unittest.main()
