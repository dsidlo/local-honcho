"""Map a 1-10 volume level to a sample multiplier.

10 is full scale and 1 is one tenth. A Piper multiplier stored before this
scale, such as 0.8, still loads as the nearest level.
"""

from __future__ import annotations

PIPER_DEFAULT = 8
KOKORO_DEFAULT = 10


def level(value: object, default: int) -> int | float:
    """Return a volume from 1 to 10. Anything else uses ``default``.

    Whole numbers stay whole. A fractional level such as 7.5 is kept.
    """
    if isinstance(value, bool):
        return default
    if isinstance(value, int):
        return value if 1 <= value <= 10 else default
    if isinstance(value, float):
        if value.is_integer() and 1 < value <= 10:
            return int(value)
        if 0 < value <= 1:
            return min(10, max(1, round(value * 10)))
        if 1 < value <= 10:
            return value
        return default
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return default
        try:
            if "." in text:
                return level(float(text), default)
            return level(int(text), default)
        except ValueError:
            return default
    return default


def multiplier(value: object, default: int) -> float:
    """Return the sample gain for one volume level."""
    return level(value, default) / 10


def engine_multiplier(data: object, engine: str, default: int) -> float:
    """Return the gain stored for ``engine``, or full scale when it is absent."""
    if not isinstance(data, dict):
        return default / 10
    section = data.get(engine)
    if not isinstance(section, dict) or "volume" not in section:
        return default / 10
    return multiplier(section["volume"], default)
