"""shop.db —— 一个不大但足够真实的电商库。

四张表：customers / products / orders / order_items。
数据全部写死在本文件里，`reset_db()` 每次从头重建 —— 自检因此永远确定。

两条连接工厂是本单元的教学道具：
- `ro_connect()` 用 `file:...?mode=ro` 打开，**SQLite 自己**拒绝一切写操作；
- `rw_connect()` 是普通连接，只有建库和种子数据用它。
第 57 章会把这条「只读连接」当成四道安全闸里的第一道，反复打它。
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = BASE_DIR / "data"
DB_PATH = DATA_DIR / "shop.db"

# 账期锚点写死，不用 datetime.now() —— 否则「上月」在哪个月跑就变，自检会飘。
AS_OF = "2026-11-01"
LAST_MONTH = "2026-10"

DDL = """
CREATE TABLE customers (
    id          INTEGER PRIMARY KEY,
    name        TEXT    NOT NULL,
    city        TEXT    NOT NULL,
    region      TEXT    NOT NULL,
    signup_date TEXT    NOT NULL
);

CREATE TABLE products (
    id       INTEGER PRIMARY KEY,
    name     TEXT    NOT NULL,
    category TEXT    NOT NULL,
    price    INTEGER NOT NULL
);

CREATE TABLE orders (
    id          INTEGER PRIMARY KEY,
    customer_id INTEGER NOT NULL REFERENCES customers(id),
    order_date  TEXT    NOT NULL,
    status      TEXT    NOT NULL,
    region      TEXT    NOT NULL
);

CREATE TABLE order_items (
    id         INTEGER PRIMARY KEY,
    order_id   INTEGER NOT NULL REFERENCES orders(id),
    product_id INTEGER NOT NULL REFERENCES products(id),
    qty        INTEGER NOT NULL,
    unit_price INTEGER NOT NULL
);

CREATE INDEX idx_orders_date ON orders(order_date);
CREATE INDEX idx_items_order ON order_items(order_id);
"""

CUSTOMERS = [
    (1, "张伟", "上海", "华东", "2024-03-11"),
    (2, "李娜", "杭州", "华东", "2024-05-02"),
    (3, "王强", "南京", "华东", "2023-11-20"),
    (4, "陈静", "苏州", "华东", "2025-01-09"),
    (5, "刘洋", "北京", "华北", "2023-08-15"),
    (6, "赵敏", "天津", "华北", "2024-09-30"),
    (7, "孙磊", "青岛", "华北", "2025-02-14"),
    (8, "周涛", "广州", "华南", "2023-12-01"),
    (9, "吴倩", "深圳", "华南", "2024-07-19"),
    (10, "郑凯", "厦门", "华南", "2025-04-06"),
    (11, "冯霞", "成都", "西部", "2024-01-22"),
    (12, "何军", "西安", "西部", "2023-06-08"),
]

PRODUCTS = [
    (1, "机械键盘 KB-01", "外设", 399),
    (2, "无线鼠标 MS-02", "外设", 129),
    (3, "4K 显示器 MN-27", "显示", 1899),
    (4, "笔记本支架 ST-01", "外设", 89),
    (5, "USB-C 扩展坞 DK-08", "外设", 459),
    (6, "蓝牙耳机 HP-05", "音频", 699),
    (7, "便携音箱 SP-03", "音频", 349),
    (8, "桌面台灯 LP-02", "家居", 159),
    (9, "人体工学椅 CH-09", "家居", 2399),
    (10, "升降桌 DK-12", "家居", 1599),
    (11, "固态硬盘 SSD-1T", "存储", 649),
    (12, "机械硬盘 HDD-4T", "存储", 899),
    (13, "路由器 RT-AX", "网络", 529),
    (14, "摄像头 CM-4K", "外设", 799),
    (15, "麦克风 MC-USB", "音频", 469),
]

ORDERS = [
    (1, 1, "2026-10-02", "已完成", "华东"),
    (2, 2, "2026-10-03", "已完成", "华东"),
    (3, 3, "2026-10-05", "已完成", "华东"),
    (4, 4, "2026-10-06", "已完成", "华东"),
    (5, 5, "2026-10-07", "已完成", "华北"),
    (6, 6, "2026-10-09", "已取消", "华北"),
    (7, 8, "2026-10-10", "已完成", "华南"),
    (8, 9, "2026-10-12", "已完成", "华南"),
    (9, 11, "2026-10-14", "已完成", "西部"),
    (10, 1, "2026-10-17", "已完成", "华东"),
    (11, 2, "2026-10-18", "已完成", "华东"),
    (12, 3, "2026-10-21", "已完成", "华东"),
    (13, 5, "2026-10-24", "已完成", "华北"),
    (14, 8, "2026-10-27", "已取消", "华南"),
    (15, 12, "2026-10-29", "已完成", "西部"),
    (16, 1, "2026-09-03", "已完成", "华东"),
    (17, 2, "2026-09-05", "已完成", "华东"),
    (18, 3, "2026-09-08", "已完成", "华东"),
    (19, 4, "2026-09-11", "已退款", "华东"),
    (20, 5, "2026-09-12", "已完成", "华北"),
    (21, 6, "2026-09-15", "已完成", "华北"),
    (22, 7, "2026-09-17", "已完成", "华北"),
    (23, 8, "2026-09-19", "已完成", "华南"),
    (24, 9, "2026-09-21", "已完成", "华南"),
    (25, 10, "2026-09-23", "已完成", "华南"),
    (26, 11, "2026-09-25", "已完成", "西部"),
    (27, 12, "2026-09-26", "已完成", "西部"),
    (28, 4, "2026-09-28", "已完成", "华东"),
    (29, 9, "2026-09-29", "已完成", "华南"),
    (30, 7, "2026-09-30", "已取消", "华北"),
]

ORDER_ITEMS = [
    (1, 1, 3, 1, 1899), (2, 1, 1, 2, 399),
    (3, 2, 9, 1, 2399), (4, 2, 4, 3, 89),
    (5, 3, 3, 2, 1899), (6, 3, 11, 1, 649),
    (7, 4, 6, 2, 699), (8, 4, 8, 1, 159),
    (9, 5, 2, 3, 129), (10, 5, 13, 1, 529),
    (11, 6, 7, 1, 349), (12, 6, 15, 1, 469),
    (13, 7, 9, 1, 2399), (14, 7, 3, 1, 1899),
    (15, 8, 11, 2, 649), (16, 8, 14, 1, 799),
    (17, 9, 10, 1, 1599), (18, 9, 12, 1, 899),
    (19, 10, 3, 1, 1899), (20, 10, 5, 2, 459),
    (21, 11, 9, 1, 2399), (22, 11, 10, 1, 1599),
    (23, 12, 1, 4, 399), (24, 12, 6, 1, 699),
    (25, 13, 1, 3, 399), (26, 13, 2, 2, 129),
    (27, 14, 6, 1, 699), (28, 14, 15, 2, 469),
    (29, 15, 3, 1, 1899), (30, 15, 8, 2, 159),
    (31, 16, 3, 1, 1899), (32, 16, 9, 1, 2399),
    (33, 17, 1, 2, 399), (34, 17, 6, 1, 699),
    (35, 18, 10, 1, 1599), (36, 18, 5, 1, 459),
    (37, 19, 2, 1, 129), (38, 19, 8, 1, 159),
    (39, 20, 11, 1, 649), (40, 20, 13, 1, 529),
    (41, 21, 14, 1, 799), (42, 21, 7, 1, 349),
    (43, 22, 9, 1, 2399), (44, 22, 12, 1, 899),
    (45, 23, 3, 2, 1899), (46, 23, 2, 4, 129),
    (47, 24, 6, 1, 699), (48, 24, 15, 1, 469),
    (49, 25, 11, 1, 649), (50, 25, 4, 2, 89),
    (51, 26, 10, 1, 1599), (52, 26, 8, 1, 159),
    (53, 27, 13, 1, 529), (54, 27, 5, 1, 459),
    (55, 28, 3, 1, 1899), (56, 28, 1, 1, 399),
    (57, 29, 9, 1, 2399), (58, 29, 6, 2, 699),
    (59, 30, 14, 1, 799), (60, 30, 2, 2, 129),
]


def reset_db() -> None:
    """删库重建。自检第一步永远先调它，杜绝上次跑残留的状态。"""
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    for suffix in ("", "-wal", "-shm"):
        p = Path(str(DB_PATH) + suffix)
        if p.exists():
            p.unlink()
    conn = sqlite3.connect(DB_PATH)
    try:
        conn.executescript(DDL)
        conn.executemany("INSERT INTO customers VALUES (?,?,?,?,?)", CUSTOMERS)
        conn.executemany("INSERT INTO products VALUES (?,?,?,?)", PRODUCTS)
        conn.executemany("INSERT INTO orders VALUES (?,?,?,?,?)", ORDERS)
        conn.executemany("INSERT INTO order_items VALUES (?,?,?,?,?)", ORDER_ITEMS)
        conn.commit()
    finally:
        conn.close()


def ensure_db() -> None:
    if not DB_PATH.exists():
        reset_db()


def ro_connect() -> sqlite3.Connection:
    """只读连接。mode=ro 由 SQLite 自己执行，绕过任何 Python 层检查都会被它挡下。"""
    ensure_db()
    conn = sqlite3.connect(f"file:{DB_PATH}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    return conn


def rw_connect() -> sqlite3.Connection:
    ensure_db()
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def table_names(conn: sqlite3.Connection) -> list[str]:
    rows = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name"
    ).fetchall()
    return [r[0] for r in rows]


def describe(conn: sqlite3.Connection, table: str) -> list[dict]:
    rows = conn.execute(f"PRAGMA table_info({table})").fetchall()
    return [
        {"name": r["name"], "type": r["type"], "notnull": bool(r["notnull"]), "pk": bool(r["pk"])}
        for r in rows
    ]


def row_counts(conn: sqlite3.Connection) -> dict[str, int]:
    return {t: conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0] for t in table_names(conn)}
