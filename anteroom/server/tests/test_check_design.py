"""scripts/check-design.py: each rule catches what DESIGN.md rules out and spares the allowed form."""

from __future__ import annotations

import sys
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parents[2] / "scripts"
sys.path.insert(0, str(SCRIPTS))

import importlib.util  # noqa: E402

spec = importlib.util.spec_from_file_location("check_design", SCRIPTS / "check-design.py")
assert spec and spec.loader
check_design = importlib.util.module_from_spec(spec)
spec.loader.exec_module(check_design)


def matches(rule: str, text: str) -> int:
    return len(check_design.RULES[rule][1].findall(text))


def test_colors_flag_hex_and_rgb_but_not_tokens_or_issue_numbers():
    assert matches("hardcoded-color", "color: #3fb950;") == 1
    assert matches("hardcoded-color", "background: rgba(0, 0, 0, 0.2);") == 1
    assert matches("hardcoded-color", "color: var(--success-foreground);") == 0
    assert matches("hardcoded-color", '"#1062 — Connect eBay"') == 0
    assert matches("hardcoded-color", 'href="#section-id"') == 0


def test_uppercase_flags_css_and_class_but_not_words_inside_names():
    assert matches("uppercase-label", "text-transform: uppercase;") == 1
    assert matches("uppercase-label", 'className="text-xs uppercase tracking-wide"') == 1
    assert matches("uppercase-label", "toUppercaseLabel()") == 0
    assert matches("uppercase-label", "normal-case") == 0


def test_weights_above_600_are_flagged():
    assert matches("heavy-weight", "font-weight: 700;") == 1
    assert matches("heavy-weight", 'className="font-bold"') == 1
    assert matches("heavy-weight", "font-weight: 600;") == 0
    assert matches("heavy-weight", 'className="font-semibold"') == 0


def test_native_controls_and_arbitrary_sizes():
    assert matches("native-control", '<select value={x}>') == 1
    assert matches("native-control", '<input type="checkbox" />') == 1
    assert matches("native-control", "<Select value={x}>") == 0
    assert matches("arbitrary-text-size", 'className="text-[13px]"') == 1
    assert matches("arbitrary-text-size", 'className="text-xs"') == 0


def test_gradients_are_flagged():
    assert matches("gradient", "background: linear-gradient(red, blue);") == 1
    assert matches("gradient", 'className="bg-linear-to-r"') == 1

