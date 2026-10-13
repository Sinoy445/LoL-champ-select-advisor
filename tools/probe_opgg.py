"""
Explore OP.GG's official MCP server (https://mcp-api.op.gg/mcp) so an importer can be written
against its real response format, which isn't documented in their README.

    python tools/probe_opgg.py                      # list tools + their input schemas
    python tools/probe_opgg.py --preset all         # the three requests the importer needs
    python tools/probe_opgg.py --call lol_get_lane_matchup_guide --args '{"champion": "Aatrox", ...}'

Everything is saved under data/_probe/ so you can share the raw files.
Use the argument names from the schema printed by the first command. OP.GG's tools take a
`desired_output_fields` list (see their README) to choose which fields come back.

Why this doesn't use the `mcp` package: OP.GG's tool list contains two tools whose outputSchema
has type "array", which the MCP spec forbids, and mcp 2.x refuses the whole list because of it.
MCP over HTTP is plain JSON-RPC, so this talks to the server directly (needs only `requests`).
"""
import argparse
import json
import sys
from pathlib import Path

import requests

DEFAULT_URL = "https://mcp-api.op.gg/mcp"
OUT_DIR = Path(__file__).resolve().parent.parent / "data" / "_probe"
CLIENT_PROTOCOL = "2025-06-18"


# Ready-made requests for the importer's three data needs. Field lines are copied verbatim from the
# server's own schema (data/_probe/tools.json), which treats them as a closed set.
_POS = ("top", "jungle", "mid", "adc", "support")
_LANE_FIELDS = ("{ban_rate,champion,is_rip,kda,kill,pick_rate,play,rank,rank_prev,rank_prev_patch,"
                "role_rate,tier,win,win_rate}")
_SYN_FIELDS = ("{champion_id,champion_name,play,position,score,score_rank,synergy_champion_id,"
               "synergy_champion_name,synergy_position,win,win_rate}")
_CTR_FIELDS = "{champion_id,champion_name,counter_win_rate,my_win_rate,play,win,win_rate}"

PRESETS = {
    "lane_meta": ("lol_list_lane_meta_champions", {
        "position": "all",
        "desired_output_fields": [f"data.positions.{p}[].{_LANE_FIELDS}" for p in _POS] + ["position_filter"],
    }),
    "aatrox": ("lol_get_champion_analysis", {
        "champion": "AATROX", "game_mode": "ranked", "position": "top",
        "desired_output_fields": [
            "champion", "position", "data.{damage_type,mythic_items}",
            "data.summary.average_stats.{ban_rate,kda,pick_rate,play,rank,tier,win_rate}",
            "data.summary.positions[].name",
            "data.summary.positions[].stats.{ban_rate,kda,pick_rate,play,role_rate,win_rate}",
            f"data.strong_counters[].{_CTR_FIELDS}", f"data.weak_counters[].{_CTR_FIELDS}",
        ] + [f"data.synergies.{p}[].{_SYN_FIELDS}" for p in ("adc", "jungle", "mid", "support")],
    }),
    "champions": ("lol_list_champions", {
        "desired_output_fields": ["data.champions[].{champion_id,key,name,release_date}", "lang"],
    }),
}


class MCPError(Exception):
    pass


class MCPHttp:
    """Minimal MCP-over-HTTP (streamable HTTP) client: initialize, tools/list, tools/call."""

    def __init__(self, url: str, timeout: float = 60):
        self.url, self.timeout = url, timeout
        self.http = requests.Session()
        self.session_id = None
        self.protocol = CLIENT_PROTOCOL
        self._next_id = 0

    def _post(self, payload: dict) -> requests.Response:
        headers = {"Content-Type": "application/json", "Accept": "application/json, text/event-stream"}
        if self.session_id:
            headers["Mcp-Session-Id"] = self.session_id
        if self.protocol:
            headers["MCP-Protocol-Version"] = self.protocol
        r = self.http.post(self.url, json=payload, headers=headers, timeout=self.timeout)
        r.raise_for_status()
        self.session_id = r.headers.get("Mcp-Session-Id") or self.session_id
        return r

    @staticmethod
    def _extract(r: requests.Response, req_id: int) -> dict:
        """The reply is either plain JSON or a server-sent-events stream; find our response."""
        if "text/event-stream" in r.headers.get("content-type", ""):
            for line in r.text.splitlines():
                if line.startswith("data:"):
                    try:
                        msg = json.loads(line[5:].strip())
                    except ValueError:
                        continue
                    if isinstance(msg, dict) and msg.get("id") == req_id:
                        return msg
            raise MCPError("no JSON-RPC response found in the event stream")
        msg = r.json()
        if isinstance(msg, list):  # a batched reply
            msg = next((m for m in msg if m.get("id") == req_id), None)
        if not isinstance(msg, dict):
            raise MCPError(f"unexpected response: {r.text[:200]!r}")
        return msg

    def request(self, method: str, params: dict | None = None) -> dict:
        self._next_id += 1
        payload = {"jsonrpc": "2.0", "id": self._next_id, "method": method}
        if params is not None:
            payload["params"] = params
        msg = self._extract(self._post(payload), self._next_id)
        if "error" in msg:
            raise MCPError(f"{method}: {json.dumps(msg['error'])[:500]}")
        return msg.get("result", {})

    def notify(self, method: str) -> None:
        self._post({"jsonrpc": "2.0", "method": method})

    def initialize(self) -> dict:
        result = self.request("initialize", {
            "protocolVersion": CLIENT_PROTOCOL, "capabilities": {},
            "clientInfo": {"name": "lol-advisor-probe", "version": "0.1"}})
        self.protocol = result.get("protocolVersion", CLIENT_PROTOCOL)
        self.notify("notifications/initialized")
        return result

    def list_tools(self) -> list:
        tools, cursor = [], None
        while True:
            result = self.request("tools/list", {"cursor": cursor} if cursor else {})
            tools += result.get("tools", [])
            cursor = result.get("nextCursor")
            if not cursor:
                return tools

    def call_tool(self, name: str, arguments: dict) -> dict:
        return self.request("tools/call", {"name": name, "arguments": arguments})


def save_call(client: "MCPHttp", label: str, tool: str, args: dict) -> None:
    result = client.call_tool(tool, args)
    text = "\n".join(b.get("text", json.dumps(b)) for b in result.get("content", []))
    (OUT_DIR / f"{label}.txt").write_text(text, encoding="utf-8")
    (OUT_DIR / f"{label}.raw.json").write_text(json.dumps(result, indent=2), encoding="utf-8")
    print(f"[{label}] {tool}: isError={result.get('isError')}, {len(text)} chars -> {OUT_DIR / (label + '.txt')}")
    print(text[:600] + ("\n  ... (truncated, see file)" if len(text) > 600 else "") + "\n")


def run(url: str, call: str | None, args: dict, preset: str | None = None) -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    client = MCPHttp(url)
    client.initialize()

    if preset:
        for name in (PRESETS if preset == "all" else [preset]):
            tool, preset_args = PRESETS[name]
            save_call(client, name, tool, preset_args)
        return

    if call is None:
        tools = client.list_tools()
        (OUT_DIR / "tools.json").write_text(json.dumps(tools, indent=2), encoding="utf-8")
        for t in tools:
            schema = t.get("inputSchema") or {}
            required = set(schema.get("required", []))
            params = ", ".join(f"{k}{'*' if k in required else ''}" for k in schema.get("properties", {}))
            print(f"- {t['name']}({params})" + ("  [has outputSchema]" if t.get("outputSchema") else ""))
        print(f"\n{len(tools)} tools. * = required. Full schemas saved to {OUT_DIR / 'tools.json'}")
        return

    save_call(client, call, call, args)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--url", default=DEFAULT_URL)
    ap.add_argument("--preset", choices=[*PRESETS, "all"],
                    help="run ready-made requests (lane_meta, aatrox, champions, or all)")
    ap.add_argument("--call", help="tool name to call (omit to list tools)")
    ap.add_argument("--args", default="{}", help="JSON object with the tool arguments")
    ap.add_argument("--debug", action="store_true", help="show the full traceback on errors")
    a = ap.parse_args()
    try:
        run(a.url, a.call, json.loads(a.args), a.preset)
    except json.JSONDecodeError as exc:
        sys.exit(f"--args is not valid JSON: {exc}")
    except requests.HTTPError as exc:
        if a.debug:
            raise
        r = exc.response
        sys.exit(f"HTTP {r.status_code} {r.reason} from {a.url}\n  body: {r.text[:500]!r}")
    except (requests.RequestException, MCPError) as exc:
        if a.debug:
            raise
        sys.exit(f"Could not talk to {a.url}: {type(exc).__name__}: {exc}")


if __name__ == "__main__":
    main()