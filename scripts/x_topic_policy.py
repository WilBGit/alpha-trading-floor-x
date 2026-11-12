#!/usr/bin/env python3
"""Choose an auditable, balanced X topic lane without publishing anything."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
from collections import Counter
from datetime import date
from pathlib import Path

STATE_PATH = Path.home() / ".hermes/state/alpha_trading_floor/poster_state.json"
LANES = ("SPY", "QQQ", "FINVIZ_STOCK")


def infer_lane(text: str) -> str:
    if re.search(r"\bQQQ\b", text, re.I):
        return "QQQ"
    if re.search(r"\bSPY\b", text, re.I):
        return "SPY"
    return "FINVIZ_STOCK"


def choose_lane(posts: list[dict], selection_date: str) -> dict:
    recent = posts[-9:]
    recent_lanes = [p.get("lane") or infer_lane(p.get("text", "")) for p in recent]
    last_lane = recent_lanes[-1] if recent_lanes else None

    eligible = [lane for lane in LANES if lane != last_lane] or list(LANES)
    counts = Counter(recent_lanes)
    least_used = min(counts[lane] for lane in eligible)
    balanced = sorted(lane for lane in eligible if counts[lane] == least_used)

    seed_material = selection_date + "|" + "|".join(recent_lanes)
    seed = int(hashlib.sha256(seed_material.encode()).hexdigest(), 16)
    selected = balanced[seed % len(balanced)]
    return {
        "lane": selected,
        "date": selection_date,
        "last_lane": last_lane,
        "recent_counts": {lane: counts[lane] for lane in LANES},
        "selection_pool": balanced,
        "policy": "balanced rotation; no consecutive lane; date-seeded tie break",
    }


def load_posts(path: Path = STATE_PATH) -> list[dict]:
    if not path.exists():
        return []
    data = json.loads(path.read_text())
    return data.get("posts", [])


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=["choose"])
    parser.add_argument("--date", default=date.today().isoformat())
    parser.add_argument("--state", type=Path, default=STATE_PATH)
    args = parser.parse_args()
    print(json.dumps(choose_lane(load_posts(args.state), args.date), indent=2))


if __name__ == "__main__":
    main()
