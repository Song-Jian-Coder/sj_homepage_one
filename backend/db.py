import pymysql
from pymysql.cursors import DictCursor


def connect(cfg):
    return pymysql.connect(
        host=cfg.db_host, port=cfg.db_port, user=cfg.db_user,
        password=cfg.db_password, charset="utf8mb4", autocommit=False,
    )


def execute(conn, sql, args=None):
    with conn.cursor() as cur:
        cur.execute(sql, args)
        return cur.rowcount


def executemany(conn, sql, rows):
    with conn.cursor() as cur:
        cur.executemany(sql, rows)
        return cur.rowcount


def query(conn, sql, args=None):
    with conn.cursor() as cur:
        cur.execute(sql, args)
        return cur.fetchall()


def query_one(conn, sql, args=None):
    with conn.cursor() as cur:
        cur.execute(sql, args)
        return cur.fetchone()


def query_dict(conn, sql, args=None):
    """返回 list[dict]，键为列名（只读查询用）"""
    with conn.cursor(DictCursor) as cur:
        cur.execute(sql, args)
        return cur.fetchall()
