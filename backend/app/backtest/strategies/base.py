"""策略协议与内置策略。"""

from __future__ import annotations

from typing import Protocol

from app.backtest.data_loader import BarContext, Panel


class Strategy(Protocol):
    """策略协议（见 docs/BACKTEST_DESIGN.md §6.1）。

    prepare 只允许使用面板中的静态信息；signal 收到的 BarContext 已被截断到 date。
    """

    name: str

    def prepare(self, panel: Panel, params: dict) -> None: ...

    def signal(self, date: str, ctx: BarContext) -> dict[str, float]: ...
