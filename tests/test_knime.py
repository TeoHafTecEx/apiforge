"""The KNIME adapter, run against a stand-in for knime.scripting.io."""
import json
import sys
import types

import pandas as pd

from apiforge.generate import build_knime_script

SERVICE = '''
def search(query: str = "coffee", limit: int = 5):
    """Matching tariff lines."""
    return [{"code": "0901.11", "query": query, "cookie": os.environ.get("UPSTREAM_COOKIE")}][:limit]

ACTIONS = {"search": search}
'''


def run_in_fake_knime(script: str, requests, flow_vars=None):
    out = {}

    class Table:
        def __init__(self, df):
            self.df = df

        def to_pandas(self):
            return self.df

        @staticmethod
        def from_pandas(df):
            return Table(df)

    io = types.ModuleType("knime.scripting.io")
    io.input_tables = [Table(pd.DataFrame({"json": requests}))]
    io.output_tables = out_list = [None]
    io.flow_variables = flow_vars or {}
    io.Table = Table
    pkg, sub = types.ModuleType("knime"), types.ModuleType("knime.scripting")
    pkg.scripting, sub.io = sub, io
    saved = {k: sys.modules.get(k) for k in ("knime", "knime.scripting", "knime.scripting.io")}
    sys.modules.update({"knime": pkg, "knime.scripting": sub, "knime.scripting.io": io})
    try:
        exec(compile(script, "knime_node.py", "exec"), {"__name__": "__main__"})
    finally:
        for k, v in saved.items():
            if v is None:
                sys.modules.pop(k, None)
            else:
                sys.modules[k] = v
    return [json.loads(r) for r in out_list[0].df["response"]]


def test_runs_as_a_knime_node(monkeypatch):
    monkeypatch.delenv("UPSTREAM_COOKIE", raising=False)
    script = build_knime_script(SERVICE, "hts", "https://hts.usitc.gov", "tariffs")
    reqs = [json.dumps({"action": "search", "params": {"query": "tea"}}),
            {"action": "help"},
            '{"action": "missing"}',
            "not json"]
    res = run_in_fake_knime(script, reqs, {"UPSTREAM_COOKIE": "abc", "other": 1})
    assert res[0] == {"ok": True, "action": "search", "count": 1,
                      "data": [{"code": "0901.11", "query": "tea", "cookie": "abc"}]}
    assert res[1]["data"][0]["action"] == "search"
    assert {"name": "query", "type": "str", "default": "coffee"} in res[1]["data"][0]["params"]
    assert res[2]["ok"] is False and "Unknown action" in res[2]["error"]
    assert res[3]["ok"] is False


def test_does_nothing_outside_knime():
    script = build_knime_script(SERVICE)
    ns = {"__name__": "__main__"}
    exec(compile(script, "knime_node.py", "exec"), ns)  # must not try to read KNIME tables
    assert ns["_forge_handle"]({"action": "search"})["ok"]
