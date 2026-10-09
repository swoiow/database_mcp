"""Core unit tests: JSON persistence + legacy pickle migration (P1-7)."""
import json
import pickle
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from gateway_config import ConfigStore, ConnectionMeta, EndpointMeta, UserMeta, ACLEntry


@pytest.fixture()
def store_path(tmp_path):
    return tmp_path / "gateway_data.json"


def _seed(store: ConfigStore):
    store.add_connection(ConnectionMeta(alias="c1", db_type="mysql", host="h",
                                        user="u", password="pw", db_name="d"))
    conn = store.get_connection_by_alias("c1")
    store.add_endpoint(EndpointMeta(alias="e1", connection_id=conn.id,
                                    allowed_tables=["orders"]))
    ep = store.get_endpoint_by_alias("e1")
    store.add_user(UserMeta(username="alice"))
    user = store.list_users()[0]
    store.add_acl(user.id, ep.id)
    return conn, ep, user


def test_json_round_trip(store_path):
    s = ConfigStore(store_path)
    conn, ep, user = _seed(s)
    # file is JSON, password present in storage (write-only at API layer)
    raw = json.loads(store_path.read_text(encoding="utf-8"))
    assert raw["connections"][0]["password"] == "pw"

    s2 = ConfigStore(store_path)  # reload
    assert s2.get_connection(conn.id).host == "h"
    assert s2.get_endpoint(ep.id).allowed_tables == ["orders"]
    assert s2.get_user(user.id).username == "alice"
    assert len(s2.list_acl()) == 1


def test_legacy_pickle_migrated(tmp_path):
    js = tmp_path / "gateway_data.json"
    legacy = tmp_path / "gateway_data.pkl"  # what ConfigStore(path) looks for
    conn = ConnectionMeta(alias="c1", db_type="pgsql", host="h2")
    with open(legacy, "wb") as f:
        pickle.dump({"connections": [conn.model_dump()], "endpoints": [],
                     "users": [], "acl": []}, f)
    s = ConfigStore(js)
    assert s.get_connection_by_alias("c1").host == "h2"
    assert js.exists()                                # migrated to JSON
    assert tmp_path / "gateway_data.pkl.bak" in list(tmp_path.iterdir())  # backup kept


def test_cascade_delete(store_path):
    s = ConfigStore(store_path)
    conn, ep, user = _seed(s)
    s.delete_connection(conn.id)
    assert s.list_connections() == []
    assert s.list_endpoints() == []
    assert s.list_acl() == []
    assert len(s.list_users()) == 1  # users survive
