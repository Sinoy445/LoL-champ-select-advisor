"""
Terminal UI using Textual for live champ select display.
"""
from textual.app import App, ComposeResult
from textual.widgets import Header, Footer, Static, DataTable, RichLog
from textual.containers import Container, Horizontal, Vertical
from textual.timer import Timer
from textual import on
from rich.text import Text
from rich.table import Table
from rich.panel import Panel
from rich.align import Align
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).parent.parent))
from lcu import lcu
from engine import create_engine, PickRecommendation


class ChampSelectApp(App):
    CSS = """
    Screen { background: #0d0d0d; color: #e0e0e0; }
    Header { background: #1a1a2e; color: #00d4ff; text-style: bold; }
    Footer { background: #1a1a2e; color: #888; }
    #main { layout: horizontal; padding: 1; }
    #left { width: 45%; border: solid #333; padding: 1; }
    #right { width: 55%; border: solid #333; padding: 1; }
    #enemy { height: 50%; border: solid #ff4444; padding: 1; margin-bottom: 1; }
    #ally { height: 50%; border: solid #44ff44; padding: 1; }
    #recs { height: 100%; border: solid #00d4ff; padding: 1; }
    .team-title { text-style: bold; color: #00d4ff; margin-bottom: 1; }
    .champ-row { height: 1; }
    .champ-pick { color: #ffd700; }
    .champ-ban { color: #ff4444; text-style: strike; }
    .champ-empty { color: #666; }
    .phase-ban { color: #ff4444; text-style: bold; }
    .phase-pick { color: #44ff44; text-style: bold; }
    .phase-planning { color: #ffaa00; text-style: bold; }
    .rec-row { height: 1; }
    .rec-high { color: #44ff44; }
    .rec-med { color: #ffaa00; }
    .rec-low { color: #ff4444; }
    """

    BINDINGS = [
        ("q", "quit", "Quit"),
        ("r", "refresh", "Refresh"),
        ("t", "test", "Test Data"),
        ("d", "debug", "Debug Info"),
    ]

    def __init__(self):
        super().__init__()
        self.engine = create_engine()
        self.session = None
        self.timer: Timer = None
        self.my_cell_id = None

    def compose(self) -> ComposeResult:
        yield Header(name="🎮 LoL Champ Select Advisor", show_clock=True)
        with Container(id="main"):
            with Vertical(id="left"):
                with Container(id="enemy"):
                    yield Static("", id="enemy-title", classes="team-title")
                    yield Static("", id="enemy-team")
                with Container(id="ally"):
                    yield Static("", id="ally-title", classes="team-title")
                    yield Static("", id="ally-team")
            with Container(id="right"):
                yield Static("", id="recs-title", classes="team-title")
                yield RichLog(id="recs", highlight=True, markup=True)
        yield Footer()

    def on_mount(self):
        self.timer = self.set_interval(2.0, self.refresh_data)
        self.refresh_data()

    def refresh_data(self):
        if not lcu.creds:
            if not lcu.connect():
                self.notify("League client not found", severity="error")
                return

        session = lcu.get_champ_select_session()
        if session:
            self.session = session
            self.my_cell_id = session.get("localPlayerCellId")
            self.update_ui()

    def update_ui(self):
        if not self.session:
            return

        s = self.session
        phase = s.get("timer", {}).get("phase", "UNKNOWN")
        time_left = s.get("timer", {}).get("adjustedTimeLeftInPhase", 0) / 1000

        # Update titles
        self.query_one("#enemy-title").update(f"🔴 ENEMY TEAM  |  Phase: {phase}  |  {time_left:.0f}s")
        self.query_one("#ally-title").update(f"🟢 YOUR TEAM")
        self.query_one("#recs-title").update(f"💡 RECOMMENDATIONS (Your pick)")

        # Build team displays
        enemy_html = self._render_team(s.get("theirTeam", []), is_enemy=True)
        ally_html = self._render_team(s.get("myTeam", []), is_enemy=False)
        self.query_one("#enemy-team").update(enemy_html)
        self.query_one("#ally-team").update(ally_html)

        # Recommendations
        self._render_recommendations()

    def _render_team(self, team: list, is_enemy: bool) -> Text:
        lines = []
        for p in team:
            cid = p.get("championId", 0)
            name = p.get("gameName", "Unknown")
            cell = p.get("cellId", -1)
            is_me = cell == self.my_cell_id

            if cid == 0:
                champ = "[dim]?[/dim]"
                status = "⏳ Picking..." if p.get("championPickIntent") == 0 else "🤔 Deciding"
            else:
                champ = self.engine.get_champion_name(cid)
                if p.get("championPickIntent") == cid:
                    status = "🔒 LOCKED"
                else:
                    status = "✅ Picked"

            prefix = "► " if is_me else "  "
            color = "red" if is_enemy else "green"
            lines.append(f"{prefix}[{color}]{champ}[/{color}]  [dim]{name}[/dim]  [{'yellow' if 'LOCKED' in status else 'cyan'}]{status}[/]")

        return Text.from_markup("\n".join(lines)) if lines else Text("[dim]Empty[/dim]")

    def _render_recommendations(self):
        if not self.session:
            return

        log = self.query_one("#recs", RichLog)
        log.clear()

        # Determine my role and pick state
        my_info = None
        for p in self.session.get("myTeam", []):
            if p.get("cellId") == self.my_cell_id:
                my_info = p
                break

        if not my_info:
            log.write("[dim]Waiting for your slot...[/dim]")
            return

        # 1. ROLE: prefer assignedPosition (works in ranked/draft), fallback to pickTurn mapping
        my_role = my_info.get("assignedPosition", "").upper()
        if not my_role:
            actions = self.session.get("actions", [])
            for action_group in actions:
                for action in action_group:
                    if action.get("actorCellId") == self.my_cell_id and action.get("type") == "pick":
                        pick_turn = action.get("pickTurn", 0)
                        roles = ["TOP", "JUNGLE", "MID", "ADC", "SUPPORT"]
                        my_role = roles[pick_turn] if pick_turn < 5 else "MID"
                        break

        # 2. ENEMY LANE OPPONENT: find enemy player in SAME role (via theirTeam assignedPosition)
        #    Fallback: completed enemy pick with same pickTurn
        enemy_lane = None
        their_team = self.session.get("theirTeam", [])

        # Method A: Match by assignedPosition (best for real games)
        for enemy in their_team:
            if enemy.get("assignedPosition", "").upper() == my_role:
                cid = enemy.get("championId", 0)
                if cid > 0:
                    enemy_lane = cid
                    break

        # Method B: Fallback to pickTurn matching (only completed picks, not bans)
        if not enemy_lane:
            actions = self.session.get("actions", [])
            my_pick_turn = None
            for action_group in actions:
                for action in action_group:
                    if action.get("actorCellId") == self.my_cell_id and action.get("type") == "pick":
                        my_pick_turn = action.get("pickTurn", 0)
                        break

            if my_pick_turn is not None:
                for action_group in actions:
                    for action in action_group:
                        if (action.get("type") == "pick"
                            and not action.get("isAllyAction", True)
                            and action.get("pickTurn") == my_pick_turn
                            and action.get("completed", False)
                            and action.get("championId", 0) > 0):
                            enemy_lane = action["championId"]
                            break

        # 3. GATHER DATA
        enemy_team = [p.get("championId", 0) for p in their_team if p.get("championId", 0) > 0]
        allied_picks = [p.get("championId", 0) for p in self.session.get("myTeam", [])
                       if p.get("championId", 0) > 0 and p.get("cellId") != self.my_cell_id]
        bans = self.session.get("bans", {})
        banned = bans.get("myTeamBans", []) + bans.get("theirTeamBans", [])
        pick_intent = my_info.get("championPickIntent", 0)

        # DEBUG: show what we're using
        pool_info = ""
        if my_role in self.engine.pools and self.engine.pools[my_role]:
            pool_info = f", pool={len(self.engine.pools[my_role])} champs"
        log.write(f"[dim]DEBUG: role={my_role}, enemy_lane={enemy_lane}, enemy_team={enemy_team}, allied={allied_picks}, banned={banned}{pool_info}[/dim]")

        # 4. RECOMMENDATIONS
        recs = self.engine.get_recommendations(
            my_role=my_role or "MID",
            enemy_lane_opponent=enemy_lane,
            enemy_team=enemy_team,
            allied_picks=allied_picks,
            banned=banned,
            my_pick_intent=pick_intent,
            top_n=5
        )

        # Render
        log.write(f"[bold cyan]Your Role: {my_role or 'MID'}[/bold cyan]")
        if enemy_lane:
            label = "Jungle Opponent" if my_role == "JUNGLE" else "Lane Opponent"
            log.write(f"[bold red]{label}: {self.engine.get_champion_name(enemy_lane)}[/bold red]")
        log.write("")

        if recs:
            for i, rec in enumerate(recs, 1):
                score_color = "green" if rec.score >= 55 else "yellow" if rec.score >= 50 else "red"
                line = f"[bold]{i}.[/bold] [{score_color}]{rec.champion_name}[/{score_color}]  [bold]{rec.score:.1f}%[/bold]"
                if rec.reasons:
                    line += f"  [dim]({' • '.join(rec.reasons)})[/dim]"
                log.write(line)
        else:
            log.write("[dim]No recommendations — check DEBUG above[/dim]")
            log.write("[dim]Try pressing 't' for test mode[/dim]")

    def action_test(self):
        """Show mock recommendations for testing."""
        log = self.query_one("#recs", RichLog)
        log.clear()
        log.write("[bold cyan]TEST MODE - Mock Recommendations[/bold cyan]")
        log.write("[bold red]Lane Opponent: Yasuo[/bold red]")
        log.write("")
        mock = [
            ("Syndra", 54.2, ["✓ Counters Yasuo (54.2%)", "✓ Bursts squishies"]),
            ("Zed", 53.8, ["✓ Counters Yasuo (53.1%)", "✓ Roams well"]),
            ("Orianna", 52.1, ["✓ Synergy w/ Malphite (8.5%)", "✓ Safe scaling"]),
            ("Akali", 51.5, ["✓ Counters Yasuo (50.5%)", "✓ High outplay"]),
            ("Ahri", 50.8, ["✓ Safe pick", "✓ Charm setup"]),
        ]
        for i, (name, score, reasons) in enumerate(mock, 1):
            color = "green" if score >= 55 else "yellow" if score >= 50 else "red"
            log.write(f"[bold]{i}.[/bold] [{color}]{name}[/{color}]  [bold]{score:.1f}%[/bold]  [dim]({' • '.join(reasons)})[/dim]")

    def action_debug(self):
        """Print debug info to log."""
        log = self.query_one("#recs", RichLog)
        log.clear()
        if not self.session:
            log.write("[dim]No session[/dim]")
            return
        s = self.session
        log.write(f"[bold]Phase:[/bold] {s.get('timer', {}).get('phase')}")
        log.write(f"[bold]My Cell ID:[/bold] {self.my_cell_id}")
        log.write(f"[bold]My Team:[/bold] {[p.get('championId') for p in s.get('myTeam', [])]}")
        log.write(f"[bold]Enemy Team:[/bold] {[p.get('championId') for p in s.get('theirTeam', [])]}")
        log.write(f"[bold]Actions:[/bold] {s.get('actions', [])}")
        log.write(f"[bold]Bans:[/bold] {s.get('bans', {})}")
        my_info = next((p for p in s.get('myTeam', []) if p.get('cellId') == self.my_cell_id), None)
        if my_info:
            log.write(f"[bold]My Info:[/bold] role={my_info.get('assignedPosition')}, intent={my_info.get('championPickIntent')}")


def run_cli():
    app = ChampSelectApp()
    app.run()


if __name__ == "__main__":
    run_cli()