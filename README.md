# 分钟因子回测台

本地分钟数据回测网页。输入因子表达式和 `Decay`，在浏览器里直接运行回测并查看结果。

## 仓库使用建议

- 代码适合放到 GitHub。
- 大数据文件不适合直接提交到 GitHub，尤其是 `股票分钟数据/`、`股票分钟数据.zip`、`DailyData20240102open.bin`。
- 建议仓库只保存代码和说明，分钟数据放在本地目录，再通过环境变量指定。

## 快速开始

1. 创建虚拟环境。

Windows PowerShell:

```powershell
python -m venv .venv
.venv\Scripts\Activate.ps1
```

Linux / macOS:

```bash
python3 -m venv .venv
source .venv/bin/activate
```

2. 安装依赖：

```bash
python -m pip install -r requirements.txt
```

3. 准备数据。

默认情况下，程序会读取项目根目录下的：

- `股票分钟数据/`
- `股票分钟数据说明.xlsx`

如果分钟数据不在项目根目录，可以设置 `MINUTE_DATA_DIR`。

Windows PowerShell:

```powershell
$env:MINUTE_DATA_DIR="D:\your\minute\data\folder"
python app.py
```

Linux / macOS:

```bash
export MINUTE_DATA_DIR="/path/to/your/minute/data/folder"
python app.py
```

如果说明文件 `股票分钟数据说明.xlsx` 也不在仓库根目录，请把它一起放到项目根目录。

4. 启动服务：

```bash
python app.py
```

5. 打开浏览器：

```text
http://127.0.0.1:5000
```

## 可选环境变量

- `MINUTE_DATA_DIR`: 分钟数据目录。
- `APP_HOST`: 服务监听地址，默认 `127.0.0.1`。
- `APP_PORT`: 服务端口，默认 `5000`。

示例：

```bash
APP_HOST=0.0.0.0 APP_PORT=5000 python app.py
```

Linux / macOS 如果上面这种单行写法不方便，也可以：

```bash
export APP_HOST=0.0.0.0
export APP_PORT=5000
python app.py
```

## 目录结构

- `app.py`: Flask 入口和 API。
- `engine/`: 数据读取、表达式解析、回测计算、字段目录。
- `templates/`、`static/`: 网页模板、样式和前端逻辑。

## 说明

- 当前版本只使用分钟 `.mat` 数据，不读取 `DailyData20240102open.bin`。
- 字段、派生字段、函数说明，以及完整回测规则，都可以直接在网页帮助区查看。
- 长时间区间首次运行会比较慢，因为后端需要逐日读取分钟数据文件。
- 如果缺少本地数据，网页会给出明确的初始化错误提示，而不是直接崩掉。
