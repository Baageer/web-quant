"""数据模式枚举：offline / auto / live。

- offline：只读本地数据，不访问 TuShare；
- auto：本地完整时直接返回，缺失时允许拉取；
- live：优先实时拉取，失败再回退本地。
"""

from __future__ import annotations

from enum import Enum


class DataMode(str, Enum):
    OFFLINE = "offline"
    AUTO = "auto"
    LIVE = "live"

    @classmethod
    def parse(cls, value: str | None) -> "DataMode":
        text = (value or "").strip().lower()
        for mode in cls:
            if mode.value == text:
                return mode
        return cls.OFFLINE

    @property
    def allows_network(self) -> bool:
        return self is not DataMode.OFFLINE

    @property
    def prefers_live(self) -> bool:
        return self is DataMode.LIVE
