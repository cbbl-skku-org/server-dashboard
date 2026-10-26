"""Tests for the site settings (app/cbbl/config.py)."""
import json
import os
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "app"))

from cbbl import config  # noqa: E402

EXAMPLE = os.path.join(HERE, "..", "config.example.json")


class Settings(unittest.TestCase):
    def write(self, data):
        fd, path = tempfile.mkstemp(suffix=".json")
        with os.fdopen(fd, "w") as f:
            f.write(data if isinstance(data, str) else json.dumps(data))
        self.addCleanup(os.remove, path)
        return path

    def test_no_file_means_defaults(self):
        self.assertEqual(config.load("/no/such/portal.json"), config.DEFAULTS)

    def test_override(self):
        s = config.load(self.write({"_comment": "ignored", "title": "Test Cluster", "nodes": ["a1"],
                                    "idle_rule": {"min_cpus": 2}, "health": {"disk_warn": 80}}))
        self.assertEqual((s["title"], s["nodes"], s["partition"]), ("Test Cluster", ["a1"], "cpu"))
        self.assertEqual(s["idle_rule"], dict(config.DEFAULTS["idle_rule"], min_cpus=2))
        self.assertEqual((s["health"]["disk_warn"], s["health"]["disk_bad"]), (80, 95))
        self.assertEqual(config.DEFAULTS["idle_rule"]["min_cpus"], 1)   # the defaults are not changed

    def test_mistakes_are_errors(self):
        for data, text in [({"titel": "x"}, "unknown setting 'titel'"), ({"health": {"disk": 1}}, "health.disk"),
                           ("{not json", "not valid JSON"), ("[]", "one JSON object")]:
            with self.assertRaises(ValueError) as e:
                config.load(self.write(data))
            self.assertIn(text, str(e.exception))

    def test_example_file_is_the_defaults(self):
        self.assertEqual(config.load(EXAMPLE), config.DEFAULTS)
        with open(EXAMPLE) as f:
            given = {k for k in json.load(f) if not k.startswith("_")}
        self.assertEqual(given, set(config.DEFAULTS))

    def test_node_texts(self):
        saved = config.SETTINGS["nodes"]
        try:
            for nodes, every, listed in [(["n1"], "the node", "n1"), (["n1", "n2"], "both nodes", "n1 and n2"),
                                         (["n1", "n2", "n3"], "all three nodes", "n1, n2 and n3"),
                                         ([f"n{i}" for i in range(12)], "all 12 nodes", None)]:
                config.SETTINGS["nodes"] = nodes
                self.assertEqual(config.all_nodes(), every)
                if listed:
                    self.assertEqual(config.node_list(), listed)
        finally:
            config.SETTINGS["nodes"] = saved


if __name__ == "__main__":
    unittest.main()
