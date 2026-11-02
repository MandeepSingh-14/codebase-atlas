from pathlib import Path

import pytest

from codemap.parser.ast_parser import parse_repo


@pytest.fixture(scope="session")
def sample_pkg_root() -> Path:
    return Path(__file__).parent / "fixtures" / "sample_pkg"


@pytest.fixture(scope="session")
def parsed_sample(sample_pkg_root):
    return parse_repo(sample_pkg_root)
