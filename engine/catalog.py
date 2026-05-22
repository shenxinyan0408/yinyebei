from __future__ import annotations

import functools
import zipfile
from pathlib import Path
from typing import Any
import xml.etree.ElementTree as ET


BASE_DIR = Path(__file__).resolve().parent.parent
RAW_FIELD_DOC_PATH = BASE_DIR / "股票分钟数据说明.xlsx"
MINUTE_DATA_DIR = BASE_DIR / "股票分钟数据"
DAILY_DATA_FILE = BASE_DIR / "DailyData20240102open.bin"
DEFAULT_EXPRESSION = "rank(last(MINUTE_CLOSE) / first(MINUTE_OPEN) - 1)"
PRICE_FIELDS = {
    "MINUTE_OPEN",
    "MINUTE_HIGH",
    "MINUTE_LOW",
    "MINUTE_CLOSE",
}
VOLUME_LIKE_FIELDS = {
    "MINUTE_VOLUME",
    "MINUTE_AMOUNT",
    "MINUTE_NUMBER",
}
RAW_EXPRESSION_FIELDS = PRICE_FIELDS | VOLUME_LIKE_FIELDS
NUMERIC_FIELD_DEPENDENCIES = {
    "VWAP": {"MINUTE_AMOUNT", "MINUTE_VOLUME"},
}
EXAMPLE_EXPRESSIONS = [
    {
        "label": "开收反转",
        "expression": "rank(last(MINUTE_CLOSE) / first(MINUTE_OPEN) - 1)",
    },
    {
        "label": "VWAP 偏离",
        "expression": "zscore(ts_mean(VWAP, 30) / last(MINUTE_CLOSE) - 1)",
    },
    {
        "label": "成交额成交量对比",
        "expression": "rank(ts_rank(MINUTE_AMOUNT, 30) - ts_rank(MINUTE_VOLUME, 30))",
    },
]
FIXED_RULES = [
    "前10%",
    "多头",
    "等权",
    "T+1 09:25",
    "日频调仓",
]
BACKTEST_RULES = [
    {
        "title": "信号时间线",
        "items": [
            "用 T 日分钟数据计算因子分数。",
            "T 日收完信号后生成目标持仓。",
            "在 T+1 日 09:25 的开盘价成交。",
        ],
    },
    {
        "title": "组合构建",
        "items": [
            "仅做多。",
            "每个调仓日对可交易股票按分数排序。",
            "买入前10%的股票并等权分配。",
            "每个交易日调仓一次。",
        ],
    },
    {
        "title": "成交价格",
        "items": [
            "入场价使用 T+1 日 09:25 的 MINUTE_OPEN。",
            "离场价使用下一次调仓日 09:25 的 MINUTE_OPEN。",
            "收益按开盘到开盘的实现收益计算。",
        ],
    },
    {
        "title": "可交易筛选",
        "items": [
            "只有 T 日分数有效的股票才会进入排序。",
            "只有 T+1 日成交价有效的股票才允许交易。",
            "如果股票在入场日无法成交，该次调仓会把它从目标组合中剔除。",
        ],
    },
    {
        "title": "部分分钟缺失",
        "items": [
            "价格字段只做同日内轻填充。",
            "首个有效分钟前的缺失值，用当日第一个有效价格回填。",
            "之后的价格缺失，用最近一个有效分钟价格前向填充。",
            "MINUTE_VOLUME、MINUTE_AMOUNT、MINUTE_NUMBER 的局部缺失补 0。",
        ],
    },
    {
        "title": "整天全空",
        "items": [
            "如果某只股票在某天的必需字段整天都是 NaN，该股票当天不能用于计算信号。",
            "不会跨交易日进行填充。",
        ],
    },
    {
        "title": "09:35 有效性规则",
        "items": [
            "09:35 截止规则只用于真正发生交易的 T+1 日。",
            "更早的信号计算日不会因为首个有效价晚于 09:35 就剔除股票。",
            "如果 T+1 日 MINUTE_OPEN 的首个有效分钟晚于 09:35，该股票在这次调仓中不可交易。",
        ],
    },
    {
        "title": "衰减",
        "items": [
            "Decay = 1 表示不做平滑。",
            "Decay = N 时，使用最近 N 个信号日的线性加权平均分数。",
            "越近的日期权重越高。",
            "如果某只股票在衰减窗口中的任意一天不可用，该股票在这次调仓中会被剔除。",
        ],
    },
    {
        "title": "范围限制",
        "items": [
            "当前版本只使用本地分钟数据目录中的 .mat 文件。",
            "不考虑手续费，不考虑滑点，不做空，不做中性化，不做日内多次调仓。",
        ],
    },
]
DERIVED_FIELDS = [
    {
        "name": "VWAP",
        "definition": "MINUTE_AMOUNT / MINUTE_VOLUME",
        "returns": "分钟矩阵",
        "description": "分钟成交均价矩阵。分母为 0 时返回 NaN。",
        "example": "rank(last(VWAP) / last(MINUTE_CLOSE) - 1)",
    }
]
FUNCTIONS = [
    {
        "name": "first",
        "signature": "first(field)",
        "returns": "股票向量",
        "description": "返回当日第一个分钟点的值。",
        "example": "first(MINUTE_OPEN)",
    },
    {
        "name": "last",
        "signature": "last(field)",
        "returns": "股票向量",
        "description": "返回当日最后一个分钟点的值。",
        "example": "last(MINUTE_CLOSE)",
    },
    {
        "name": "at",
        "signature": 'at("09:30", field)',
        "returns": "股票向量",
        "description": "按分钟标签或整数索引取值。",
        "example": 'at("09:30", MINUTE_CLOSE)',
    },
    {
        "name": "delta",
        "signature": "delta(field, periods=1)",
        "returns": "分钟矩阵",
        "description": "沿分钟轴做差分，前几个位置补 NaN。",
        "example": "last(delta(MINUTE_CLOSE, 1))",
    },
    {
        "name": "ts_mean",
        "signature": "ts_mean(field, window)",
        "returns": "股票向量",
        "description": "取最后 window 个分钟的均值。",
        "example": "ts_mean(MINUTE_VOLUME, 30)",
    },
    {
        "name": "ts_std",
        "signature": "ts_std(field, window)",
        "returns": "股票向量",
        "description": "取最后 window 个分钟的标准差。",
        "example": "ts_std(MINUTE_CLOSE, 20)",
    },
    {
        "name": "ts_sum",
        "signature": "ts_sum(field, window)",
        "returns": "股票向量",
        "description": "取最后 window 个分钟的求和。",
        "example": "ts_sum(MINUTE_AMOUNT, 30)",
    },
    {
        "name": "ts_min",
        "signature": "ts_min(field, window)",
        "returns": "股票向量",
        "description": "取最后 window 个分钟的最小值。",
        "example": "ts_min(MINUTE_LOW, 15)",
    },
    {
        "name": "ts_max",
        "signature": "ts_max(field, window)",
        "returns": "股票向量",
        "description": "取最后 window 个分钟的最大值。",
        "example": "ts_max(MINUTE_HIGH, 15)",
    },
    {
        "name": "ts_rank",
        "signature": "ts_rank(field, window)",
        "returns": "股票向量",
        "description": "返回最后一个值在最近 window 个分钟中的分位排名。",
        "example": "ts_rank(MINUTE_CLOSE, 20)",
    },
    {
        "name": "rank",
        "signature": "rank(vector)",
        "returns": "与输入同维度",
        "description": "横截面分位排名。向量按股票维度排名，矩阵按每分钟横截面排名。",
        "example": "rank(last(MINUTE_CLOSE) / first(MINUTE_OPEN) - 1)",
    },
    {
        "name": "zscore",
        "signature": "zscore(vector)",
        "returns": "与输入同维度",
        "description": "横截面标准化。标准差为 0 时返回 0。",
        "example": "zscore(ts_mean(VWAP, 30))",
    },
    {
        "name": "scale",
        "signature": "scale(vector, factor=1.0)",
        "returns": "与输入同维度",
        "description": "按横截面绝对值和归一，再乘 factor。",
        "example": "scale(zscore(last(MINUTE_CLOSE)), 1)",
    },
    {
        "name": "winsorize",
        "signature": "winsorize(vector, limit=3.0)",
        "returns": "与输入同维度",
        "description": "按横截面均值 ± limit * std 截尾。",
        "example": "winsorize(zscore(last(MINUTE_CLOSE)), 3)",
    },
    {
        "name": "abs",
        "signature": "abs(x)",
        "returns": "与输入同维度",
        "description": "绝对值。",
        "example": "abs(last(MINUTE_CLOSE) - first(MINUTE_OPEN))",
    },
    {
        "name": "log",
        "signature": "log(x)",
        "returns": "与输入同维度",
        "description": "自然对数，非正值返回 NaN。",
        "example": "log(ts_sum(MINUTE_AMOUNT, 30))",
    },
    {
        "name": "sqrt",
        "signature": "sqrt(x)",
        "returns": "与输入同维度",
        "description": "平方根，负值返回 NaN。",
        "example": "sqrt(ts_mean(MINUTE_VOLUME, 30))",
    },
    {
        "name": "sign",
        "signature": "sign(x)",
        "returns": "与输入同维度",
        "description": "返回符号。",
        "example": "sign(last(MINUTE_CLOSE) - first(MINUTE_OPEN))",
    },
    {
        "name": "where",
        "signature": "where(condition, x, y)",
        "returns": "与 x / y 广播后同维度",
        "description": "条件选择。",
        "example": "where(last(MINUTE_CLOSE) > first(MINUTE_OPEN), 1, -1)",
    },
]
FUNCTION_NAMES = {item["name"] for item in FUNCTIONS}


def resolve_field_dependencies(field_name: str) -> set[str]:
    if field_name in RAW_EXPRESSION_FIELDS:
        return {field_name}
    return set(NUMERIC_FIELD_DEPENDENCIES.get(field_name, set()))


def _cell_text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, (list, tuple)):
        if not value:
            return ""
        return _cell_text(value[0])
    return str(value)


@functools.lru_cache(maxsize=1)
def load_raw_field_catalog() -> list[dict[str, str]]:
    ns = {
        "a": "http://schemas.openxmlformats.org/spreadsheetml/2006/main",
        "r": "http://schemas.openxmlformats.org/officeDocument/2006/relationships",
    }
    rows: list[dict[str, str]] = []

    with zipfile.ZipFile(RAW_FIELD_DOC_PATH) as workbook_zip:
        workbook = ET.fromstring(workbook_zip.read("xl/workbook.xml"))
        sheets = workbook.find("a:sheets", ns)
        if sheets is None or not list(sheets):
            return rows

        first_sheet = list(sheets)[0]
        relationship_id = first_sheet.attrib.get(
            "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}id"
        )
        relationships = ET.fromstring(workbook_zip.read("xl/_rels/workbook.xml.rels"))
        relationship_map = {
            rel.attrib["Id"]: rel.attrib["Target"] for rel in relationships
        }
        target = "xl/" + relationship_map[relationship_id]
        worksheet = ET.fromstring(workbook_zip.read(target))

        shared_strings: list[str] = []
        if "xl/sharedStrings.xml" in workbook_zip.namelist():
            shared_root = ET.fromstring(workbook_zip.read("xl/sharedStrings.xml"))
            for item in shared_root:
                fragments = [
                    node.text or ""
                    for node in item.iter(
                        "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}t"
                    )
                ]
                shared_strings.append("".join(fragments))

        def cell_value(cell: ET.Element) -> str:
            cell_type = cell.attrib.get("t")
            value_node = cell.find("a:v", ns)
            if value_node is None:
                return ""
            raw = value_node.text or ""
            if cell_type == "s":
                return shared_strings[int(raw)]
            return raw

        sheet_data = worksheet.find("a:sheetData", ns)
        if sheet_data is None:
            return rows

        for index, row in enumerate(sheet_data):
            if index == 0:
                continue
            values = [cell_value(cell) for cell in row]
            if not values or len(values) < 5:
                continue
            rows.append(
                {
                    "category": "raw",
                    "label": values[0],
                    "name": values[1],
                    "displayName": values[1],
                    "description": values[2],
                    "dimensions": values[3],
                    "notes": values[4],
                    "expressionReady": str(values[1] in RAW_EXPRESSION_FIELDS).lower(),
                }
            )

    return rows


def build_backtest_rules() -> list[dict[str, Any]]:
    rules = [
        {
            "title": section["title"],
            "items": list(section["items"]),
        }
        for section in BACKTEST_RULES
    ]
    if rules:
        rules[-1] = {
            "title": "范围限制",
            "items": [
                "分钟信号与开盘成交价格来自本地分钟数据目录中的 .mat 文件。",
                "IC 标签来自 DailyData20240102open.bin 中的 Label 字段。",
                "不考虑手续费，不考虑滑点，不做空，不做中性化，不做日内多次调仓。",
            ],
        }
    rules.append(
        {
            "title": "标签与 IC",
            "items": [
                "Label 表示未来 5 日收益，并且已经剥离风险因子后的剩余收益。",
                "某个信号日的标签从下一交易日上午开始计算，例如 2009-01-05 这一行对应 2009-01-06 上午到 2009-01-13 上午。",
                "IC 按信号日横截面计算，使用当日衰减后的因子分数与同一日的 Label 做相关系数。",
            ],
        }
    )
    return rules


def build_catalog_payload() -> dict[str, Any]:
    return {
        "backtestRules": build_backtest_rules(),
        "rawFields": load_raw_field_catalog(),
        "derivedFields": DERIVED_FIELDS,
        "functions": FUNCTIONS,
    }
