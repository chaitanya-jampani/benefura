from __future__ import annotations

from collections.abc import Iterator

import pytest

from tests.chat.helpers import ChatHarness, build_harness


@pytest.fixture
def harness() -> Iterator[ChatHarness]:
    yield build_harness()
