"""
Scoring engine: ranks the champions in your pool for the current champ-select state.

How the score works
-------------------
Each factor is first converted to a common -1..+1 scale (see CAPS), then combined
using WEIGHTS (re-normalised to sum to 1):

    score = 50 + SCORE_SPREAD * sum(weight_i * factor_i)

so a weight is the factor's real maximum influence. (Before, raw numbers on very
different scales were multiplied by the weights, so e.g. synergy bonuses of +8..+12
could outweigh the "35%" lane matchup, and every score landed between ~48 and ~54.)

Role fit is no longer a scored factor: candidates already come from your pool for the
role (or from the roles table if you have no pool), so it could only add noise.
Missing data counts as neutral (0), never as a penalty.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Set, Tuple

# ---------------------------------------------------------------- tuning knobs
WEIGHTS = {"lane": 0.35, "synergy": 0.25, "counter": 0.20, "meta": 0.10}
# Value at which a factor counts as "maximum" (+1 / -1):
CAPS = {"lane": 8.0,      # matchup win rate points above/below 50
        "synergy": 10.0,  # summed synergy bonus across allies
        "counter": 10.0,  # summed counter bonus across enemies
        "meta": 5.0}      # meta win rate points above/below 50
SCORE_SPREAD = 10.0       # score of a pick that maxes every factor = 50 + this

# ------------------------------------------------------------------- helpers
_ROLE_ALIASES = {
    "TOP": "TOP",
    "JUNGLE": "JUNGLE", "JUNGLER": "JUNGLE", "JG": "JUNGLE", "JNG": "JUNGLE",
    "MID": "MID", "MIDDLE": "MID",
    "ADC": "ADC", "BOTTOM": "ADC", "BOT": "ADC",
    "SUPPORT": "SUPPORT", "UTILITY": "SUPPORT", "SUP": "SUPPORT",
}

# Names that differ between data sources (normalised form, both directions).
_NAME_ALIASES = [("nunuwillump", "nunu"), ("monkeyking", "wukong")]


def canonical_role(raw) -> Optional[str]:
    """Map LCU / user spellings (middle, bottom, utility, ...) to TOP/JUNGLE/MID/ADC/SUPPORT."""
    if not raw:
        return None
    return _ROLE_ALIASES.get(str(raw).strip().upper())


def _norm(name) -> str:
    """'Kha'Zix', 'khazix', 'KhaZix' -> 'khazix'."""
    return re.sub(r"[^a-z0-9]", "", str(name).lower())


def _clip(x: float) -> float:
    return max(-1.0, min(1.0, x))


def _unique(ids: Iterable[int]) -> List[int]:
    seen, out = set(), []
    for i in ids:
        if i and i not in seen:
            seen.add(i)
            out.append(i)
    return out


def _merge_pairs(pairs):
    """json hook: merge duplicate object keys instead of silently keeping the last one."""
    out = {}
    for k, v in pairs:
        if k in out and isinstance(out[k], dict) and isinstance(v, dict):
            out[k] = {**out[k], **v}
        else:
            out[k] = v
    return out


def _load_json(path: Path, merge_duplicates: bool = False):
    with open(path, encoding="utf-8") as f:
        return json.load(f, object_pairs_hook=_merge_pairs if merge_duplicates else None)


def _pairs(table: Dict[str, Dict[str, float]]) -> Dict[Tuple[int, int], float]:
    out = {}
    for a, row in table.items():
        for b, v in row.items():
            out[(int(a), int(b))] = float(v)
    return out


# ------------------------------------------------------------------- results
@dataclass
class PickRecommendation:
    champion_id: int
    champion_name: str
    score: float
    matchup_score: float
    synergy_score: float
    counter_score: float
    reasons: List[str]
    has_matchup_data: bool = False
    is_current: bool = False  # this is the champ you are currently hovering


# -------------------------------------------------------------------- engine
class ScoringEngine:
    def __init__(self, data_dir: Path):
        self.data_dir = Path(data_dir)
        self.champions: Dict[str, str] = {}          # "238" -> "Zed"
        self.roles: Dict[str, List[int]] = {}
        self.champ_roles: Dict[int, Set[str]] = {}   # id -> {"MID", ...}
        self.name_index: Dict[str, int] = {}         # normalised name -> id
        self.pools: Dict[str, List[int]] = {}        # role -> champion ids
        self.pool_warnings: List[str] = []
        self.winrates: Dict[str, Dict[int, float]] = {}
        self._aliases: Dict[int, str] = {}
        self._raw_pools: Dict[str, List[str]] = {}
        self._raw_winrates: Dict[str, Dict[str, float]] = {}
        self._mu: Dict[Tuple[int, int], float] = {}
        self._syn: Dict[Tuple[int, int], float] = {}
        self._ctr: Dict[Tuple[int, int], float] = {}
        total = sum(WEIGHTS.values())
        self._w = {k: v / total for k, v in WEIGHTS.items()}
        self._load_data()
        self._rebuild_indexes()

    # ---------------------------------------------------------------- loading
    def _load_data(self):
        data = _load_json(self.data_dir / "champions.json")
        self.champions = {str(k): v for k, v in data["champions"].items()}
        self.roles = {(canonical_role(k) or k): [int(x) for x in v]
                      for k, v in data.get("roles", {}).items()}
        for role, ids in self.roles.items():
            for cid in ids:
                self.champ_roles.setdefault(cid, set()).add(role)

        mdata = _load_json(self.data_dir / "matchups.json", merge_duplicates=True)
        self._mu = _pairs(mdata.get("matchups", {}))
        self._syn = _pairs(mdata.get("synergies", {}))
        self._ctr = _pairs(mdata.get("counters", {}))

        pools_file = self.data_dir / "champion_pools.json"
        if pools_file.exists():
            for role, names in _load_json(pools_file).get("pools", {}).items():
                self._raw_pools[canonical_role(role) or role.upper()] = list(names)

        wr_file = self.data_dir / "champion_winrates.json"
        if wr_file.exists():
            for role, table in _load_json(wr_file).get("winrates", {}).items():
                self._raw_winrates[canonical_role(role) or role.upper()] = dict(table)

    def _rebuild_indexes(self):
        """(Re)build name -> id lookup, then resolve pool and win-rate names to ids."""
        in_role_table = {c for ids in self.roles.values() for c in ids}
        idx: Dict[str, int] = {}
        for id_str, name in self.champions.items():
            cid = int(id_str)
            for key in {_norm(name), _norm(self._aliases.get(cid, ""))}:
                if key and (key not in idx or cid in in_role_table):
                    idx[key] = cid  # on duplicate names prefer the id the role table knows
        for a, b in _NAME_ALIASES:
            if a in idx and b not in idx:
                idx[b] = idx[a]
            elif b in idx and a not in idx:
                idx[a] = idx[b]
        self.name_index = idx

        self.pools, self.pool_warnings = {}, []
        for role, names in self._raw_pools.items():
            ids: List[int] = []
            for name in names:
                cid = idx.get(_norm(name))
                if cid is None:
                    self.pool_warnings.append(f"{role}: '{name}' not found in champion list")
                elif cid not in ids:
                    ids.append(cid)
            self.pools[role] = ids

        self.winrates = {}
        for role, table in self._raw_winrates.items():
            self.winrates[role] = {idx[_norm(n)]: float(v) for n, v in table.items() if _norm(n) in idx}

    def merge_live_names(self, summary) -> int:
        """Update names from the client's champion summary (fixes stale champions.json)."""
        items = summary.values() if isinstance(summary, dict) else summary
        count = 0
        for item in items:
            try:
                cid = int(item["id"])
            except (KeyError, TypeError, ValueError):
                continue
            if cid <= 0:
                continue
            if item.get("name"):
                self.champions[str(cid)] = item["name"]
                count += 1
            if item.get("alias"):
                self._aliases[cid] = item["alias"]
        self._rebuild_indexes()
        return count

    # ---------------------------------------------------------------- lookups
    def get_champion_name(self, champ_id: int) -> str:
        return self.champions.get(str(champ_id), f"Unknown({champ_id})")

    def has_pool(self, role: str) -> bool:
        return bool(self._raw_pools.get(role))

    def _matchup_winrate(self, attacker: int, defender: int) -> Optional[float]:
        """Attacker's win rate vs defender, or None if we have no data."""
        fwd = self._mu.get((attacker, defender))
        rev = self._mu.get((defender, attacker))
        if fwd is not None and rev is not None:
            return (fwd + (100.0 - rev)) / 2.0
        if fwd is not None:
            return fwd
        if rev is not None:
            return 100.0 - rev
        return None

    def _synergy_bonus(self, pick: int, ally: int) -> float:
        fwd, rev = self._syn.get((pick, ally)), self._syn.get((ally, pick))
        vals = [v for v in (fwd, rev) if v is not None]
        return sum(vals) / len(vals) if vals else 0.0

    def _counter_bonus(self, pick: int, enemy: int) -> float:
        """Positive: pick counters enemy. Negative: enemy counters pick."""
        return self._ctr.get((pick, enemy), 0.0) - self._ctr.get((enemy, pick), 0.0)

    # ---------------------------------------------------------------- scoring
    def score_pick(
        self,
        pick_id: int,
        enemy_lane_opponent: Optional[int],
        enemy_team: List[int],
        allied_picks: List[int],
        my_role: str,
    ) -> PickRecommendation:
        w = self._w
        contribs: List[Tuple[float, str]] = []  # (impact on score, reason)
        total = 0.0

        # 1. Lane matchup
        matchup_score, has_matchup = 50.0, False
        if enemy_lane_opponent:
            opp = self.get_champion_name(enemy_lane_opponent)
            wr = self._matchup_winrate(pick_id, enemy_lane_opponent)
            if wr is None:
                contribs.append((0.0, f"· No matchup data vs {opp}"))
            else:
                matchup_score, has_matchup = wr, True
                impact = w["lane"] * _clip((wr - 50.0) / CAPS["lane"])
                total += impact
                if wr >= 52:
                    contribs.append((impact, f"✓ Counters {opp} ({wr:.1f}%)"))
                elif wr <= 48:
                    contribs.append((impact, f"✗ Weak into {opp} ({wr:.1f}%)"))

        # 2. Synergy with allies
        syn_sum = 0.0
        for ally in _unique(allied_picks):
            bonus = self._synergy_bonus(pick_id, ally)
            if bonus:
                syn_sum += bonus
                sign = "+" if bonus > 0 else ""
                mark = "✓" if bonus > 0 else "✗"
                contribs.append((w["synergy"] * bonus / CAPS["synergy"],
                                 f"{mark} Synergy w/ {self.get_champion_name(ally)} ({sign}{bonus:.1f})"))
        total += w["synergy"] * _clip(syn_sum / CAPS["synergy"])

        # 3. Counters vs the rest of the enemy team (lane opponent is already in factor 1)
        ctr_sum = 0.0
        for enemy in _unique(enemy_team):
            if enemy == enemy_lane_opponent:
                continue
            bonus = self._counter_bonus(pick_id, enemy)
            if bonus:
                ctr_sum += bonus
                name = self.get_champion_name(enemy)
                if bonus > 0:
                    text = f"✓ Counters {name} (+{bonus:.1f})"
                else:
                    text = f"✗ Countered by {name} ({bonus:.1f})"
                contribs.append((w["counter"] * bonus / CAPS["counter"], text))
        total += w["counter"] * _clip(ctr_sum / CAPS["counter"])

        # 4. Meta win rate (tie-breaker)
        meta = self.winrates.get(my_role, {}).get(pick_id)
        if meta is not None:
            impact = w["meta"] * _clip((meta - 50.0) / CAPS["meta"])
            total += impact
            if meta >= 52:
                contribs.append((impact, f"✓ Strong meta pick ({meta:.1f}% WR)"))
            elif meta <= 48:
                contribs.append((impact, f"✗ Weak meta pick ({meta:.1f}% WR)"))

        contribs.sort(key=lambda c: abs(c[0]), reverse=True)
        return PickRecommendation(
            champion_id=pick_id,
            champion_name=self.get_champion_name(pick_id),
            score=round(50.0 + SCORE_SPREAD * total, 1),
            matchup_score=round(matchup_score, 1),
            synergy_score=round(syn_sum, 1),
            counter_score=round(ctr_sum, 1),
            reasons=[text for _, text in contribs[:3]],
            has_matchup_data=has_matchup,
        )

    def get_recommendations(
        self,
        my_role: str,
        enemy_lane_opponent: Optional[int],
        enemy_team: List[int],
        allied_picks: List[int],
        banned: List[int],
        my_pick_intent: int = 0,
        top_n: int = 5,
        available: Optional[Set[int]] = None,
    ) -> List[PickRecommendation]:
        """Top N picks for a role. `available` (optional) = ids you own / can pick."""
        role = canonical_role(my_role)
        if role is None:
            return []

        # Your pool is authoritative when you have one (the roles table is incomplete:
        # it lacks Wukong entirely and Shen/Sylas/Aatrox in some of your lanes).
        if self.has_pool(role):
            base = self.pools.get(role, [])
        else:
            base = self.roles.get(role, [])

        excluded = {c for c in (*banned, *enemy_team, *allied_picks) if c}
        candidates = [c for c in _unique(base) if c not in excluded]
        if available is not None:
            candidates = [c for c in candidates if c in available]

        scored = [self.score_pick(c, enemy_lane_opponent, enemy_team, allied_picks, role)
                  for c in candidates]
        for rec in scored:
            rec.is_current = bool(my_pick_intent) and rec.champion_id == my_pick_intent
        scored.sort(key=lambda r: r.score, reverse=True)
        return scored[:top_n]


def create_engine() -> ScoringEngine:
    data_dir = Path(__file__).parent.parent / "data"
    return ScoringEngine(data_dir)