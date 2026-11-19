# 服务启动脚本命令

## 0. 前置准备

在项目根目录（`web-quant/`）执行：

```powershell
# 安装后端依赖
pip install -r requirements.txt

# 安装前端依赖
cd frontend
npm install
cd ..

# 复制环境变量模板（离线模式可直接启动，无需 token）
copy .env.example .env
```

## 1. 启动后端（开发模式）

后端代码位于 `backend/`，包名为 `app`，需在该目录下启动：

```powershell
cd backend
python -m uvicorn app.main:app --reload --host 127.0.0.1 --port 8000
```

或在项目根目录用 `--app-dir` 免切换目录：

```powershell
python -m uvicorn app.main:app --app-dir backend --reload --host 127.0.0.1 --port 8000
```

启动后：

- API 文档：http://127.0.0.1:8000/docs
- 健康检查：http://127.0.0.1:8000/api/health

## 2. 启动前端（开发模式）

```powershell
cd frontend
npm run dev
```

访问 http://127.0.0.1:5173 ，`/api` 已由 Vite 代理到 `http://127.0.0.1:8000`。

## 3. 生产模式（单端口）

先构建前端，再启动后端；后端会自动托管 `frontend/dist`：

```powershell
cd frontend
npm run build
cd ..

cd backend
python -m uvicorn app.main:app --host 127.0.0.1 --port 8000
```

访问 http://127.0.0.1:8000 即可。

## 4. 数据初始化 / 刷新（可选）

需在 `.env` 中配置 `TS_TOKEN`，并将 `WEB_QUANT_MODE` 设为 `auto` 或 `live`。

```powershell
# 初始化单个交易日
python scripts/bootstrap_data.py --date 20261118

# 批量刷新区间
python scripts/bootstrap_data.py --start 20260101 --end 20261118

# 生成离线演示样本
python scripts/build_offline_samples.py
```

## 5. 运行测试

```powershell
pytest
```

默认使用 `WEB_QUANT_MODE=offline`，不访问网络。
