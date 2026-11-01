"""Unit 50 · 自定义 Tool 的 MCP 服务端（stdio）。

四个工具，故意分成三类来写：
  1. fx_rate      —— 纯函数：无 I/O、无状态，同样的参数永远同样的结果
  2. unit_convert —— 纯函数 + 大量错误分支：非法单位要能自己兜住
  3. csv_stats    —— 有状态：结果取决于磁盘上 data/sales.csv 的内容
  4. append_note  —— 有副作用：调用会改变世界（多一行日志），重试不安全

启动（一般不手动起，main.py 会以子进程方式拉起）：
    /home/rik/.cache/aj/runner/bin/python server.py
"""

from __future__ import annotations

import csv
import os
from datetime import datetime
from pathlib import Path

from mcp.server.mcpserver import MCPServer

BASE_DIR = Path(__file__).parent
DATA_DIR = BASE_DIR / "data"
SALES_CSV = DATA_DIR / "sales.csv"
NOTES_LOG = DATA_DIR / "notes.log"

server = MCPServer(
    "unit50-tools",
    version="0.1.0",
    instructions="提供汇率换算、单位换算、CSV 列统计三类工具；全部离线。",
)

# ---------------------------------------------------------------------------
# 纯函数：汇率
# ---------------------------------------------------------------------------
# 以 USD 为基准的固定汇率表。真实系统这里会接行情源，本单元故意离线——
# 工具的教学价值在 schema 和错误分支，不在于能不能联网。
RATES_TO_USD = {
    "USD": 1.0,
    "CNY": 7.12,
    "EUR": 0.92,
    "JPY": 156.40,
}

CURRENCY_ALIASES = {"RMB": "CNY", "YUAN": "CNY", "US$": "USD", "€": "EUR", "¥": "JPY"}


def normalize_currency(code: str) -> str:
    """把用户可能写歪的币种名归一化。归一化失败返回空串。"""
    c = code.strip().upper()
    c = CURRENCY_ALIASES.get(c, c)
    return c if c in RATES_TO_USD else ""


def fx_convert(amount: float, src: str, dst: str) -> float:
    """纯函数：金额换算。调用方负责保证币种合法。"""
    return amount / RATES_TO_USD[src] * RATES_TO_USD[dst]


# ---------------------------------------------------------------------------
# 纯函数：单位换算
# ---------------------------------------------------------------------------
# 每组单位都换算到同一个基准单位，跨组直接拒绝（km 换 kg 是没有意义的）。
LENGTH_TO_M = {"mm": 0.001, "cm": 0.01, "m": 1.0, "km": 1000.0, "in": 0.0254, "ft": 0.3048, "mi": 1609.344}
WEIGHT_TO_KG = {"g": 0.001, "kg": 1.0, "t": 1000.0, "oz": 0.0283495, "lb": 0.4535924}
TEMPERATURE_UNITS = {"c", "f", "k"}


def unit_group(unit: str) -> str:
    u = unit.strip().lower()
    if u in LENGTH_TO_M:
        return "length"
    if u in WEIGHT_TO_KG:
        return "weight"
    if u in TEMPERATURE_UNITS:
        return "temperature"
    return ""


def all_units() -> list[str]:
    return sorted(list(LENGTH_TO_M) + list(WEIGHT_TO_KG) + sorted(TEMPERATURE_UNITS))


def convert_value(value: float, src: str, dst: str) -> float:
    """纯函数：单位换算。调用前必须已经确认同组、合法。"""
    s, d = src.strip().lower(), dst.strip().lower()
    if s in TEMPERATURE_UNITS and d in TEMPERATURE_UNITS:
        celsius = {"c": value, "f": (value - 32) / 1.8, "k": value - 273.15}[s]
        return {"c": celsius, "f": celsius * 1.8 + 32, "k": celsius + 273.15}[d]
    if s in LENGTH_TO_M:
        return value * LENGTH_TO_M[s] / LENGTH_TO_M[d]
    return value * WEIGHT_TO_KG[s] / WEIGHT_TO_KG[d]


# ---------------------------------------------------------------------------
# 有状态：CSV 统计
# ---------------------------------------------------------------------------
SALES_HEADER = ["order_id", "date", "region", "product", "qty", "unit_price"]
SALES_ROWS = [
    [1001, "2026-09-01", "华东", "笔记本", 2, 6499.00],
    [1002, "2026-09-01", "华南", "显示器", 3, 1299.00],
    [1003, "2026-09-02", "华北", "键盘", 5, 399.00],
    [1004, "2026-09-02", "华东", "鼠标", 8, 129.00],
    [1005, "2026-09-03", "华南", "笔记本", 1, 7299.00],
    [1006, "2026-09-03", "西南", "显示器", 2, 1599.00],
    [1007, "2026-09-04", "华北", "键盘", 4, 459.00],
    [1008, "2026-09-04", "华东", "耳机", 6, 699.00],
    [1009, "2026-09-05", "华南", "鼠标", 12, 99.00],
    [1010, "2026-09-05", "西南", "笔记本", 3, 5999.00],
    [1011, "2026-09-06", "华北", "显示器", 1, 2199.00],
    [1012, "2026-09-06", "华东", "键盘", 10, 329.00],
    [1013, "2026-09-07", "华南", "耳机", 4, 899.00],
    [1014, "2026-09-07", "西南", "鼠标", 15, 149.00],
    [1015, "2026-09-08", "华北", "笔记本", 2, 8499.00],
    [1016, "2026-09-08", "华东", "显示器", 4, 1099.00],
    [1017, "2026-09-09", "华南", "键盘", 3, 529.00],
    [1018, "2026-09-09", "西南", "耳机", 2, 1299.00],
    [1019, "2026-09-10", "华北", "鼠标", 9, 179.00],
    [1020, "2026-09-10", "华东", "笔记本", 1, 6499.00],
]

NUMERIC_COLUMNS = {"order_id", "qty", "unit_price"}
STAT_OPS = ("summary", "sum", "mean", "min", "max", "rows")


def ensure_sales_csv() -> Path:
    """幂等：文件不在就写一份；在就不动（不要覆盖用户改过的数据）。"""
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    if not SALES_CSV.exists():
        with SALES_CSV.open("w", newline="", encoding="utf-8") as f:
            w = csv.writer(f)
            w.writerow(SALES_HEADER)
            w.writerows(SALES_ROWS)
    return SALES_CSV


def read_column(column: str) -> list[float]:
    """读一列数值。非数值列由调用方提前拒绝。"""
    ensure_sales_csv()
    with SALES_CSV.open(newline="", encoding="utf-8") as f:
        return [float(row[column]) for row in csv.DictReader(f)]


def fmt_num(x: float) -> str:
    """97.0 -> '97'，4.85 -> '4.85'。统计结果要给人看，不要 '97.00000000001'。"""
    return str(int(x)) if float(x).is_integer() else f"{round(x, 4):g}"


def compute_stats(values: list[float]) -> dict:
    return {
        "rows": len(values),
        "sum": sum(values),
        "mean": sum(values) / len(values) if values else 0.0,
        "min": min(values) if values else 0.0,
        "max": max(values) if values else 0.0,
    }


# ---------------------------------------------------------------------------
# 有副作用：追加一行笔记
# ---------------------------------------------------------------------------
def append_note_line(note: str) -> int:
    """追加一行到 data/notes.log，返回总行数。这个函数不幂等。"""
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    with NOTES_LOG.open("a", encoding="utf-8") as f:
        f.write(f"{stamp}\t{note}\n")
    return len(NOTES_LOG.read_text(encoding="utf-8").splitlines())


def count_notes() -> int:
    if not NOTES_LOG.exists():
        return 0
    return len(NOTES_LOG.read_text(encoding="utf-8").splitlines())


# ---------------------------------------------------------------------------
# 工具定义
# ---------------------------------------------------------------------------
@server.tool(
    name="fx_rate",
    description=(
        "把一笔金额从一种货币换算成另一种货币，使用内置的固定汇率表（离线，不联网）。"
        "参数：amount 是正数金额；from_currency 与 to_currency 是三位货币代码，"
        "支持 USD / CNY / EUR / JPY，也接受 RMB、¥ 这类别名。"
        "返回一行文本：换算结果（保留 2 位小数）和本次使用的汇率。"
        "不支持的货币不要调用本工具，先问用户要哪种货币。"
    ),
)
def fx_rate(amount: float, from_currency: str, to_currency: str) -> str:
    src = normalize_currency(from_currency)
    dst = normalize_currency(to_currency)
    if not src or not dst:
        bad = from_currency if not src else to_currency
        return f"错误：不认识货币 {bad!r}，当前只支持 {'/'.join(RATES_TO_USD)}（也可写 RMB）。没做任何换算。"
    if amount <= 0:
        return f"错误：amount 必须大于 0，收到 {amount}。"
    value = fx_convert(amount, src, dst)
    rate = fx_convert(1.0, src, dst)
    return f"{fmt_num(amount)} {src} = {value:.2f} {dst}（1 {src} = {rate:.4f} {dst}）"


@server.tool(
    name="unit_convert",
    description=(
        "换算带单位的数值，支持三组互不相通的单位："
        "长度 mm/cm/m/km/in/ft/mi，重量 g/kg/t/oz/lb，温度 c/f/k。"
        "参数：value 是数值；from_unit 与 to_unit 是不带数字的单位符号（例如 'km'、'lb'、'c'）。"
        "返回一行文本：换算结果和所用公式。"
        "不同组之间（例如 km 换 kg）会返回中文错误说明，不会瞎算。"
    ),
)
def unit_convert(value: float, from_unit: str, to_unit: str) -> str:
    gs, gd = unit_group(from_unit), unit_group(to_unit)
    if not gs or not gd:
        bad = from_unit if not gs else to_unit
        return f"错误：不认识单位 {bad!r}，可用单位：{', '.join(all_units())}。没做任何换算。"
    if gs != gd:
        return f"错误：{from_unit} 属于 {gs}，{to_unit} 属于 {gd}，两组单位不能互换。"
    result = convert_value(value, from_unit, to_unit)
    return f"{fmt_num(value)} {from_unit.lower()} = {fmt_num(round(result, 6))} {to_unit.lower()}（{gs}）"


@server.tool(
    name="csv_stats",
    description=(
        "对内置销售表 data/sales.csv（20 行）的某一列做统计。"
        "参数：column 只能是数值列 order_id / qty / unit_price；"
        "op 取 summary（默认，一次给全）/ sum / mean / min / max / rows。"
        "返回一行文本，形如「列 qty：rows=20 sum=97 mean=4.85 min=1 max=15」。"
        "问文本列（region、product）的统计请改用其他方式，本工具会返回错误。"
    ),
)
def csv_stats(column: str, op: str = "summary") -> str:
    col = column.strip()
    if col not in NUMERIC_COLUMNS:
        return f"错误：列 {col!r} 不是数值列，可选：{', '.join(sorted(NUMERIC_COLUMNS))}。"
    op = (op or "summary").strip().lower()
    if op not in STAT_OPS:
        return f"错误：op {op!r} 不支持，可选：{', '.join(STAT_OPS)}。"
    stats = compute_stats(read_column(col))
    if op == "rows":
        return f"列 {col}：rows={stats['rows']}"
    if op == "summary":
        return (
            f"列 {col}：rows={stats['rows']} sum={fmt_num(stats['sum'])} "
            f"mean={fmt_num(stats['mean'])} min={fmt_num(stats['min'])} max={fmt_num(stats['max'])}"
        )
    return f"列 {col} {op}={fmt_num(stats[op])}"


@server.tool(
    name="append_note",
    description=(
        "把一条笔记追加到 data/notes.log（有副作用，重复调用会写多行）。"
        "参数：note 是要记录的一句话，不超过 200 字。"
        "返回：已写入的行号和当前总行数。仅在用户明确要求「记下来」时调用。"
    ),
)
def append_note(note: str) -> str:
    text = note.strip()
    if not text:
        return "错误：note 是空的，没有写入任何内容。"
    if len(text) > 200:
        return f"错误：note 长 {len(text)} 字，超过 200 字上限，没有写入。"
    total = append_note_line(text)
    return f"已写入第 {total} 条笔记（data/notes.log），当前共 {total} 行。"


def main() -> None:
    ensure_sales_csv()
    server.run("stdio")


if __name__ == "__main__":
    main()
