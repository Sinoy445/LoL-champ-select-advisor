#!/usr/bin/env python
"""Import OP.GG data (official MCP server) into the advisor's data files.

Run from the project root:   python tools/import_opgg.py
Options: --refresh (ignore cache)  --offline (cache only)  --min-play N  --shrink K

Raw responses are cached in data/_opgg_cache/ so re-runs are fast and debuggable.
"""
import argparse, hashlib, json, re, shutil, sys, time
from pathlib import Path

URL = "https://mcp-api.op.gg/mcp"
ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
CACHE = DATA / "_opgg_cache"

# Your pool (edit here). Roles use the engine names.
POOL = {
    "JUNGLE": ["Sylas", "Wukong", "Shen", "Viego", "Master Yi", "Aatrox"],
    "SUPPORT": ["Braum", "Thresh", "Blitzcrank", "Rakan"],
    "TOP": ["Aatrox", "Fiora", "Camille", "Shen", "Wukong"],
    "MID": ["Sylas", "Kai'Sa"],
    "ADC": ["Kai'Sa", "Ashe", "Ezreal", "Jhin", "Lucian"],
}
LANES = ["top", "mid", "jungle", "adc", "support"]


def norm(s):
    return re.sub(r"[^a-z0-9]", "", str(s).lower())


# ----------------------------------------------------------------- MCP client
class MCP:
    def __init__(self):
        import requests
        self.requests = requests
        self.s = requests.Session()
        self.sid = None
        self.n = 0

    def _post(self, body):
        h = {"Content-Type": "application/json",
             "Accept": "application/json, text/event-stream"}
        if self.sid:
            h["Mcp-Session-Id"] = self.sid
        r = self.s.post(URL, json=body, headers=h, timeout=90)
        r.raise_for_status()
        self.sid = r.headers.get("Mcp-Session-Id") or self.sid
        if "id" not in body:
            return None
        if "text/event-stream" in r.headers.get("Content-Type", ""):
            for line in r.text.splitlines():
                if line.startswith("data:"):
                    try:
                        m = json.loads(line[5:].strip())
                    except ValueError:
                        continue
                    if "id" in m and ("result" in m or "error" in m):
                        return m
            raise RuntimeError("no JSON-RPC reply in SSE stream")
        return r.json()

    def rpc(self, method, params=None):
        self.n += 1
        m = self._post({"jsonrpc": "2.0", "id": self.n, "method": method,
                        "params": params or {}})
        if "error" in m:
            raise RuntimeError(f"{method}: {m['error']}")
        return m["result"]

    def start(self):
        self.rpc("initialize", {"protocolVersion": "2025-03-26", "capabilities": {},
                                "clientInfo": {"name": "lol-advisor", "version": "1"}})
        self._post({"jsonrpc": "2.0", "method": "notifications/initialized"})

    def tools(self):
        out, cursor = [], None
        while True:
            res = self.rpc("tools/list", {"cursor": cursor} if cursor else {})
            out += res.get("tools", [])
            cursor = res.get("nextCursor")
            if not cursor:
                return out

    def call(self, name, args):
        res = self.rpc("tools/call", {"name": name, "arguments": args})
        text = "\n".join(c.get("text", "") for c in res.get("content", [])
                         if c.get("type") == "text")
        if res.get("isError"):
            raise RuntimeError(text[:300] or "tool error")
        return text


class Source:
    """Cached tool calls. Offline mode only reads the cache."""

    def __init__(self, refresh, offline):
        self.refresh, self.offline = refresh, offline
        self.mcp = None
        self.tool_defs = None
        CACHE.mkdir(parents=True, exist_ok=True)

    def _mcp(self):
        if self.mcp is None:
            self.mcp = MCP()
            self.mcp.start()
        return self.mcp

    def tools(self):
        if self.tool_defs is None:
            f = CACHE / "_tools.json"
            if f.exists() and not self.refresh:
                self.tool_defs = json.loads(f.read_text(encoding="utf-8"))
            elif not self.offline:
                self.tool_defs = self._mcp().tools()
                f.write_text(json.dumps(self.tool_defs), encoding="utf-8")
            else:
                self.tool_defs = []
        return {t["name"]: t for t in self.tool_defs}

    def available_fields(self, tool):
        t = self.tools().get(tool)
        if not t:
            return []
        d = t["inputSchema"]["properties"].get("desired_output_fields", {}).get("description", "")
        part = d.split("Available fields:")[-1].split("Server-injected")[0]
        return [ln[2:].strip() for ln in part.splitlines() if ln.startswith("- ")]

    def call(self, tool, args):
        key = hashlib.md5(json.dumps(args, sort_keys=True).encode()).hexdigest()[:8]
        f = CACHE / f"{tool}_{key}.txt"
        if f.exists() and not self.refresh:
            return f.read_text(encoding="utf-8")
        if self.offline:
            raise RuntimeError("not cached (offline)")
        text = self._mcp().call(tool, args)
        f.write_text(text, encoding="utf-8")
        time.sleep(0.3)
        return text

    def default_args(self, tool, **over):
        """Fill required args generically (all fields for desired_output_fields)."""
        t = self.tools().get(tool, {})
        props = t.get("inputSchema", {}).get("properties", {})
        args = {}
        for k in t.get("inputSchema", {}).get("required", []):
            p = props.get(k, {})
            if k == "desired_output_fields":
                args[k] = self.available_fields(tool)
            elif k == "game_mode":
                args[k] = "ranked"
            elif k == "position":
                args[k] = "all"
            elif k == "lang":
                args[k] = "en_US"
            elif p.get("enum"):
                args[k] = p["enum"][0]
        args.update(over)
        return args


# --------------------------------------------------- compact response parser
HDR = re.compile(r"^class (\w+):\s*(.*)$")
NUM = re.compile(r"-?\d+(?:\.\d+)?(?:[eE][-+]?\d+)?")
IDENT = re.compile(r"[A-Za-z_]\w*")


def parse_compact(text):
    """Parse 'class X: a,b' header lines + 'X(arg,Y(..),[..])' body into dicts."""
    schema, body = {}, []
    for ln in text.splitlines():
        m = HDR.match(ln)
        if m:
            schema[m.group(1)] = [f for f in m.group(2).split(",") if f]
        elif ln.strip():
            body.append(ln.strip())
    src = "".join(body)
    pos = 0

    def ws():
        nonlocal pos
        while pos < len(src) and src[pos] in " \t\r\n":
            pos += 1

    def value():
        nonlocal pos
        ws()
        c = src[pos]
        if c == '"':
            i = pos + 1
            while src[i] != '"':
                i += 2 if src[i] == "\\" else 1
            s = json.loads(src[pos:i + 1])
            pos = i + 1
            return s
        if c == "[":
            pos += 1
            out = []
            ws()
            while src[pos] != "]":
                out.append(value())
                ws()
                if src[pos] == ",":
                    pos += 1
                ws()
            pos += 1
            return out
        m = NUM.match(src, pos)
        if m and (c == "-" or c.isdigit()):
            pos = m.end()
            f = float(m.group())
            return int(f) if re.fullmatch(r"-?\d+", m.group()) else f
        m = IDENT.match(src, pos)
        if not m:
            raise ValueError(f"parse error near: {src[pos:pos+40]!r}")
        name = m.group()
        pos = m.end()
        if name in ("true", "false", "null", "True", "False", "None"):
            return {"true": True, "True": True, "false": False, "False": False}.get(name)
        ws()
        if pos < len(src) and src[pos] == "(":
            pos += 1
            args = []
            ws()
            while src[pos] != ")":
                args.append(value())
                ws()
                if src[pos] == ",":
                    pos += 1
                ws()
            pos += 1
            fields = schema.get(name)
            if fields and len(fields) == len(args):
                return dict(zip(fields, args))
            return {"_class": name, "_args": args}
        return name

    return value() if src else {}


# ------------------------------------------------------------------- helpers
def g(d, *path, default=None):
    for p in path:
        if isinstance(d, dict) and p in d:
            d = d[p]
        else:
            return default
    return d


def shrink(wr_pct, play, k):
    """Pull a win rate toward 50 when the sample is thin."""
    return 50.0 + (wr_pct - 50.0) * play / (play + k)


def pick_fields(avail, prefixes, exclude=()):
    return [f for f in avail
            if any(f.startswith(p) for p in prefixes) and not any(e in f for e in exclude)]


def candidates(name, key):
    snake_key = re.sub(r"(?<!^)(?=[A-Z])", "_", key).upper()
    c = [re.sub(r"[^A-Za-z0-9]+", "_", name).strip("_").upper(),
         re.sub(r"[^A-Za-z0-9]", "", name).upper(),
         snake_key, key.upper()]
    seen, out = set(), []
    for x in c:
        if x and x not in seen:
            seen.add(x)
            out.append(x)
    return out


# --------------------------------------------------------------------- main
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--refresh", action="store_true")
    ap.add_argument("--offline", action="store_true")
    ap.add_argument("--min-play", type=int, default=20)
    ap.add_argument("--shrink", type=float, default=150.0)
    a = ap.parse_args()
    src = Source(a.refresh, a.offline)
    warn = []
    pf = DATA / "champion_pools.json"
    if pf.exists():
        try:
            pools = json.loads(pf.read_text(encoding="utf-8"))["pools"]
            POOL.clear()
            POOL.update({k.upper(): list(v) for k, v in pools.items()})
            print("pool loaded from champion_pools.json")
        except Exception as e:
            print(f"pool file unreadable ({e}); using built-in pool")

    # 1) champion table
    txt = src.call("lol_list_champions", src.default_args("lol_list_champions"))
    champs = [c for c in g(parse_compact(txt), "data", "champions", default=[])
              if isinstance(c, dict) and c.get("champion_id", 0) < 60000]
    by_norm, by_id = {}, {}
    for c in champs:
        cid = int(c["champion_id"])
        by_id[cid] = c
        by_norm[norm(c["name"])] = cid
        by_norm.setdefault(norm(c["key"]), cid)
    print(f"champions: {len(champs)}")

    def cid_of(name):
        return by_norm.get(norm(name))

    # 2) lane meta -> winrates + roles
    txt = src.call("lol_list_lane_meta_champions",
                   src.default_args("lol_list_lane_meta_champions"))
    positions = g(parse_compact(txt), "data", "positions", default={})
    winrates, roles = {}, {}
    for lane in LANES:
        role = {"top": "TOP", "mid": "MID", "jungle": "JUNGLE", "adc": "ADC",
                "support": "SUPPORT"}[lane]
        for row in positions.get(lane, []):
            cid = cid_of(row.get("champion", ""))
            if cid is None:
                warn.append(f"lane_meta: unmapped champion {row.get('champion')!r}")
                continue
            if row.get("play", 0) >= a.min_play:
                wr = 100.0 * row["win"] / row["play"] if row.get("win") is not None else row["win_rate"] * 100
                winrates.setdefault(role, {})[row["champion"]] = round(wr, 2)
            if row.get("role_rate", 0) >= 0.25 and row.get("play", 0) >= 50:
                roles.setdefault(role, []).append(cid)
    print("winrates:", {r: len(v) for r, v in winrates.items()})
    print("roles   :", {r: len(v) for r, v in roles.items()})
    for r, v in winrates.items():
        srt = sorted(v.items(), key=lambda kv: -kv[1])
        print(f"  {r:8s} top: {srt[:3]}  bottom: {srt[-2:]}")

    # 3) per-champion analysis for the pool
    an_avail = src.available_fields("lol_get_champion_analysis")
    f1 = (["position"] if "position" in an_avail else []) + \
        [f for f in an_avail if f == "data.damage_type"] + \
        pick_fields(an_avail, ["data.strong_counters", "data.weak_counters",
                               "data.synergies."], exclude=["tier_data"])
    f2 = ["position"] + pick_fields(an_avail, ["data.summary.positions[].counters[]"])

    mu_acc = {}     # (mine, opp) -> [sum wr*play, sum play]
    syn_acc = {}    # (mine, other) -> [sum wr*play, sum play]
    damage = {}
    AGREE = []

    def analyse(name, lane):
        cid = cid_of(name)
        c = by_id.get(cid)
        if not c:
            warn.append(f"pool: cannot map {name!r}")
            return
        last = None
        for cand in candidates(c["name"], c["key"]):
            try:
                args = {"game_mode": "ranked", "champion": cand, "position": lane,
                        "desired_output_fields": f1}
                r = parse_compact(src.call("lol_get_champion_analysis", args))
                data = g(r, "data")
                if not isinstance(data, dict):
                    continue
            except Exception as e:  # try next spelling
                last = e
                continue
            break
        else:
            warn.append(f"{name} {lane}: no data ({last})")
            return
        damage[str(cid)] = data.get("damage_type")
        rows = []
        for key in ("strong_counters", "weak_counters"):
            for x in data.get(key) or []:
                if x.get("my_win_rate") is not None and x.get("play"):
                    rows.append((x["champion_id"], x["my_win_rate"] * 100, x["play"]))
        used = "counters"
        raw = {}
        try:
            args2 = {"game_mode": "ranked", "champion": cand, "position": lane,
                     "desired_output_fields": f2}
            r2 = parse_compact(src.call("lol_get_champion_analysis", args2))
            for p in g(r2, "data", "summary", "positions", default=[]) or []:
                for x in p.get("counters") or []:
                    if x.get("play") and x.get("win") is not None:
                        # ASSUMPTION: win = wins of the selected champion
                        raw[int(x["champion_id"])] = (100.0 * x["win"] / x["play"], x["play"])
        except Exception as e:
            warn.append(f"{name} {lane}: raw counters failed ({e})")
        have = {int(o) for o, _, _ in rows}
        for o, w, pl in rows:           # agreement check: trusted vs raw
            if int(o) in raw:
                AGREE.append(abs(raw[int(o)][0] - w))
        rows += [(o, w, pl) for o, (w, pl) in raw.items() if o not in have]
        used = f"{len(have)} trusted + {len(rows) - len(have)} raw"
        seen = {}
        for opp, wr, play in rows:  # dedupe, keep biggest sample
            if opp not in seen or play > seen[opp][1]:
                seen[opp] = (wr, play)
        for opp, (wr, play) in seen.items():
            if play >= a.min_play and int(opp) != cid:
                acc = mu_acc.setdefault((cid, int(opp)), [0.0, 0])
                acc[0] += wr * play
                acc[1] += play
        n_syn = 0
        for role_key, lst in (data.get("synergies") or {}).items():
            for x in lst or []:
                if x.get("play", 0) >= a.min_play and x.get("win_rate") is not None:
                    acc = syn_acc.setdefault((cid, int(x["synergy_champion_id"])), [0.0, 0])
                    acc[0] += x["win_rate"] * 100 * x["play"]
                    acc[1] += x["play"]
                    n_syn += 1
        print(f"  {name:11s} {lane:8s} matchups={len(seen):3d} ({used})  synergies={n_syn}")

    print("analysis:")
    for role, names in POOL.items():
        for n in names:
            analyse(n, role.lower())

    if AGREE:
        print(f"raw-vs-trusted check: {len(AGREE)} overlapping matchups, "
              f"mean |diff| = {sum(AGREE)/len(AGREE):.2f} pts (should be ~0-2)")
    matchups = {}
    for (me, opp), (s, p) in mu_acc.items():
        matchups.setdefault(str(me), {})[str(opp)] = round(shrink(s / p, p, a.shrink), 2)
    synergies = {}
    for (me, ally), (s, p) in syn_acc.items():
        synergies.setdefault(str(me), {})[str(ally)] = round(shrink(s / p, p, a.shrink) - 50.0, 2)

    # mirror entries so lookups work in either direction (own measurements win)
    for me in list(matchups):
        for opp, wr in list(matchups[me].items()):
            matchups.setdefault(opp, {}).setdefault(me, round(100.0 - wr, 2))
    for me in list(synergies):
        for ally, pts in list(synergies[me].items()):
            synergies.setdefault(ally, {}).setdefault(me, pts)

    # 4) write
    def backup(path):
        if path.exists():
            b = path.with_name(path.name + ".pre_opgg.bak")
            if not b.exists():
                shutil.copy(path, b)

    mfile = DATA / "matchups.json"
    existing = json.loads(mfile.read_text(encoding="utf-8")) if mfile.exists() else {}
    if isinstance(existing, dict) and "matchups" in existing and "synergies" in existing and isinstance(existing["matchups"], dict):
        backup(mfile)
        existing["matchups"], existing["synergies"] = matchups, synergies
        mfile.write_text(json.dumps(existing, indent=1), encoding="utf-8")
        print("wrote matchups.json (matchups + synergies replaced; counters untouched)")
    else:
        out = DATA / "opgg_matchups.json"
        out.write_text(json.dumps({"matchups": matchups, "synergies": synergies}, indent=1),
                       encoding="utf-8")
        warn.append("matchups.json has no 'matchups'/'synergies' keys -> wrote data/opgg_matchups.json instead")
    wf = DATA / "champion_winrates.json"
    wd = json.loads(wf.read_text(encoding="utf-8")) if wf.exists() else {"version": "1.0"}
    backup(wf)
    wd["winrates"] = {r: dict(sorted(v.items(), key=lambda kv: -kv[1])) for r, v in winrates.items()}
    wd["description"] = "Win rates (%) per role from OP.GG lane meta (imported by tools/import_opgg.py)."
    wf.write_text(json.dumps(wd, indent=2, ensure_ascii=False), encoding="utf-8")
    cf = DATA / "champions.json"
    cd = json.loads(cf.read_text(encoding="utf-8"))
    backup(cf)
    old = cd.get("roles", {})
    cd["roles"] = {r: sorted(set(v)) for r, v in roles.items()}
    cf.write_text(json.dumps(cd, indent=2, ensure_ascii=False), encoding="utf-8")
    (DATA / "opgg_damage_type.json").write_text(json.dumps(damage, indent=1), encoding="utf-8")
    print("wrote champion_winrates.json, champions.json roles "
          + str({r: f"{len(old.get(r, []))}->{len(cd['roles'].get(r, []))}" for r in cd["roles"]})
          + ", opgg_damage_type.json")
    print(f"matchups: {sum(len(v) for v in matchups.values())} pairs, "
          f"synergies: {sum(len(v) for v in synergies.values())} pairs")
    for w in warn:
        print("WARN", w)


if __name__ == "__main__":
    main()