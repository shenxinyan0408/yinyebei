# 分钟因子回测网页

本地回测网页。可以同时管理多个因子窗口，分别设置表达式、开始日期、结束日期和 `Decay`，并查看回测结果、`IC`、因子相关性等信息。

## 数据说明

仓库本身 **不包含** 大体积本地数据。其他人从 GitHub `pull` 下来后，需要自行准备：

- 分钟 `.mat` 数据目录
- `DailyData20240102open.bin`
- `股票分钟数据说明.xlsx`

第一次启动时，如果项目找不到本地数据，网页会自动显示“数据路径设置”面板。  
用户只需要在网页里填写自己的分钟数据目录和标签文件路径，保存后即可使用。

本机数据路径配置会写入：

```text
runtime/data_sources.json
```

分钟数据还会自动生成本机缓存：

```text
runtime/minute_cache/
```

首次读取某天 `.mat` 文件时，会顺手生成这一天的缓存；之后优先读取缓存，所以同一台机器上的后续回测通常会明显更快。

网页里有“分钟缓存预热”按钮，可以先一次性把缓存建好。  
如果你想直接在命令行里做这件事，也可以运行：

```bash
python prewarm_minute_cache.py
```

可选参数：

```bash
python prewarm_minute_cache.py --start 2020-01-02 --end 2020-12-31
python prewarm_minute_cache.py --force
```

这些本机配置和缓存目录都只保存在本地，已经加入 `.gitignore`，不会提交到 GitHub。

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

## 启动

```bash
python app.py
```

浏览器打开：

```text
http://127.0.0.1:5000
```

## 可选环境变量

如果你更希望在启动时直接指定路径，也可以使用环境变量。环境变量优先级高于网页里保存的本机配置。

Windows PowerShell:

```powershell
$env:MINUTE_DATA_DIR="D:\your\minute\data\folder"
$env:DAILY_DATA_FILE="D:\your\DailyData20240102open.bin"
$env:MINUTE_CACHE_DIR="D:\your\minute\cache"
python app.py
```

Linux / macOS:

```bash
export MINUTE_DATA_DIR="/path/to/your/minute/data/folder"
export DAILY_DATA_FILE="/path/to/your/DailyData20240102open.bin"
export MINUTE_CACHE_DIR="/path/to/your/minute/cache"
python app.py
```

服务地址和端口也可以调整：

```bash
export APP_HOST=0.0.0.0
export APP_PORT=5000
python app.py
```

上面这种 `export` 方式只对当前终端会话生效，不是永久修改。

## 其他说明

- 后端同时运行的最大回测任务数由 `APP_MAX_WORKERS` 控制，默认是 `5`。
- 字段、派生字段、函数、回测规则和标签口径都放在网页帮助区查看。
- 首次长区间运行仍然会比较慢，因为需要逐日读取原始 `.mat` 并生成缓存。
- 同一台机器上后续重复回测，通常会比第一次明显更快。
