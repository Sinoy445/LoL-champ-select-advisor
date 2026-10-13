"""Patch src/engine.py: nominate the single most threatening enemy counter.
Run from project root:  python tools\\patch_counters.py   (backs up to engine.py.pre_counter.bak)
"""
import shutil, sys
from pathlib import Path

P = Path(__file__).resolve().parent.parent / "src" / "engine.py"
s = P.read_text(encoding="utf-8")
if "_counter_threat" in s:
    sys.exit("already patched")


def sub(old, new):
    global s
    if s.count(old) != 1:
        sys.exit(f"ABORT (nothing written): expected 1 match, found {s.count(old)} for:\n{old[:80]}")
    s = s.replace(old, new)


sub("FULL_CONFIDENCE_ALLIES = 3  # comp factors fade in until this many allies are known\n",
    "FULL_CONFIDENCE_ALLIES = 3  # comp factors fade in until this many allies are known\n"
    "\n# Individual counters: only the most threatening enemy counts fully\n"
    "COUNTER_REST_WEIGHT = 0.25   # each further counter on the enemy team counts at this fraction\n"
    "MATCHUP_AS_COUNTER = 0.6     # lane-matchup data reused for non-lane enemies (cross-role proxy)\n"
    "MIN_COUNTER_SIGNAL = 1.5     # ignore bonuses smaller than this many points\n")

sub("    is_current: bool = False  # this is the champ you are currently hovering\n",
    "    is_current: bool = False  # this is the champ you are currently hovering\n"
    "    main_counter: str = \"\"    # most threatening enemy champion for this pick\n")

sub("        self.traits: Dict[int, Dict[str, float]] = {}  # id -> ad_share/damage_weight/frontline\n",
    "        self.traits: Dict[int, Dict[str, float]] = {}  # id -> ad_share/damage_weight/frontline\n"
    "        self.threat: Dict[int, float] = {}             # id -> kit threat multiplier (overrides)\n")

sub("                cur.update({f: float(t[f]) for f in TRAIT_FIELDS if f in t})\n",
    "                cur.update({f: float(t[f]) for f in TRAIT_FIELDS if f in t})\n"
    "                if \"threat\" in t:\n"
    "                    self.threat[int(k)] = float(t[\"threat\"])\n")

sub("    # ------------------------------------------------------------- comp logic\n",
    '''    def _counter_threat(self, pick: int, enemy: int) -> float:
        """Points: >0 pick counters enemy, <0 enemy counters pick.
        Uses the counters table; if it has nothing, falls back to matchup data.
        Negative values are scaled by the enemy's kit threat (overrides 'threat', default 1)."""
        raw = self._counter_bonus(pick, enemy)
        if not raw:
            wr = self._matchup_winrate(pick, enemy)
            raw = (wr - 50.0) * MATCHUP_AS_COUNTER if wr is not None else 0.0
        if abs(raw) < MIN_COUNTER_SIGNAL:
            return 0.0
        return raw * self.threat.get(enemy, 1.0) if raw < 0 else raw

    # ------------------------------------------------------------- comp logic
''')

a = s.index("        # 3. Counters vs the rest of the enemy team")
endmark = '        total += w["counter"] * _clip(ctr_sum / CAPS["counter"])\n'
b = s.index(endmark, a) + len(endmark)
s = s[:a] + '''        # 3. Individual counters among the rest of the enemy team (lane opponent is factor 1).
        #    The most threatening enemy counts fully; further counters count at a fraction.
        threats = []
        for enemy in _unique(enemy_team):
            if enemy == enemy_lane_opponent:
                continue
            tb = self._counter_threat(pick_id, enemy)
            if tb:
                threats.append((tb, enemy))
        neg = sorted(t for t in threats if t[0] < 0)
        pos = sorted((t for t in threats if t[0] > 0), reverse=True)
        ctr_sum, main_counter = 0.0, ""
        for group in (neg, pos):
            if not group:
                continue
            main_b, main_e = group[0]
            ctr_sum += main_b + COUNTER_REST_WEIGHT * sum(x for x, _ in group[1:])
            cname = self.get_champion_name(main_e)
            if main_b < 0:
                main_counter = cname
                text = f"✗ Main counter: {cname} ({main_b:.1f})"
            else:
                text = f"✓ Counters {cname} (+{main_b:.1f})"
            contribs.append((w["counter"] * main_b / CAPS["counter"], text))
        total += w["counter"] * _clip(ctr_sum / CAPS["counter"])
''' + s[b:]

sub("            has_matchup_data=has_matchup,\n        )",
    "            has_matchup_data=has_matchup,\n            main_counter=main_counter,\n        )")

bak = P.with_name("engine.py.pre_counter.bak")
if not bak.exists():
    shutil.copy(P, bak)
P.write_text(s, encoding="utf-8")
print("patched", P)