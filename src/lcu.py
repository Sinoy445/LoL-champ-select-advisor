"""
LCU (League Client Update) API client.
Polls the local League client's REST API for champ select state.

Changes vs the old version:
  * get() raises LCUConnectionError when the client is unreachable (returns None only for
    "no data", e.g. 404 when you're not in champ select) and forgets stale credentials,
    so a client restart is picked up automatically.
  * TLS warnings are silenced (they printed on every request) and one Session is reused.
  * The unused WebSocket code is gone: asyncio.create_task() needs a running event loop
    (tkinter has none), and newer `websockets` releases renamed `extra_headers`.
    Polling every ~1.5s is plenty for champ select.
"""
import base64
import re
from dataclasses import dataclass
from typing import Any, Dict, Optional, Set

import psutil
import requests
import urllib3

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)


class LCUConnectionError(Exception):
    """The League client could not be reached."""


@dataclass
class LCUCredentials:
    port: int
    token: str
    base_url: str
    auth_header: str


class LCUClient:
    PROCESS_NAMES = {"LeagueClientUx.exe", "LeagueClientUx"}  # Windows / macOS

    def __init__(self):
        self.creds: Optional[LCUCredentials] = None
        self.last_error = "League client not found"
        self._http = requests.Session()
        self._http.verify = False  # the client uses a self-signed certificate

    def find_credentials(self) -> Optional[LCUCredentials]:
        """Extract LCU port and auth token from the LeagueClientUx process."""
        denied = False
        for proc in psutil.process_iter(["pid", "name"]):
            try:
                if proc.info["name"] not in self.PROCESS_NAMES:
                    continue
                cmdline = " ".join(proc.cmdline() or [])
            except psutil.AccessDenied:
                denied = True
                continue
            except psutil.NoSuchProcess:
                continue
            port_match = re.search(r"--app-port=(\d+)", cmdline)
            token_match = re.search(r"--remoting-auth-token=([^\s\"]+)", cmdline)
            if port_match and token_match:
                port, token = int(port_match.group(1)), token_match.group(1)
                auth = base64.b64encode(f"riot:{token}".encode()).decode()
                return LCUCredentials(port, token, f"https://127.0.0.1:{port}", f"Basic {auth}")
        self.last_error = (
            "Client found but unreadable - run the overlay as administrator"
            if denied else "League client not found"
        )
        return None

    def connect(self) -> bool:
        self.creds = self.find_credentials()
        return self.creds is not None

    def get(self, endpoint: str) -> Optional[Any]:
        """GET an LCU endpoint. Returns parsed JSON on 200, None on any other status.
        Raises LCUConnectionError if the client can't be reached (credentials are dropped)."""
        if not self.creds:
            raise LCUConnectionError(self.last_error)
        try:
            r = self._http.get(
                f"{self.creds.base_url}{endpoint}",
                headers={"Authorization": self.creds.auth_header, "Accept": "application/json"},
                timeout=3,
            )
        except requests.RequestException as exc:
            self.creds = None
            self.last_error = "League client not found"
            raise LCUConnectionError(str(exc)) from exc
        if r.status_code != 200:
            return None
        try:
            return r.json()
        except ValueError:
            return None

    # ------------------------------------------------------------ endpoints
    def get_champ_select_session(self) -> Optional[Dict]:
        return self.get("/lol-champ-select/v1/session")

    def get_current_summoner(self) -> Optional[Dict]:
        return self.get("/lol-summoner/v1/current-summoner")

    def get_champion_summary(self) -> Optional[list]:
        """[{id, name, alias, ...}] for every champion in the live game data."""
        return self.get("/lol-game-data/assets/v1/champion-summary.json")

    def get_owned_champion_ids(self) -> Optional[Set[int]]:
        """Ids of champions you own, have on rental, or that are free this week."""
        data = self.get("/lol-champions/v1/owned-champions-minimal")
        if not isinstance(data, list):
            return None
        ids: Set[int] = set()
        for champ in data:
            own = champ.get("ownership") or {}
            rented = (own.get("rental") or {}).get("rented")
            if own.get("owned") or rented or champ.get("freeToPlay"):
                try:
                    ids.add(int(champ["id"]))
                except (KeyError, TypeError, ValueError):
                    pass
        return ids


# Global instance
lcu = LCUClient()