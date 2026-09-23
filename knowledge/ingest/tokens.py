"""tiktoken counts. If the BPE ranks can't download, an estimate that over-counts keeps chunks under the limit."""

from __future__ import annotations

import logging
import math
import re
from collections.abc import Callable
from functools import lru_cache

logger = logging.getLogger(__name__)

ENCODING_NAME = "o200k_base"
_WORDISH = re.compile(r"\w+|[^\w\s]", re.UNICODE)


def approximate_token_count(text: str) -> int:
    if not text:
        return 0
    return max(len(_WORDISH.findall(text)), math.ceil(len(text) / 3.5))


@lru_cache
def get_counter() -> Callable[[str], int]:
    try:
        import tiktoken

        enc = tiktoken.get_encoding(ENCODING_NAME)
    except Exception as exc:
        logger.warning(
            "tiktoken %s unavailable (%s); using approximate token counts", ENCODING_NAME, type(exc).__name__
        )
        return approximate_token_count

    def count(text: str) -> int:
        return len(enc.encode(text, disallowed_special=())) if text else 0

    return count


def count_tokens(text: str) -> int:
    return get_counter()(text)
