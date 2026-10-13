"""
Import champion names/ids and base comp traits from Riot's Data Dragon (official, no API key).

    python tools/import_ddragon.py                  # latest patch
    python tools/import_ddragon.py --version 26.15.1
    python tools/import_ddragon.py --from-file champion.json   # offline / testing

Writes into data/:
  champions.json        id -> display name (+ aliases, e.g. Wukong's internal id MonkeyKing).
                        Your existing "roles" table is kept untouched: Data Dragon has no lane data.
  champion_traits.json  per champion: ad_share, damage_weight, frontline (derived below)

data/traits_overrides.json is never written; it's applied on top at load time. Existing files are
copied to *.bak first.

How the traits are derived (rough, from Data Dragon's 0-10 ratings and tags):
  ad_share       attack^2 / (attack^2 + magic^2)
  damage_weight  highest of: Marksman/Mage/Assassin 1.0, Fighter 0.8, Tank 0.5, Support 0.4
  frontline      Tank-first 0.9, Tank-second 0.6, Fighter 0.45, else 0; +0.05 per defense point above 6
These are coarse (Data Dragon documents its own champion data as imperfect), which is why
overrides exist for the cases where the ratings mislead (enchanters, hybrids, off-meta builds).
"""
import argparse
import json
import shutil
import sys
from pathlib import Path

BASE = "https://ddragon.leagueoflegends.com"
TAG_DAMAGE_WEIGHT = {"Marksman": 1.0, "Mage": 1.0, "Assassin": 1.0, "Fighter": 0.8, "Tank": 0.5, "Support": 0.4}
MIN_CHAMPIONS = 100  # sanity check: refuse to overwrite your data with a truncated download


def fetch_json(url: str):
    import requests  # imported here so --from-file works without it
    r = requests.get(url, timeout=20)
    r.raise_for_status()
    return r.json()


def derive_traits(info: dict, tags: list) -> dict:
    attack = float(info.get("attack", 5))
    magic = float(info.get("magic", 5))
    defense = float(info.get("defense", 5))
    denom = attack ** 2 + magic ** 2
    ad_share = attack ** 2 / denom if denom else 0.5

    weight = max((TAG_DAMAGE_WEIGHT.get(t, 0.6) for t in tags), default=0.6)

    if "Tank" in tags:
        front = 0.9 if tags[0] == "Tank" else 0.6
    elif "Fighter" in tags:
        front = 0.45
    else:
        front = 0.0
    if front:
        front = min(1.0, front + max(0.0, defense - 6) * 0.05)

    return {"ad_share": round(ad_share, 2), "damage_weight": round(weight, 2), "frontline": round(front, 2)}


def parse_champions(raw: dict):
    """champion.json -> (names, aliases, traits), all keyed by the numeric champion id as a string."""
    names, aliases, traits = {}, {}, {}
    for entry in raw["data"].values():
        key = str(entry["key"])
        names[key] = entry["name"]
        aliases[key] = entry["id"]  # internal id, e.g. "MonkeyKing"
        traits[key] = {"name": entry["name"], "tags": entry.get("tags", []),
                       **derive_traits(entry.get("info", {}), entry.get("tags", []))}
    return names, aliases, traits


def write_json(path: Path, obj) -> None:
    if path.exists():
        shutil.copy2(path, path.with_suffix(path.suffix + ".bak"))
    with open(path, "w", encoding="utf-8") as f:
        json.dump(obj, f, indent=2, ensure_ascii=False)
        f.write("\n")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--data-dir", type=Path, default=Path(__file__).resolve().parent.parent / "data")
    ap.add_argument("--version", help="patch to import (default: latest)")
    ap.add_argument("--lang", default="en_US")
    ap.add_argument("--from-file", type=Path, help="read a local champion.json instead of downloading")
    args = ap.parse_args(argv)

    if args.from_file:
        raw = json.loads(args.from_file.read_text(encoding="utf-8"))
        version = raw.get("version", "local")
    else:
        version = args.version or fetch_json(f"{BASE}/api/versions.json")[0]
        raw = fetch_json(f"{BASE}/cdn/{version}/data/{args.lang}/champion.json")

    names, aliases, traits = parse_champions(raw)
    if len(names) < MIN_CHAMPIONS:
        print(f"Only {len(names)} champions in the download - refusing to overwrite your data.", file=sys.stderr)
        return 1

    args.data_dir.mkdir(parents=True, exist_ok=True)
    champs_path = args.data_dir / "champions.json"
    old = json.loads(champs_path.read_text(encoding="utf-8")) if champs_path.exists() else {}
    roles = old.get("roles", {})
    old_ids = set(old.get("champions", {}))

    write_json(champs_path, {"version": version, "champions": names, "aliases": aliases, "roles": roles})
    write_json(args.data_dir / "champion_traits.json", {
        "version": version,
        "description": "Derived from Data Dragon ratings/tags by tools/import_ddragon.py. "
                       "Put corrections in traits_overrides.json, not here (this file is regenerated).",
        "traits": traits,
    })

    added = sorted(set(names) - old_ids, key=int)
    print(f"Patch {version}: {len(names)} champions imported "
          f"({len(added)} not in your old champions.json).")
    if added:
        print("  new/changed ids:", ", ".join(f"{names[i]} ({i})" for i in added[:15]) + (" ..." if len(added) > 15 else ""))
    if not roles:
        print("Note: no 'roles' table found - lane-opponent guessing needs one (see README).")
    else:
        unknown = [i for ids in roles.values() for i in ids if str(i) not in names]
        if unknown:
            bad = sorted(set(unknown))
            print(f"Note: roles table references {len(bad)} id(s) that don't exist in this patch: "
                  f"{bad[:15]}{' ...' if len(bad) > 15 else ''}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
