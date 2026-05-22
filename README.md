# 分钟因子回测网页

本地回测网页。可以同时管理多个因子窗口，在浏览器里输入表达式、单独设置每个因子的开始日期、结束日期和 `Decay`，然后查看回测结果。

另外还提供一个独立的“因子相关性”计算区，可以选择两个已有因子窗口，按各自当前的表达式与设置，在重叠日期区间里计算每日横截面相关性。

## 准备数据

默认会读取项目根目录下的：

- `股票分钟数据/`
- `股票分钟数据说明.xlsx`
- `DailyData20240102open.bin`

其中：

- 分钟 `.mat` 数据用于信号计算和开盘成交价回测。
- `DailyData20240102open.bin` 中的 `Label` 用于计算因子与标签的 `IC` / `ICIR`。

大数据文件不建议直接提交到 GitHub。更适合把仓库只作为代码仓库，数据单独放在本地磁盘，再通过环境变量指定路径。

## 安装

Windows PowerShell:

```powershell
python -m venv .venv
.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
```

Linux / macOS:

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
```

## 运行

默认启动：

```bash
python app.py
```

浏览器打开：

```text
http://127.0.0.1:5000
```

如果数据不在项目根目录，可以设置环境变量。

Windows PowerShell:

```powershell
$env:MINUTE_DATA_DIR="D:\your\minute\data\folder"
$env:DAILY_DATA_FILE="D:\your\daily\DailyData20240102open.bin"
python app.py
```

Linux / macOS:

```bash
export MINUTE_DATA_DIR="/path/to/your/minute/data/folder"
export DAILY_DATA_FILE="/path/to/your/daily/DailyData20240102open.bin"
python app.py
```

如果还想改监听地址或端口：

```bash
export APP_HOST=0.0.0.0
export APP_PORT=5000
python app.py
```

上面这种 `export` 方式只对当前终端会话生效，不是永久修改。

## 环境变量

- `MINUTE_DATA_DIR`: 分钟数据目录。
- `DAILY_DATA_FILE`: 日频标签文件路径。
- `APP_HOST`: 服务监听地址，默认 `127.0.0.1`。
- `APP_PORT`: 服务端口，默认 `5000`。
- `APP_MAX_WORKERS`: 后端同时执行的最大回测任务数，默认 `5`。

## 项目结构

- `app.py`: Flask 入口和 API。
- `engine/`: 数据读取、表达式解析、回测计算、字段目录。
- `templates/`、`static/`: 页面模板、样式和前端逻辑。

## 说明

- 因子数量本身不受限制，但后端并行回测数量受 `APP_MAX_WORKERS` 限制。
- 网页里不提供并发数量修改入口；如果需要调整最大同时回测数，请只在服务端通过 `APP_MAX_WORKERS` 修改。
- 字段、派生字段、函数说明，以及完整回测规则，都可以直接在网页帮助区查看。
- 长区间首次运行会比较慢，因为后端需要逐日读取分钟数据文件。
- 如果缺少本地数据，网页会显示明确的初始化错误提示。
