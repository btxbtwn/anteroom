"""The web Scout screen must have the same API routes as the desktop app."""
import ast
from pathlib import Path
import unittest


class HostedScoutRoutesTest(unittest.TestCase):
    def test_hosted_registers_scout_router(self):
        source = Path(__file__).parents[1] / 'vendoo_studio' / 'hosted.py'
        tree = ast.parse(source.read_text())
        routers = {
            node.value.id
            for node in ast.walk(tree)
            if isinstance(node, ast.Attribute)
            and node.attr == 'router'
            and isinstance(node.value, ast.Name)
        }
        self.assertIn('scout', routers)
