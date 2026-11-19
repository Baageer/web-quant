# web-quant A 股量化研究 Web 服务设计文档

> 状态：设计稿（待评审）
> 参考项目：`web3-quant-sandbox` 的 `src/dashboard` 模块
> 数据源：TuShare Pro
> 参考实现：`E:\workspace\2_quant\quant_system\data`

---

## 1. 背景与目标

### 1.1 背景

`web3-quant-sandbox` 的 `dashboard` 模块已经验证了一套适合研究与教学的数据服务模式：

- 以函数为唯一出口，由 HTTP 层做路由分发；
- `offline / auto / live` 三种数据模式；
- “本地快照 → 上游 → 直连数据源 → 样本兜底”的降级链；
- fixture / snapshot / manifest / 完整性校验的数据治理；
- 行情、机会雷达、K 线分析、信号计算各自独立成模块，互不耦合。

本项目把这套模式迁移到 A 股，使用 TuShare Pro 作为主数据源，实现三个核心功能：

1. **行情**：指数、涨跌分布、成交额、行业热度、涨跌幅榜。
2. **机会雷达**：全市场横截面打分与筛选，输出可解释的候选列表。
3. **K 线与交易信号**：单只股票多周期 K 线、技术指标、交易信号、交易计划与证据链。

### 1.2 目标

| 编号 | 目标 |
| --- | --- |
| G1 | 提供可直接运行的 A 股研究 Web 服务，后端 Python，前端 React |
| G2 | 数据默认离线可用，TuShare 不可用或未配置 token 时仍能演示 |
| G3 | 行情、雷达、K 线与信号三个功能端到端打通 |
| G4 | 所有信号可解释：给出评分分解、理由、风险标记和交易计划 |
| G5 | 数据链路可追溯：每条响应带 `source`、`as_of`、`stale`、`warnings` |

### 1.3 非目标（本期不做）

- 实盘交易、券商接口、自动下单；
- 完整回测框架与组合优化；
- 因子挖掘、机器学习选股；
- 知识图谱、LLM 信号（预留扩展位，不实现）；
- 分钟级或 Tick 级行情（本期以日线为主，周/月线由日线重采样）。

---

## 2. 用户场景与范围

| 场景 | 说明 |
| --- | --- |
| 盘后复盘 | 收盘后查看指数、涨跌分布、行业强弱、全市场机会榜 |
| 个股研究 | 搜索个股，查看日/周/月 K 线、均线、MACD、RSI、BOLL、量能 |
| 信号确认 | 查看系统给出的信号等级、评分分解、止损/目标位与仓位建议 |
| 教学演示 | 断网环境下用本地快照完整演示三个页面 |
| 数据运维 | 手动触发刷新，查看各数据集是否完整、是否过期 |

交易时段：`09:30-11:30`、`13:00-15:00`（Asia/Shanghai）。本期以收盘后数据为准，盘中仅展示最近一个已完成交易日的数据。

---

## 3. 总体架构

### 3.1 技术选型

| 层 | 选型 | 理由 |
| --- | --- | --- |
| 后端框架 | FastAPI + Uvicorn | 自动生成 OpenAPI，Pydantic 校验响应，适合多接口数据服务 |
| 数据处理 | pandas + numpy + pyarrow | 与参考代码一致，横截面与时间序列处理成熟 |
| 数据源 | tushare（`pro_api`） | 用户指定；支持日线、基础指标、资金流、涨跌停 |
| 存储 | Parquet（主数据）+ SQLite（元数据） | Parquet 列式压缩、按日分区；SQLite 存清单、任务、日历 |
| 调度 | APScheduler（进程内） | 盘后刷新、盘前股票池更新 |
| 前端 | React 18 + TypeScript + Vite | 与参考项目一致 |
| UI | Ant Design 5 | 表格、筛选器、抽屉、状态标签 |
| 图表 | ECharts 5 | K 线、成交量、MACD 多面板、dataZoom、markLine |
| 请求缓存 | TanStack Query | 轮询、缓存、失效控制 |
| 路由 | React Router 6 | 三个主页面 |

**决策记录**

| 决策 | 方案 | 备选 | 结论 |
| --- | --- | --- | --- |
| 后端框架 | FastAPI | 标准库 `http.server`（参考项目） | 选 FastAPI，接口多且需要 schema 校验 |
| K 线图 | ECharts | lightweight-charts（参考项目） | 选 ECharts，多面板与技术指标叠加更方便 |
| 主存储 | Parquet 按交易日分区 | 单文件 CSV / SQLite 全量 | 选 Parquet，全市场 10 年数据量可控且查询快 |
| 复权 | 存储原始价 + 复权因子，读取时派生 | 只存一种复权价 | 选前者，支持前端切换 qfq/hfq/raw |
| 信号计算 | 规则引擎 + 可配置权重 | LLM 直接判断 | 选规则引擎，可解释、可复现 |

### 3.2 分层结构

```
┌──────────────────────────────────────────────────────────┐
│ 前端 React                                               │
│ /market 行情   /radar 机会雷达   /kline K线与信号          │
└───────────────────────────┬──────────────────────────────┘
                            │ /api/*
┌───────────────────────────▼──────────────────────────────┐
│ 接入层  FastAPI routes                                   │
│ market / radar / kline / signal / data                   │
└───────────────────────────┬──────────────────────────────┘
┌───────────────────────────▼──────────────────────────────┐
│ 应用服务层 services                                      │
│ market_service / radar_service / kline_service /          │
│ signal_service / refresh_service                          │
└───────────────────────────┬──────────────────────────────┘
┌───────────────────────────▼──────────────────────────────┐
│ 领域计算层 domain                                        │
│ indicators / scoring / signals / trade_plan / regime      │
└───────────────────────────┬──────────────────────────────┘
┌───────────────────────────▼──────────────────────────────┐
│ 数据访问层 data                                          │
│ tushare_client / repository / calendar / adjust / universe│
└───────────────────────────┬──────────────────────────────┘
┌───────────────────────────▼──────────────────────────────┐
│ 存储层                                                   │
│ data/raw (Parquet) + data/meta (SQLite) + snapshots       │
└──────────────────────────────────────────────────────────┘
```

与 `dashboard` 的对应关系：

| dashboard 模块 | web-quant 对应 | 说明 |
| --- | --- | --- |
| `api.py` | `api/routes_*.py` + `services/*_service.py` | 接入层与业务分离 |
| `mode.py` | `core/data_mode.py` | offline / auto / live |
| `resolve.py` + `snapshot.py` + `catalog.py` | `data/repository.py` + `core/cache.py` | 快照读取与完整性校验 |
| `market.py` | `data/tushare_client.py` + `services/market_service.py` | 数据抓取与聚合分离 |
| `opportunity.py` | `domain/scoring.py` + `services/radar_service.py` | 横截面打分 |
| `kline_analysis.py` + `signal_analysis.py` | `domain/indicators.py` + `domain/signals.py` | K 线与信号 |
| `normalize.py` + `dataset_views.py` | `models/schemas.py` + service 内整形 | 统一响应结构 |
| `refresh.py` + `graph_scheduler.py` | `tasks/scheduler.py` + `refresh_service.py` | 定时刷新 |

---

## 4. 项目目录

```
web-quant/
├── DESIGN.md                        # 本文档
├── README.md                        # 启动说明（后续补）
├── .env.example                     # TS_TOKEN / 模式 / 端口
├── requirements.txt
├── backend/
│   ├── app/
│   │   ├── main.py                  # FastAPI 实例、静态资源托管
│   │   ├── api/
│   │   │   ├── deps.py              # 依赖注入（服务单例）
│   │   │   ├── routes_market.py
│   │   │   ├── routes_radar.py
│   │   │   ├── routes_kline.py
│   │   │   ├── routes_signal.py
│   │   │   └── routes_data.py
│   │   ├── core/
│   │   │   ├── config.py            # 环境变量与 YAML 配置
│   │   │   ├── data_mode.py         # offline / auto / live
│   │   │   ├── cache.py             # 内存 TTL 缓存
│   │   │   ├── errors.py            # 统一错误码
│   │   │   └── logging.py
│   │   ├── data/
│   │   │   ├── tushare_client.py    # TuShare Pro 封装
│   │   │   ├── repository.py        # Parquet / SQLite 读写
│   │   │   ├── calendar.py          # 交易日历
│   │   │   ├── adjust.py            # qfq / hfq 派生
│   │   │   └── universe.py          # 股票池过滤
│   │   ├── domain/
│   │   │   ├── indicators.py        # MA/EMA/MACD/RSI/BOLL/ATR/KDJ
│   │   │   ├── scoring.py           # 雷达横截面打分
│   │   │   ├── signals.py           # 趋势评分与信号等级
│   │   │   ├── trade_plan.py        # 入场/止损/目标/仓位
│   │   │   └── regime.py            # 市场状态
│   │   ├── services/
│   │   │   ├── market_service.py
│   │   │   ├── radar_service.py
│   │   │   ├── kline_service.py
│   │   │   ├── signal_service.py
│   │   │   └── refresh_service.py
│   │   ├── models/
│   │   │   └── schemas.py           # Pydantic 响应模型
│   │   └── tasks/
│   │       └── scheduler.py
│   └── tests/
│       ├── test_indicators.py
│       ├── test_adjust.py
│       ├── test_scoring.py
│       ├── test_signals.py
│       └── test_api_contract.py
├── frontend/
│   ├── package.json
│   ├── vite.config.ts
│   └── src/
│       ├── main.tsx
│       ├── App.tsx
│       ├── api/
│       │   ├── client.ts
│       │   └── types.ts
│       ├── layouts/MainLayout.tsx
│       ├── components/
│       │   ├── StatusBadge.tsx
│       │   ├── IndexCard.tsx
│       │   ├── BreadthPanel.tsx
│       │   ├── SectorHeatmap.tsx
│       │   ├── RankTable.tsx
│       │   ├── ScoreBreakdown.tsx
│       │   ├── TradePlanCard.tsx
│       │   ├── SignalBadge.tsx
│       │   └── charts/KlineChart.tsx
│       └── pages/
│           ├── market/MarketPage.tsx
│           ├── radar/RadarPage.tsx
│           └── kline/KlinePage.tsx
├── config/
│   ├── radar.yaml                   # 雷达权重、阈值、过滤条件
│   └── signal.yaml                  # 信号阈值、指标参数
├── data/
│   ├── raw/tushare/
│   │   ├── bars_by_date/YYYY/YYYYMMDD.parquet
│   │   ├── daily_basic/YYYY/YYYYMMDD.parquet
│   │   ├── adj_factor/YYYY/YYYYMMDD.parquet
│   │   ├── index_daily/YYYYMMDD.parquet
│   │   ├── moneyflow/YYYY/YYYYMMDD.parquet
│   │   ├── limit_list/YYYYMMDD.parquet
│   │   └── stock_basic.parquet
│   ├── raw/tushare/by_symbol/{ts_code}.parquet   # 按需构建的个股历史缓存
│   ├── snapshots/latest/                          # 派生结果快照
│   ├── meta/web_quant.db                          # SQLite 元数据
│   └── samples/                                   # 离线演示样本
└── scripts/
    ├── bootstrap_data.py            # 首次初始化
    ├── refresh_daily.py             # 手动刷新
    └── build_offline_samples.py     # 生成离线样本
```

---

## 5. 数据源设计（TuShare Pro）

### 5.1 认证与配置

- Token 从 `.env` 读取：`TS_TOKEN=...`，服务端持有，不下发前端。
- 参考 `quant_system/data/fetch_tushare_prices.py` 的 `read_token()` 与 `call_api()`：
  - HTTP POST 到 `https://api.tushare.pro`；
  - `params` 分页（`limit` / `offset`）；
  - 网络错误指数退避重试；
  - 识别“每分钟频率限制”并等待；
  - 识别“积分/权限不足”并抛出明确错误。

### 5.2 接口清单

| 数据集 | TuShare 接口 | 关键字段 | 用途 | 权限 |
| --- | --- | --- | --- | --- |
| 股票列表 | `stock_basic` | `ts_code,symbol,name,area,industry,market,list_date,list_status` | 股票池、行业 | 基础 |
| 交易日历 | `trade_cal` | `cal_date,is_open` | 判断最新交易日 | 基础 |
| 日线行情 | `daily` | `ts_code,trade_date,open,high,low,close,pre_close,change,pct_chg,vol,amount` | 行情、K 线、雷达 | 基础 |
| 每日指标 | `daily_basic` | `turnover_rate,volume_ratio,pe_ttm,pb,total_mv,circ_mv,total_share,float_share` | 雷达、流动性 | 基础 |
| 复权因子 | `adj_factor` | `ts_code,trade_date,adj_factor` | qfq/hfq | 基础 |
| 指数日线 | `index_daily` | `ts_code,trade_date,close,pct_chg,amount` | 指数卡片 | 基础 |
| 资金流 | `moneyflow` | `ts_code,net_mf_amount,buy_lg_amount,sell_lg_amount` | 主力资金因子 | 需积分 |
| 涨跌停 | `limit_list_d` | `ts_code,limit_type,first_time,last_time,open_times` | 涨停/跌停/炸板 | 需积分 |
| 涨跌停价 | `stk_limit` | `up_limit,down_limit` | 可交易性判断 | 需积分 |
| 停牌 | `suspend_d` | `ts_code,suspend_type,trade_date` | 过滤停牌 | 需积分 |

> 权限不足的策略：启动时探测接口可用性，缺失的数据集自动降级。`moneyflow` 不可用时资金因子按 0 分处理并在 `warnings` 标注；`limit_list_d` 不可用时用 `pct_chg` 近似涨跌停家数。

### 5.3 拉取策略

**全市场按交易日拉取优于逐股拉取**：

```python
pro.daily(trade_date="20261119")        # 一次返回全市场约 5000+ 行
pro.daily_basic(trade_date="20261119")
pro.adj_factor(trade_date="20261119")
```

- 单次返回上限约 6000 行，A 股全市场一天通常在一页内；
- 第一次初始化历史时，按交易日循环；
- 个股 K 线优先读 `by_symbol/{ts_code}.parquet`，缺失时从 `bars_by_date` 聚合构建；
- 逐股 `pro_bar` 仅作为补数兜底。

**限流设计**：

- `TushareClient` 内置令牌桶，默认 `120 次/分钟`，可由 `TS_CALLS_PER_MIN` 覆盖；
- 每个接口独立重试次数（默认 5 次），指数退避；
- 单飞锁：同一数据集同一交易日只允许一个刷新任务。

### 5.4 复权处理

存储 **原始价 + `adj_factor`**，读取时派生：

```
qfq_price = raw_price * adj_factor / adj_factor_latest
hfq_price = raw_price * adj_factor
```

- 默认展示 `qfq`，保持最新价与行情软件一致；
- 信号计算固定使用 `hfq`（收益序列与 qfq 等价，但价格连续）；
- 参考实现 `fetch_tushare_prices.py` 使用 `raw * factor / last_factor` 归一化到最新价，本项目保留该口径作为 `qfq`，并额外提供 `hfq`；
- `adj_factor` 缺失时前向/后向填充，并在响应 `warnings` 中提示。

### 5.5 数据标准化

沿用参考实现 `DataAPI.STANDARD_PRICE_COLUMNS` 的字段命名：

```
date, code, open, close, high, low, volume, amount,
amplitude, pct_change, change, turnover
```

存储层统一使用 `trade_date` + `ts_code`，服务层再转换成前端字段（camelCase）。

---

## 6. 数据模型与存储

### 6.1 主数据表（Parquet）

**bars_by_date**（按交易日分区）

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `trade_date` | string(8) | `YYYYMMDD` |
| `ts_code` | string | `600000.SH` |
| `open/high/low/close` | float | 原始价（元） |
| `pre_close` | float | 昨收 |
| `pct_chg` | float | 涨跌幅（%） |
| `change` | float | 涨跌额 |
| `vol` | float | 成交量（手） |
| `amount` | float | 成交额（千元） |

**daily_basic**：`trade_date, ts_code, turnover_rate, volume_ratio, pe_ttm, pb, total_mv, circ_mv`

**adj_factor**：`trade_date, ts_code, adj_factor`

**index_daily**：`trade_date, ts_code, close, pct_chg, amount`

**moneyflow**：`trade_date, ts_code, net_mf_amount, buy_lg_amount, sell_lg_amount`

**limit_list**：`trade_date, ts_code, limit_type, open_times`

**stock_basic**：`ts_code, symbol, name, industry, market, list_date, list_status`

### 6.2 元数据（SQLite）

```sql
CREATE TABLE dataset_manifest (
    dataset      TEXT NOT NULL,
    trade_date   TEXT NOT NULL,
    layer        TEXT NOT NULL,        -- raw / snapshot
    path         TEXT NOT NULL,
    rows         INTEGER NOT NULL,
    complete     INTEGER NOT NULL,
    reason       TEXT,
    origin       TEXT NOT NULL,        -- tushare / sample
    updated_at   TEXT NOT NULL,
    PRIMARY KEY (dataset, trade_date)
);

CREATE TABLE refresh_runs (
    id           TEXT PRIMARY KEY,
    dataset      TEXT NOT NULL,
    trade_date   TEXT,
    status       TEXT NOT NULL,        -- running / done / failed
    error        TEXT,
    started_at   TEXT NOT NULL,
    finished_at  TEXT
);

CREATE TABLE signal_snapshot (
    trade_date   TEXT NOT NULL,
    ts_code      TEXT NOT NULL,
    score        REAL NOT NULL,
    signal       TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    created_at   TEXT NOT NULL,
    PRIMARY KEY (trade_date, ts_code)
);
```

### 6.3 完整性校验

每个数据集定义检查器，参考 `dashboard/catalog.py`：

| 数据集 | 校验规则 |
| --- | --- |
| `daily` | 行数 ≥ 3000；`trade_date` 一致；`close > 0`；`high >= low` |
| `daily_basic` | 覆盖率 ≥ 95%；`total_mv` 非空率 ≥ 95% |
| `adj_factor` | 覆盖率 ≥ 99% |
| `index_daily` | 至少包含 6 个核心指数 |
| `moneyflow` | 覆盖率 ≥ 90%，否则标记降级 |
| `limit_list` | 允许为空，但需标记 `empty_ok` |

校验结果写入 `dataset_manifest.complete/reason`，并在 `/api/data/status` 暴露。

---

## 7. 缓存与离线降级

### 7.1 三级读取

```
内存 TTL 缓存（秒级）
      ↓ miss
本地 Parquet / 派生快照（离线可用）
      ↓ miss
TuShare 实时拉取（受模式与限流控制）
      ↓ 失败
返回最近快照 + stale=true + warnings
```

### 7.2 数据模式

环境变量 `WEB_QUANT_MODE`：

| 模式 | 行为 |
| --- | --- |
| `offline`（默认） | 只读本地快照，不访问 TuShare |
| `auto` | 本地完整则直接返回，后台异步刷新 |
| `live` | 优先实时拉取，失败再回退快照 |

### 7.3 快照策略

- **原始快照**：`data/raw/tushare/.../YYYYMMDD.parquet`，只增不改；
- **派生快照**：`data/snapshots/latest/{dataset}.json`，包含：
  - `market_overview`
  - `radar_top{ N }`
  - `signal_watchlist`
- **原子切换**：先写 `*.tmp`，校验通过后 `os.replace` 覆盖 `latest`；
- **过期判断**：交易日 15:10 后仍未更新到当日，标记 `stale=true`。

### 7.4 离线样本

`data/samples/` 提交一份小样本（例如 300 只股票 × 250 个交易日 + 6 个指数），用于：

- 首次启动即可打开三个页面；
- 单元测试与前端联调；
- 无 token 环境演示。

样本只用于教学，不作为投资依据，并在页脚展示数据声明。

---

## 8. 后端模块设计

### 8.1 `data/tushare_client.py`

```python
class TushareClient:
    def stock_basic(self, list_status: str = "L") -> pd.DataFrame: ...
    def trade_cal(self, start: str, end: str) -> pd.DataFrame: ...
    def daily(self, trade_date: str) -> pd.DataFrame: ...
    def daily_basic(self, trade_date: str) -> pd.DataFrame: ...
    def adj_factor(self, trade_date: str) -> pd.DataFrame: ...
    def index_daily(self, ts_code: str, start: str, end: str) -> pd.DataFrame: ...
    def moneyflow(self, trade_date: str) -> pd.DataFrame: ...
    def limit_list(self, trade_date: str) -> pd.DataFrame: ...
    def suspend(self, trade_date: str) -> pd.DataFrame: ...
```

职责：

- 统一 token、限流、重试、分页；
- 返回标准列名；
- 把 TuShare 错误码映射为 `TusharePermissionError` / `TushareRateLimitError` / `TushareEmptyError`；
- 不感知业务，不写 Parquet。

### 8.2 `data/repository.py`

- `read_bars(trade_date) -> DataFrame`
- `read_bars_by_symbol(ts_code, start, end) -> DataFrame`
- `write_dataset(dataset, trade_date, frame, origin)`（含校验 + manifest）
- `load_latest(dataset) -> DataFrame | None`
- `is_complete(dataset, trade_date) -> bool`
- `list_datasets() -> list[DatasetStatus]`
- 并发写使用文件锁；读 Parquet 使用 `pyarrow.parquet.read_table`。

### 8.3 `data/calendar.py`

- 启动时加载当年 + 次年交易日历到 SQLite；
- `latest_trade_date(now) -> str`：15:10 前返回上一交易日；
- `is_trade_day(date) -> bool`；
- `prev_trade_day(date, n=1)`；
- 日历缺失时按周一到周五兜底并给出警告。

### 8.4 `data/universe.py`

股票池过滤（雷达与信号共用）：

```python
EXCLUDE = [
    name 含 "ST" / "*ST" / "退",
    list_status != "L",
    list_date 距今天数 < 60 个交易日,
    停牌,
    20 日平均成交额 < 5000 万元,
    close < 2 元,
]
```

输出：

```python
UniverseFrame = [
    ts_code, name, industry, list_date,
    trade_date, close, pct_chg, amount,
    turnover_rate, volume_ratio, total_mv, circ_mv,
    net_mf_amount, is_limit_up, is_limit_down, is_suspended,
]
```

### 8.5 `domain/indicators.py`

纯函数，不依赖存储：

| 指标 | 参数 | 输出 |
| --- | --- | --- |
| SMA | 5/10/20/60 | 均线序列 |
| EMA | 12/26 | EMA 序列 |
| MACD | 12/26/9 | `macd, signal, hist` |
| RSI | 14 | 0-100 |
| BOLL | 20, 2σ | `upper, middle, lower, bandwidth` |
| ATR | 14 | 绝对值与百分比 |
| KDJ | 9,3,3 | `k, d, j` |
| 量能 | 5/20 | `vol_ma5, vol_ma20, vol_ratio` |

参考 `quant_system/data/indicator_cache.py` 的缓存键设计：`(indicator, params_hash, data_fingerprint)`，本项目先做内存缓存，后续可选落盘。

### 8.6 `services/market_service.py`

- `overview(trade_date)`：
  - 指数：上证指数、深证成指、创业板指、沪深300、中证500、中证1000；
  - 涨跌分布：上涨/下跌/平盘家数，涨停/跌停家数，涨跌幅中位数；
  - 成交额：全市场合计、较 5 日均值比例；
  - 行业：按 `stock_basic.industry` 等权聚合涨跌幅，取前后 10；
  - 榜单：涨幅榜、跌幅榜、成交额榜、换手率榜；
  - 市场状态：`risk_on / neutral / risk_off`。
- `tickers(trade_date, sort, limit)`：全市场横截面，供行情页表格。
- `breadth(trade_date)`：单独接口，便于前端局部刷新。

### 8.7 `services/radar_service.py`

1. 读取当日 `daily + daily_basic + moneyflow + limit_list + stock_basic`；
2. `universe` 过滤；
3. 对每个因子做横截面百分位排名；
4. 加权得到 0-100 分；
5. 风险扣分；
6. 取 Top K，生成理由与风险标记；
7. 对 Top K 计算轻量交易计划；
8. 结果写入 `snapshots/latest/radar_top{N}.json`。

### 8.8 `services/kline_service.py`

- 输入：`ts_code`、`period ∈ {daily, weekly, monthly}`、`adjust ∈ {qfq, hfq, raw}`、`limit`；
- 读取个股历史，按周期重采样；
- 计算指标；
- 返回 bars + indicators + meta（含 `adjust`、`as_of`、`warnings`）；
- 结果按 `(ts_code, period, adjust, last_date)` 做内存缓存。

### 8.9 `services/signal_service.py`

- 复用 `kline_service` 的日线/周线数据；
- 计算趋势评分、多周期共振、信号等级；
- 生成交易计划与仓位建议；
- 生成证据链（每个因子的贡献）；
- 批量接口对 `Watchlist` 或雷达 Top N 复用同一套逻辑。

### 8.10 `tasks/scheduler.py`

| 时间 | 任务 |
| --- | --- |
| 每个交易日 09:00 | 更新 `stock_basic`、`trade_cal` |
| 每个交易日 15:30 | 拉取 `daily`、`daily_basic`、`adj_factor`、`index_daily`、`moneyflow`、`limit_list` |
| 15:40 | 重建 `market_overview`、`radar_top50`、`signal_watchlist` |
| 每 5 分钟 | 检查任务补偿：若当日数据缺失则重试一次 |

实现要求：

- 单飞锁 + `refresh_runs` 记录；
- 任务失败不影响 API 读取旧快照；
- 提供 `POST /api/data/refresh` 手动触发。

---

## 9. API 设计

### 9.1 统一响应包

```json
{
  "ok": true,
  "source": "snapshot",
  "as_of": "2026-11-19T15:40:00+08:00",
  "stale": false,
  "warnings": [],
  "data": {}
}
```

错误响应：

```json
{
  "ok": false,
  "error": "DATA_NOT_READY",
  "message": "20261119 的日线数据尚未就绪",
  "warnings": ["last_available=20261118"]
}
```

错误码：

| 错误码 | 含义 |
| --- | --- |
| `DATA_NOT_READY` | 当日数据未刷新 |
| `SYMBOL_NOT_FOUND` | 股票代码不存在或已退市 |
| `INSUFFICIENT_HISTORY` | K 线不足以计算指标（< 60 根） |
| `TUSHARE_PERMISSION` | 积分/权限不足 |
| `TUSHARE_RATE_LIMITED` | 触发频率限制 |
| `INVALID_PARAM` | 参数非法 |

### 9.2 行情

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| GET | `/api/market/overview?date=` | 指数、涨跌分布、成交额、行业、榜单、市场状态 |
| GET | `/api/market/tickers?date=&sort=pct_chg&order=desc&limit=100` | 全市场横截面 |
| GET | `/api/market/breadth?date=` | 涨跌分布单独接口 |
| GET | `/api/market/sectors?date=&limit=10` | 行业强弱 |

### 9.3 机会雷达

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| GET | `/api/radar/opportunities?date=&topK=50&minAmount=50000000&excludeST=true` | 候选列表 |
| GET | `/api/radar/{ts_code}?date=` | 单只股票的评分分解与理由 |

`opportunities` 单项字段：

```json
{
  "rank": 1,
  "tsCode": "600000.SH",
  "name": "浦发银行",
  "industry": "银行",
  "close": 10.25,
  "pctChg": 3.21,
  "turnoverRate": 1.85,
  "volumeRatio": 1.62,
  "amount": 812345.6,
  "score": 86.4,
  "confidence": 78.0,
  "signal": "BUY",
  "signalLabel": "趋势与资金共振",
  "riskLevel": "medium",
  "bias": "bullish",
  "keyReasons": [
    "收盘价站上 MA20 与 MA60",
    "量比 1.62，成交额较 5 日均值放大",
    "主力净流入占成交额 4.8%"
  ],
  "riskFlags": ["接近 60 日高点"],
  "tradePlan": {
    "entryLow": 10.15,
    "entryHigh": 10.35,
    "stopLoss": 9.62,
    "target1": 11.05,
    "target2": 11.60,
    "rr1": 1.8,
    "rr2": 2.6,
    "positionPct": 12.0
  }
}
```

### 9.4 K 线与信号

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| GET | `/api/kline/{ts_code}?period=daily&adjust=qfq&limit=250` | K 线 + 指标 |
| GET | `/api/signal/{ts_code}?period=daily` | 交易信号 + 交易计划 + 证据链 |
| GET | `/api/signal/batch?codes=600000.SH,000001.SZ` | 批量信号 |

K 线响应：

```json
{
  "ok": true,
  "source": "snapshot",
  "as_of": "2026-11-19T15:40:00+08:00",
  "data": {
    "tsCode": "600000.SH",
    "name": "浦发银行",
    "period": "daily",
    "adjust": "qfq",
    "bars": [
      {"date": "2026-11-19", "open": 9.95, "high": 10.31, "low": 9.90,
       "close": 10.25, "volume": 123456, "amount": 812345.6, "pctChg": 3.21}
    ],
    "indicators": {
      "ma": {"ma5": [], "ma10": [], "ma20": [], "ma60": []},
      "boll": {"upper": [], "middle": [], "lower": []},
      "macd": {"diff": [], "dea": [], "hist": []},
      "rsi": [],
      "volMa": {"ma5": [], "ma20": []}
    }
  }
}
```

信号响应：

```json
{
  "ok": true,
  "data": {
    "tsCode": "600000.SH",
    "score": 68.5,
    "signal": "BUY",
    "signalLabel": "偏多",
    "confidence": 74.0,
    "direction": "bullish",
    "marketState": "trending",
    "tradePlan": {
      "entryLow": 10.15,
      "entryHigh": 10.35,
      "stopLoss": 9.62,
      "target1": 11.05,
      "target2": 11.60,
      "rr1": 1.8,
      "rr2": 2.6,
      "positionPct": 12.0,
      "executable": true,
      "executionNote": "信号于收盘后生成，最早下一交易日执行"
    },
    "scoreBreakdown": [
      {"factor": "trend", "weight": 0.30, "raw": 0.85, "contribution": 25.5, "reason": "均线多头排列"}
    ],
    "evidence": {
      "technical": {"bias": "bullish", "score": 72.0},
      "capital": {"bias": "bullish", "score": 65.0},
      "sentiment": {"bias": "neutral", "score": 50.0}
    },
    "riskFlags": ["接近 60 日高点"],
    "warnings": []
  }
}
```

### 9.5 数据运维

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| GET | `/api/data/status` | 各数据集完整性、最新日期、是否过期 |
| POST | `/api/data/refresh` | 手动刷新（body 可指定 date/dataset） |
| GET | `/api/config` | 数据模式、数据源可用性、股票池规模 |

---

## 10. 核心算法设计

### 10.1 行情总览

**指数**：固定 6 个核心指数，返回 `close`、`pct_chg`、`amount`。

**涨跌分布**：

```python
up   = (pct_chg > 0).sum()
down = (pct_chg < 0).sum()
flat = (pct_chg == 0).sum()
limit_up   = (pct_chg >= limit_threshold - 0.1).sum()
limit_down = (pct_chg <= -limit_threshold + 0.1).sum()
median_pct = pct_chg.median()
```

涨跌停阈值按板块区分：

| 板块 | 代码前缀 | 阈值 |
| --- | --- | --- |
| 主板 | `60/00` | 10% |
| 创业板 | `30` | 20% |
| 科创板 | `688` | 20% |
| 北交所 | `8/4` | 30% |
| ST | 名称含 ST | 5% |

**行业强弱**：按 `industry` 分组，计算成分股 `pct_chg` 中位数与成交额加权涨幅，取前 10 / 后 10。

**市场状态**：

```
breadth_score = (up - down) / total
amount_score  = amount / amount_ma5 - 1
index_score   = mean(pct_chg of 6 indices) / 2
regime_score  = 0.5 * breadth_score + 0.3 * index_score + 0.2 * amount_score

regime_score >  0.15 -> risk_on
regime_score < -0.15 -> risk_off
else                 -> neutral
```

### 10.2 机会雷达评分

**单因子横截面百分位**（0-100）：

| 因子 | 计算 | 权重 |
| --- | --- | --- |
| `momentum_1d` | `pct_chg` | 0.18 |
| `momentum_5d` | 5 日累计涨幅 | 0.14 |
| `volume_ratio` | `vol / vol_ma5` | 0.14 |
| `turnover` | `turnover_rate` | 0.10 |
| `trend` | 均线排列 + MACD 状态 | 0.18 |
| `capital_flow` | `net_mf_amount / amount` | 0.14 |
| `relative_strength` | 20 日超额收益 vs 沪深300 | 0.12 |

**趋势因子细化**：

```
ma_score:
  close > ma5 > ma10 > ma20 > ma60  -> 100
  close > ma20 > ma60               -> 70
  close > ma20                      -> 55
  close < ma20 < ma60               -> 10
  else                              -> 40

macd_score:
  hist > 0 and hist > hist[-1]      -> 100
  hist > 0 and hist <= hist[-1]     -> 65
  hist < 0 and hist > hist[-1]      -> 40
  else                              -> 10

trend = 0.6 * ma_score + 0.4 * macd_score
```

**风险扣分**：

| 风险 | 扣分 |
| --- | --- |
| ST / *ST / 退市整理 | 直接剔除 |
| 停牌 | 直接剔除 |
| 上市不足 60 个交易日 | 直接剔除 |
| 20 日平均成交额 < 5000 万 | 直接剔除 |
| 当日跌停 | -30 |
| 连续涨停 ≥ 3 天 | -20 |
| 收盘价处于 20 日区间 > 98% | -10 |
| ATR% > 8% | -10 |

**最终得分**：

```
score = clamp(sum(weight_i * factor_i) - risk_penalty, 0, 100)
```

**置信度**：

```
confidence = 40 + 0.4 * abs(score - 50) + data_quality_bonus
data_quality_bonus = 10 if 必需数据集完整 else 0
confidence = clamp(confidence, 25, 95)
```

**信号映射**：

| 分数 | 信号 | 标签 |
| --- | --- | --- |
| ≥ 80 | `STRONG_BUY` | 多维共振 |
| 65-80 | `BUY` | 偏多 |
| 50-65 | `WATCH` | 观察 |
| 35-50 | `NEUTRAL` | 中性 |
| < 35 | `AVOID` | 回避 |

### 10.3 K 线周期

- 日线：直接使用 `daily`；
- 周线：按自然周聚合，`open` 取首日、`close` 取末日、`high` 取最高、`low` 取最低、`volume/amount` 求和；
- 月线：按自然月聚合，规则同上；
- 先复权再聚合，避免跨除权周出现跳空；
- 指标在对应周期上独立计算，不跨周期混用。

### 10.4 交易信号

**趋势评分（日线）**：

```
score = 100 * (
    0.30 * ma_score +
    0.20 * macd_score +
    0.15 * rsi_score +
    0.15 * boll_score +
    0.10 * volume_score +
    0.10 * relative_strength_score
)
```

各分项归一化到 `[-1, 1]`：

| 分项 | 规则 |
| --- | --- |
| `ma_score` | 多头排列 +1；站上 MA20/60 +0.6；跌破 MA20/60 -1；其余 0 |
| `macd_score` | 红柱放大 +1；红柱缩短 +0.3；绿柱放大 -1；绿柱缩短 -0.3 |
| `rsi_score` | 50-70 +0.6；40-50 +0.2；70-80 +0.1；>80 -0.6；30-40 -0.2；<30 -0.8 |
| `boll_score` | 触及上轨 +0.5；中轨上方 +0.2；中轨下方 -0.2；触及下轨 -0.5 |
| `volume_score` | 放量上涨 +1；缩量上涨 +0.3；放量下跌 -1；缩量下跌 -0.3 |
| `rs_score` | 20 日超额收益的横截面百分位映射到 [-1, 1] |

**多周期共振**：

```
total = 0.70 * daily_score + 0.30 * weekly_score
```

**信号等级**：

| 分数 | 信号 | 说明 |
| --- | --- | --- |
| ≥ 60 | `STRONG_BUY` | 日周共振，趋势与量能同步 |
| 30-60 | `BUY` | 偏多，可观察回踩 |
| 10-30 | `WATCH` | 偏多但证据不足 |
| -10-10 | `HOLD` | 中性 |
| -30--10 | `REDUCE` | 偏空 |
| ≤ -30 | `SELL` | 空头趋势 |

**交易计划**：

| 字段 | 规则 |
| --- | --- |
| 入场区间 | 突破型：`[max(close, high20), high20 * 1.01]`；回踩型：`[ma20 * 0.99, ma20 * 1.01]` |
| 止损 | `min(entry_low - 1.5 * atr, ma20 * 0.97, support20 * 0.99)` |
| 目标 1 | `min(resistance20, entry + 2 * atr)`，若无阻力则 `entry + 2 * atr` |
| 目标 2 | `entry + 3 * atr` |
| 盈亏比 | `(target1 - entry) / (entry - stop)` |
| 仓位 | `risk_budget = capital * 1%`；`shares = floor(risk_budget / (entry - stop) / 100) * 100`；单票上限 20% |
| 可执行性 | 当日涨停、停牌、`rr < 1.5` 时标记不可执行或降级为 `WATCH` |

**A 股约束必须写进信号输出**：

- T+1：当日买入最早下一交易日卖出；
- 涨停不可买、跌停不可卖；
- 100 股整手；
- 费用：佣金（默认万 2.5，最低 5 元）、印花税（卖出 0.05%）、过户费（0.001%）；
- 信号基于收盘数据生成，最早下一交易日开盘执行，禁止把当日收盘价当作成交价。

**证据链**：每个分项返回 `factor / weight / raw / contribution / reason`，前端展示评分分解，避免“黑盒分数”。

---

## 11. 前端设计

### 11.1 路由与布局

```
/            -> 重定向 /market
/market      -> 行情总览
/radar       -> 机会雷达
/kline       -> K 线与交易信号
```

`MainLayout`：

- 左侧导航：行情、机会雷达、K 线与信号；
- 顶部状态条：数据模式、最新交易日、`stale` 标记、刷新按钮；
- 内容区最大宽度 1600px，最小适配 1280px。

### 11.2 行情页 `/market`

| 区域 | 内容 |
| --- | --- |
| 指数卡片 | 6 个核心指数，涨跌颜色、成交额 |
| 市场宽度 | 上涨/下跌/平盘、涨停/跌停、涨跌中位数 |
| 成交额 | 全市场成交额、较 5 日均值 |
| 行业热力 | 前后 10 行业横向条形图或热力图 |
| 榜单 | 涨幅榜、跌幅榜、成交额榜、换手率榜（Tabs） |
| 股票表格 | 代码、名称、行业、最新价、涨跌幅、成交额、换手率、量比 |

交互：

- 点击股票行跳转 `/kline?code=...`；
- 表格支持排序、分页、搜索；
- 刷新按钮触发 `POST /api/data/refresh`（仅 auto/live 模式）。

### 11.3 机会雷达页 `/radar`

| 区域 | 内容 |
| --- | --- |
| 筛选器 | 日期、TopK、最小成交额、是否排除 ST、行业 |
| 榜单表格 | 排名、代码、名称、得分、置信度、信号、涨跌幅、成交额、风险等级 |
| 评分分解 | 点击行打开抽屉，展示 7 个因子的贡献条形图 |
| 理由 | `keyReasons` 列表 + `riskFlags` |
| 交易计划 | 入场区间、止损、目标位、盈亏比 |

### 11.4 K 线与信号页 `/kline`

| 区域 | 内容 |
| --- | --- |
| 搜索栏 | 代码/名称搜索，周期切换（日/周/月），复权切换（qfq/hfq/raw） |
| 主图 | K 线 + MA5/10/20/60 + BOLL 上下轨 |
| 副图 1 | 成交量 + 量均线 |
| 副图 2 | MACD（DIF/DEA/柱） |
| 副图 3 | RSI(14) |
| 信号卡 | 信号等级、分数、置信度、方向、市场状态 |
| 交易计划卡 | 入场、止损、目标 1/2、盈亏比、仓位、可执行性 |
| 证据链 | 技术/资金/情绪三组评分与理由 |
| 数据状态 | 数据来源、`as_of`、`stale`、`warnings` |

图表要求：

- ECharts `candlestick` + `line` + `bar` 多 grid 联动；
- `dataZoom` 支持缩放与区间选择；
- `markLine` 绘制入场、止损、目标位；
- 涨跌配色：红涨绿跌（A 股习惯），可在设置中切换；
- 空态提示“数据不足 60 根 K 线”。

### 11.5 状态与错误

- 使用 TanStack Query：
  - `staleTime`：行情 60 秒，雷达 5 分钟，K 线 10 分钟；
  - `retry: 1`；
  - 错误统一展示 `error` + `message` + 建议操作。
- 顶部 `StatusBadge` 明确显示 `offline / auto / live` 和 `stale`。
- 所有页面底部展示“仅供研究，不构成投资建议”。

---

## 12. 配置设计

### 12.1 `.env.example`

```ini
TS_TOKEN=
WEB_QUANT_MODE=offline
TS_CALLS_PER_MIN=120
TS_MAX_RETRIES=5
WEB_QUANT_DB=data/meta/web_quant.db
WEB_QUANT_DATA_DIR=data
API_HOST=127.0.0.1
API_PORT=8000
LOG_LEVEL=INFO
```

### 12.2 `config/radar.yaml`

```yaml
universe:
  exclude_st: true
  exclude_suspended: true
  min_list_days: 60
  min_avg_amount_20d: 50000000
  min_price: 2.0

weights:
  momentum_1d: 0.18
  momentum_5d: 0.14
  volume_ratio: 0.14
  turnover: 0.10
  trend: 0.18
  capital_flow: 0.14
  relative_strength: 0.12

risk_penalty:
  limit_down: 30
  consecutive_limit_up_3: 20
  range_pos_gt_98: 10
  atr_pct_gt_8: 10

output:
  top_k: 50
  min_confidence: 45
```

### 12.3 `config/signal.yaml`

```yaml
indicators:
  ma: [5, 10, 20, 60]
  macd: [12, 26, 9]
  rsi: 14
  boll: [20, 2.0]
  atr: 14
  kdj: [9, 3, 3]

weights:
  ma: 0.30
  macd: 0.20
  rsi: 0.15
  boll: 0.15
  volume: 0.10
  relative_strength: 0.10

timeframes:
  daily: 0.70
  weekly: 0.30

thresholds:
  strong_buy: 60
  buy: 30
  watch: 10
  reduce: -10
  sell: -30

trade_plan:
  atr_stop_multiple: 1.5
  target1_atr: 2.0
  target2_atr: 3.0
  min_rr: 1.5
  risk_per_trade: 0.01
  max_position: 0.20

fees:
  commission_rate: 0.00025
  commission_min: 5.0
  stamp_duty_sell: 0.0005
  transfer_fee: 0.00001
```

---

## 13. 刷新与数据治理

### 13.1 刷新流程

```
1. 读取交易日历，确定 target_date
2. 检查 manifest，跳过已完整数据集
3. 依次拉取 daily / daily_basic / adj_factor / index_daily
4. 可选拉取 moneyflow / limit_list / suspend
5. 标准化列名、类型、日期
6. 完整性校验，写 Parquet + manifest
7. 重建派生快照：market_overview / radar_top50 / signal_watchlist
8. 原子替换 latest
9. 记录 refresh_runs
```

### 13.2 数据质量规则

| 检查 | 处理 |
| --- | --- |
| 全市场行数 < 3000 | 标记不完整，保留旧快照 |
| `trade_date` 不一致 | 丢弃该批数据 |
| `close <= 0` | 剔除并记录 |
| `high < low` | 剔除并记录 |
| `adj_factor` 覆盖率 < 99% | 前向填充 + 警告 |
| 指数缺失 | 行情页局部降级，雷达继续 |
| 15:10 后仍无当日数据 | `/api/data/status` 标记 `stale` |

### 13.3 防前视偏差

- 雷达与信号只使用 `trade_date` 当日收盘及之前的数据；
- 所有信号明确标注“收盘后生成，下一交易日执行”；
- 不使用未来函数：均线、MACD、RSI 全部使用 `shift` 后的历史窗口；
- 回测（后续扩展）采用“信号日收盘生成、次日开盘成交”的口径。

---

## 14. 测试策略

| 层级 | 内容 |
| --- | --- |
| 单元测试 | 指标计算（与已知序列对比）、复权公式、周期聚合、评分函数 |
| 契约测试 | 每个 API 的响应字段、类型、错误码 |
| 集成测试 | 使用 `data/samples` 离线样本，不访问网络 |
| 金样本测试 | 固定一只股票、固定日期，断言信号等级与关键字段 |
| 数据质量测试 | 校验器对构造的脏数据返回预期结果 |

要求：

- 测试默认 `WEB_QUANT_MODE=offline`；
- 网络调用全部 mock，避免 CI 依赖 TuShare；
- 指标测试覆盖边界：空序列、不足窗口、全涨、全跌、停牌缺口。

---

## 15. 性能与容量

| 项目 | 估算 |
| --- | --- |
| 全市场单日行数 | 约 5000-5500 |
| 10 年日线总量 | 约 1300 万行 |
| Parquet 压缩后 | 约 1-2 GB |
| 雷达单日横截面计算 | 目标 < 300 ms |
| 单股 250 根 K 线 + 指标 | 目标 < 50 ms |
| API P95 | 行情 < 500 ms，雷达 < 800 ms，K 线 < 300 ms |

优化手段：

- 雷达结果预计算并落快照，接口只读；
- K 线按 `(ts_code, period, adjust, last_date)` 缓存；
- 指标使用向量化 pandas，避免逐行循环；
- 前端表格虚拟滚动，图表按需加载。

---

## 16. 安全与合规

- 只读研究服务，不接券商、不产生委托；
- `TS_TOKEN` 仅存在于服务端 `.env`，日志中脱敏；
- 不向前端返回原始 TuShare 响应；
- 页面显著位置展示“数据仅供研究，不构成投资建议”；
- 离线样本如来自 TuShare，需遵守其数据使用条款，不公开再分发；
- 所有交易计划均为研究参考，明确标注不可直接执行。

---

## 17. 实施阶段

### P0：骨架与数据底座

- FastAPI 骨架、配置、日志、错误码；
- `TushareClient`、`repository`、`calendar`、`universe`；
- `stock_basic` / `trade_cal` / `daily` / `adj_factor` 拉取与校验；
- `/api/market/overview`、`/api/market/tickers`；
- 行情页最小版本。

**验收**：离线样本可打开行情页；配置 token 后能刷新当日数据。

### P1：机会雷达

- `daily_basic` / `moneyflow` / `limit_list` 接入；
- `scoring.py` 与 `radar_service`；
- `/api/radar/opportunities`、`/api/radar/{ts_code}`；
- 雷达页、评分分解抽屉。

**验收**：给定交易日能返回 Top 50，评分与理由可解释。

### P2：K 线与交易信号

- `indicators.py`、`kline_service`、`signal_service`、`trade_plan`；
- `/api/kline/{ts_code}`、`/api/signal/{ts_code}`、批量接口；
- K 线页、信号卡、交易计划卡、证据链。

**验收**：单股日/周/月 K 线正确；信号与交易计划字段完整；A 股约束有提示。

### P3：调度与打磨

- APScheduler 盘后刷新；
- `data/status`、`data/refresh`；
- 离线样本生成脚本；
- 测试补齐、性能优化、README。

**验收**：交易日 15:30 自动刷新；断网可完整演示；测试全部通过。

---

## 18. 风险与备选方案

| 风险 | 影响 | 应对 |
| --- | --- | --- |
| TuShare 积分不足 | 资金流/涨跌停不可用 | 启动探测，缺失因子降级为 0 分并警告；保留 akshare 备选接口 |
| 频率限制 | 刷新慢或失败 | 令牌桶 + 指数退避 + 按日批量拉取 |
| 复权因子缺失 | 历史价格跳变 | 前后向填充；覆盖率低时标记 stale |
| 停牌/退市 | 雷达误选 | 股票池过滤 + 可交易性标记 |
| 新股上市不足 | 指标失真 | 上市 < 60 交易日剔除 |
| 涨跌停无法成交 | 信号不可执行 | `executable=false` + 次日观察提示 |
| 数据陈旧 | 研究结论失真 | `as_of`、`stale`、`warnings` 全链路透出 |
| 全市场扫描慢 | 页面卡顿 | 盘后预计算 + 快照读取 |

---

## 19. 附录

### 19.1 核心指数

| 代码 | 名称 |
| --- | --- |
| `000001.SH` | 上证指数 |
| `399001.SZ` | 深证成指 |
| `399006.SZ` | 创业板指 |
| `000300.SH` | 沪深 300 |
| `000905.SH` | 中证 500 |
| `000852.SH` | 中证 1000 |

### 19.2 字段命名约定

| 层 | 风格 | 示例 |
| --- | --- | --- |
| TuShare / Parquet | snake_case | `trade_date`, `pct_chg` |
| 服务内部 | snake_case | `momentum_1d` |
| API 响应 | camelCase | `tsCode`, `pctChg` |
| 前端类型 | camelCase | `tsCode`, `tradePlan` |

### 19.3 参考实现复用清单

| 参考文件 | 复用的设计 |
| --- | --- |
| `quant_system/data/data_api.py` | 标准 12 列价格 schema、按 source/adjust 隔离缓存、复权模式 |
| `quant_system/data/fetch_tushare_prices.py` | 分页、重试、频率限制处理、`adj_factor` 复权 |
| `quant_system/data/data_get_stocklist.py` | `stock_basic` 股票池获取与 A 股代码过滤 |
| `quant_system/data/indicator_cache.py` | 指标缓存键与参数哈希设计 |
| `web3-quant-sandbox/src/dashboard` | 数据模式、降级链、manifest、完整性校验、信号分层 |

### 19.4 页面验收清单

- [ ] 行情页展示 6 个指数、涨跌分布、成交额、行业前后 10、四类榜单
- [ ] 雷达页支持筛选、排序、TopK，点击行展示评分分解与理由
- [ ] K 线页支持日/周/月、qfq/hfq/raw、MA/BOLL/成交量/MACD/RSI
- [ ] 信号卡展示等级、分数、置信度、方向
- [ ] 交易计划展示入场、止损、目标、盈亏比、仓位、可执行性
- [ ] 所有页面展示数据来源、`as_of`、`stale`、`warnings`
- [ ] 断网时三个页面均可从离线样本打开
- [ ] 页面底部展示研究用途声明
