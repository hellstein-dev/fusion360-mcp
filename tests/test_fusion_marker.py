"""Tests marked ``fusion`` are skipped by default and collected with ``-m fusion``.

The inner pytest run reuses the project's real ``[tool.pytest.ini_options]``.
"""

import json
import tomllib
from pathlib import Path

import pytest

PYPROJECT = Path(__file__).resolve().parent.parent / "pyproject.toml"

SAMPLE = """
import pytest

@pytest.mark.fusion
def test_needs_fusion():
    pass

def test_offline():
    pass
"""


@pytest.fixture
def sample(pytester: pytest.Pytester) -> pytest.Pytester:
    ini = tomllib.loads(PYPROJECT.read_text(encoding="utf-8"))["tool"]["pytest"]["ini_options"]
    pytester.makepyprojecttoml(
        "[tool.pytest.ini_options]\n"
        f"markers = {json.dumps(ini['markers'])}\n"
        f"addopts = {json.dumps(ini['addopts'])}\n"
    )
    pytester.makepyfile(test_sample=SAMPLE)
    return pytester


def test_fusion_tests_deselected_by_default(sample: pytest.Pytester) -> None:
    result = sample.runpytest("--strict-markers")
    result.assert_outcomes(passed=1, deselected=1)


def test_fusion_tests_collected_with_marker_flag(sample: pytest.Pytester) -> None:
    result = sample.runpytest("--strict-markers", "-m", "fusion", "-v")
    result.assert_outcomes(passed=1, deselected=1)
    result.stdout.fnmatch_lines(["*test_needs_fusion PASSED*"])
