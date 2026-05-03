from __future__ import annotations

from functools import lru_cache
from typing import Any


def count_tokens(text: str, *, encoder_name: str = "cl100k_base") -> int:
    if not text:
        return 0
    try:
        encoder = _encoder(encoder_name)
        return len(encoder.encode(text))
    except Exception:
        return max(1, (len(text) + 3) // 4)


@lru_cache(maxsize=8)
def _encoder(encoder_name: str) -> Any:
    import tiktoken

    return tiktoken.get_encoding(encoder_name)
