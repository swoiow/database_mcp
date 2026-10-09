"""Core unit tests: L2 endpoint table scope (EndpointMeta)."""
import sys
from pathlib import Path


sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from gateway_config import EndpointMeta


def _ep(*tables):
    return EndpointMeta(alias="e", connection_id="c", allowed_tables=list(tables))


def test_empty_whitelist_means_all():
    ep = _ep()
    assert not ep.table_restricted
    assert ep.is_table_allowed("anything", "anydb")
    assert ep.filter_tables(["a", "b"], "db") == ["a", "b"]


def test_plain_table_matches_any_scope():
    ep = _ep("orders")
    assert ep.is_table_allowed("orders", "shop")
    assert ep.is_table_allowed("ORDERS", "shop")  # case-insensitive
    assert not ep.is_table_allowed("users", "shop")


def test_qualified_entry():
    ep = _ep("shop.orders")
    assert ep.is_table_allowed("orders", "shop")
    assert ep.is_table_allowed("orders", None)  # unqualified ref matches
    assert not ep.is_table_allowed("orders", "other")


def test_filter_tables():
    ep = _ep("orders", "shop.items")
    assert ep.filter_tables(["orders", "users", "items"], "shop") == ["orders", "items"]
