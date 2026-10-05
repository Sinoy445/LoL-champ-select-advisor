# LoL Champ Select Advisor

## Features
- Real-time champ select recommendations via LCU API
- Considers matchup winrates, team synergy, and counter-picking
- **Champion pool filtering** - only recommends champions you actually play
- Terminal UI (future: Tauri overlay)

## Champion Pools

The advisor now supports **role-based champion pools** - it will only recommend champions from your defined pool for each role.

To update your champion pools:
1. Edit `data/champion_pools.json`
2. Each role (TOP, JUNGLE, MID, ADC, SUPPORT) has a list of champion names
3. Only champions in your pool will be recommended for that role
4. Empty list = all champions allowed (default behavior)

### Example (your pools):
```json
{
  "pools": {
    "JUNGLE": ["Sylas", "Wukong", "Shen", "Viego", "MasterYi"],
    "SUPPORT": ["Braum", "Thresh", "Blitzcrank", "Rakan"],
    "TOP": [],
    "MID": [],
    "ADC": []
  }
}
```

**Champion names must match exactly** (case-insensitive matching is supported):
- Use the official champion name (e.g., "MasterYi" not "Master Yi" or "Yi")
- See `data/champions.json` for all valid names

## Usage
```bash
cd C:\Users\Asus\Desktop\lol-advisor
python main.py
```

**Keys:**
- `q` = quit
- `r` = refresh (manual)
- `t` = test mode (show mock data)
- `d` = debug (shows session data + pool info)

## Data Sources
- Champion data: Riot Games API (via LCU)
- Matchup/synergy/counter data: Static JSON files (update weekly from U.GG/LoLalytics)
- Champion pools: `data/champion_pools.json` (user-configurable)

---
*Built with ❤️ for better champ selects*