"""
Turns a raw LCU champ-select session into recommendations. No UI code here, so it can be
tested without tkinter and run on a background thread.
"""
import time
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Set, Tuple

from engine import ScoringEngine, canonical_role
from lcu import LCUClient, LCUConnectionError

CONNECT_RETRY_SECONDS = 5.0
SWAP_MARGIN = 2.0   # only mention a swap candidate if it beats the locked pick by this many points
CANDIDATES_SCANNED = 10


@dataclass
class ChampSelectState:
    role: Optional[str]            # TOP / JUNGLE / MID / ADC / SUPPORT, or None if unknown
    role_source: str               # "auto" | "manual" | "unknown"
    lane_opponent: Optional[int]
    lane_candidates: List[int] = field(default_factory=list)  # possible opponents if ambiguous
    enemy_team: List[int] = field(default_factory=list)
    allied_picks: List[int] = field(default_factory=list)     # excludes you
    banned: List[int] = field(default_factory=list)
    my_hover: int = 0
    phase: str = ""
    locked_champion: int = 0       # your locked-in champion id, 0 while you are still choosing


def _champ_id(player: dict) -> int:
    return int(player.get("championId") or 0)


def _locked_pick(session: dict, me: dict, my_cell, phase: str) -> int:
    """Champion id you have locked in, else 0. A completed pick action for your cell is the
    reliable signal; in FINALIZATION the team entry holds the locked champion as a fallback."""
    for group in session.get("actions") or []:
        for act in group or []:
            if (act.get("actorCellId") == my_cell and act.get("type") == "pick"
                    and act.get("completed") and int(act.get("championId") or 0) > 0):
                return int(act["championId"])
    if phase == "FINALIZATION":
        return _champ_id(me)
    return 0


def infer_lane_opponent(
    role: Optional[str], their_team: List[dict], champ_roles: Dict[int, Set[str]]
) -> Tuple[Optional[int], List[int]]:
    """Return (opponent, candidates). Opponent is None unless we can identify it with
    confidence; candidates lists the possibilities when it's ambiguous."""
    if not role:
        return None, []
    picked = [p for p in their_team if _champ_id(p) > 0]

    # Some modes expose the enemy's assigned positions - trust them when present.
    if any(canonical_role(p.get("assignedPosition")) for p in their_team):
        for p in picked:
            if canonical_role(p.get("assignedPosition")) == role:
                return _champ_id(p), []
        return None, []

    # Otherwise: enemy picks that can play my role (per the roles table).
    cands = [_champ_id(p) for p in picked if role in champ_roles.get(_champ_id(p), ())]
    if len(cands) == 1:
        return cands[0], []
    return None, cands


def parse_session(
    session: dict, champ_roles: Dict[int, Set[str]], role_override: Optional[str] = None
) -> Optional[ChampSelectState]:
    my_cell = session.get("localPlayerCellId")
    me = next((p for p in session.get("myTeam", []) if p.get("cellId") == my_cell), None)
    if me is None:
        return None

    assigned = canonical_role(me.get("assignedPosition"))
    override = canonical_role(role_override)
    role = override or assigned
    source = "manual" if override else ("auto" if assigned else "unknown")

    their_team = session.get("theirTeam") or []
    lane, cands = infer_lane_opponent(role, their_team, champ_roles)

    phase = (session.get("timer") or {}).get("phase", "")
    bans = session.get("bans") or {}
    banned = [int(b) for b in (*bans.get("myTeamBans", []), *bans.get("theirTeamBans", [])) if b and int(b) > 0]

    return ChampSelectState(
        role=role,
        role_source=source,
        lane_opponent=lane,
        lane_candidates=cands,
        enemy_team=[_champ_id(p) for p in their_team if _champ_id(p) > 0],
        allied_picks=[_champ_id(p) for p in session.get("myTeam", [])
                      if _champ_id(p) > 0 and p.get("cellId") != my_cell],
        banned=banned,
        my_hover=_champ_id(me) or int(me.get("championPickIntent") or 0),
        phase=phase,
        locked_champion=_locked_pick(session, me, my_cell, phase),
    )


class Advisor:
    def __init__(self, engine: ScoringEngine, client: LCUClient, top_n: int = 3):
        self.engine = engine
        self.client = client
        self.top_n = top_n
        self.owned: Optional[Set[int]] = None
        self._synced = False
        self._last_connect_try = 0.0

    def _no_client(self) -> dict:
        return {"status": "no_client", "message": self.client.last_error}

    def _sync_client_data(self):
        """Once per connection: live champion names (fixes stale champions.json) + ownership."""
        summary = self.client.get_champion_summary()
        if summary:
            self.engine.merge_live_names(summary)
            self._synced = True
        owned = self.client.get_owned_champion_ids()
        # Sanity check so a misparsed response can never wipe out all recommendations.
        self.owned = owned if owned and len(owned) >= 20 else None

    def poll(self, role_override: Optional[str] = None) -> dict:
        """Return a JSON-friendly payload describing what the overlay should show."""
        client = self.client
        if not client.creds:
            now = time.monotonic()
            if now - self._last_connect_try < CONNECT_RETRY_SECONDS:
                return self._no_client()
            self._last_connect_try = now
            self._synced = False
            if not client.connect():
                return self._no_client()

        try:
            if not self._synced:
                self._sync_client_data()
            session = client.get_champ_select_session()
        except LCUConnectionError:
            self._synced = False
            return self._no_client()

        if not session:
            return {"status": "idle", "message": "Waiting for champ select..."}

        state = parse_session(session, self.engine.champ_roles, role_override)
        if state is None:
            return {"status": "idle", "message": "Waiting for your slot..."}

        eng = self.engine
        payload = {
            "status": "ok" if state.role else "no_role",
            "role": state.role,
            "role_source": state.role_source,
            "phase": state.phase.replace("_", " ").title(),
            "lane_opponent": eng.get_champion_name(state.lane_opponent) if state.lane_opponent else None,
            "lane_candidates": [eng.get_champion_name(c) for c in state.lane_candidates],
            "recommendations": [],
            "note": "",
        }
        if not state.role:
            payload["note"] = "Role not detected - pick one below"
            return payload

        recs = eng.get_recommendations(
            my_role=state.role,
            enemy_lane_opponent=state.lane_opponent,
            enemy_team=state.enemy_team,
            allied_picks=state.allied_picks,
            banned=state.banned,
            my_pick_intent=state.my_hover,
            top_n=max(self.top_n, CANDIDATES_SCANNED),
            available=self.owned,
        )

        if state.locked_champion:
            payload["locked"] = self._locked_payload(state, recs)
            if not eng.has_pool(state.role):
                payload["note"] = f"No pool set for {state.role}"
            return payload

        shown = recs[: self.top_n]
        payload["recommendations"] = [
            {"name": r.champion_name, "score": r.score, "reasons": r.reasons, "current": r.is_current,
             "has_data": r.has_data, "lane_missing": r.lane_missing}
            for r in shown
        ]
        # Said once for the whole list instead of once per row.
        if state.lane_opponent and shown and all(r.lane_missing for r in shown):
            opp = eng.get_champion_name(state.lane_opponent)
            payload["data_note"] = f"No matchup data vs {opp} - scores use synergy, comp and meta only"
        if not eng.has_pool(state.role):
            payload["note"] = f"No pool set for {state.role} - showing all champions"
        elif not recs:
            payload["note"] = "No pool champions available (banned / picked / not owned)"
        return payload

    def _locked_payload(self, state: ChampSelectState, recs) -> dict:
        """Read on the champion you already locked, plus an optional swap hint."""
        eng = self.engine
        rec = eng.score_pick(state.locked_champion, state.lane_opponent, state.enemy_team,
                             state.allied_picks, state.role)
        d = rec.details
        reads = [d.get("lane", ""), d.get("synergy", ""), d.get("comp", ""), d.get("counter", "")]
        if not state.lane_opponent:
            reads[0] = "· Lane opponent not known yet"
        swap = None
        alts = [r for r in recs if r.champion_id != state.locked_champion and r.has_data]
        if alts:
            best = alts[0]
            if best.score - rec.score >= SWAP_MARGIN:
                swap = f"{best.champion_name} ({best.score - rec.score:+.1f})"
        return {
            "name": rec.champion_name,
            "score": rec.score,
            "has_data": rec.has_data,
            "reads": [r for r in reads if r],
            "swap": swap,
        }