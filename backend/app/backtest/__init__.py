"""回测框架（P4.0 骨架）。

模块划分见 docs/BACKTEST_DESIGN.md §3.1：
  data_loader  面板装载与按日切片
  constraints  A 股交易约束（纯函数）
  broker       成交价、费用与资金约束
  portfolio    账户、持仓与估值
  engine       逐交易日事件循环
  metrics      绩效指标
  result_store 产物落盘与读取
  strategies   策略协议与内置策略
"""

from app.backtest.engine import BacktestEngine, LookaheadError
from app.backtest.models import BacktestConfig, BacktestResult

__all__ = ["BacktestEngine", "BacktestConfig", "BacktestResult", "LookaheadError"]
