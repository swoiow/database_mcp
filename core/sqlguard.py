"""Read-only SQL guard (AST-based).

Replaces the old first-keyword check, which was bypassable, e.g.:

  - WITH x AS (DELETE FROM t RETURNING *) SELECT * FROM x   (PostgreSQL data-modifying CTE)
  - SELECT * INTO new_table FROM t                          (PostgreSQL: creates a table)
  - SELECT a INTO OUTFILE '/tmp/x' FROM t                   (MySQL: writes server files)
  - SELECT 1; DROP TABLE users                              (stacked queries)

Validation is done on the parsed AST via sqlglot (fail-closed: unparsable
SQL is rejected). Only a single, pure SELECT statement is allowed.
"""
from __future__ import annotations

import sqlglot
from sqlglot import exp


# AST node types that imply a write of any kind.
_FORBIDDEN = (exp.Insert, exp.Update, exp.Delete, exp.Into, exp.Copy)


def ensure_readonly_select(sql: str, dialect: str) -> None:
    """Raise ValueError unless `sql` is a single pure-SELECT statement.

    `dialect`: "mysql" or "postgres" (sqlglot dialect names).
    """
    s = (sql or "").strip()
    if not s:
        raise ValueError("Empty SQL. SQL 为空。")

    try:
        trees = sqlglot.parse(s, read=dialect)
    except Exception as e:
        raise ValueError(f"SQL parse failed, rejected for safety. SQL 解析失败，已拒绝: {e}")

    if len(trees) != 1 or trees[0] is None:
        raise ValueError(
            "Only a single SELECT statement is allowed (no stacked queries). "
            "只允许单条 SELECT 语句（禁止堆叠查询）。"
        )

    tree = trees[0]
    if not isinstance(tree, exp.Select):
        raise ValueError(
            f"Only SELECT statements are allowed (got {type(tree).__name__}). "
            "只允许 SELECT 语句。"
        )

    bad = tree.find(*_FORBIDDEN)
    if bad is not None:
        raise ValueError(
            f"Write operation detected ({type(bad).__name__}); this gateway is read-only. "
            "检测到写操作；本网关只读。"
        )


def extract_tables(sql: str, dialect: str) -> list[tuple[str | None, str]]:
    """Extract (scope, table) references from SQL via AST.

    CTE names and derived-table aliases are excluded (they are not real
    tables). Used for L2 table-scope validation -- replaces the old
    FROM|JOIN regex, which mis-parsed quoted identifiers and string
    literals.
    """
    try:
        trees = sqlglot.parse(sql, read=dialect)
    except Exception:
        return []
    out: list[tuple[str | None, str]] = []
    for tree in trees:
        if tree is None:
            continue
        cte_names = {c.alias.lower() for c in tree.find_all(exp.CTE) if c.alias}
        for t in tree.find_all(exp.Table):
            name = (t.name or "").lower()
            if not name or name in cte_names:
                continue
            scope = (t.db or "").lower() or None
            out.append((scope, name))
    # dedupe, keep order
    seen: set[tuple[str | None, str]] = set()
    res: list[tuple[str | None, str]] = []
    for item in out:
        if item not in seen:
            seen.add(item)
            res.append(item)
    return res
