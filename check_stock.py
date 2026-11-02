#!/usr/bin/env python3
"""
iPhone Duo (Night Sky, 256GB + 512GB) pickup tracker for Apple Stores near a ZIP.

- Checks Apple's pickup availability for each nearby store.
- Sends a phone alert (via ntfy.sh) when a store goes from unavailable -> available.
- Keeps a tracker: tracker.csv (current status) and history.csv (every change).
- Fails loudly: if Apple's response changes, you get ONE "tracker broken" alert.

Uses only the Python standard library.
"""
import csv
import json
import os
import sys
import urllib.parse
import urllib.request
from datetime import datetime, timezone

ZIP = os.environ.get("TRACK_ZIP", "92127")
NTFY_TOPIC = os.environ.get("NTFY_TOPIC", "")
MAX_STORES = int(os.environ.get("MAX_STORES", "10"))

# Part numbers for the unlocked Night Sky models (from Apple's buy pages)
PARTS = {
    "MK1F4LL/A": "iPhone Duo Night Sky 256GB",
    "MK1H4LL/A": "iPhone Duo Night Sky 512GB",
}
BUY_LINK = "https://www.apple.com/shop/buy-iphone/iphone-duo"

STATE_FILE = "state.json"
TRACKER_FILE = "tracker.csv"
HISTORY_FILE = "history.csv"


def now():
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")


def fetch():
    params = [("pl", "true"), ("mts.0", "regular"), ("location", ZIP)]
    for i, part in enumerate(PARTS):
        params.append((f"parts.{i}", part))
    url = "https://www.apple.com/shop/fulfillment-messages?" + urllib.parse.urlencode(params)
    req = urllib.request.Request(
        url,
        headers={
            "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 "
            "(KHTML, like Gecko) Version/17.0 Safari/605.1.15",
            "Accept": "application/json",
        },
    )
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.load(resp)


def parse(data):
    try:
        stores = data["body"]["content"]["pickupMessage"]["stores"]
    except (KeyError, TypeError):
        raise ValueError("Unexpected response shape - Apple may have changed or blocked the endpoint")
    if not stores:
        raise ValueError(f"No stores returned for ZIP {ZIP}")
    out = {}
    for s in stores[:MAX_STORES]:
        for part, label in PARTS.items():
            info = (s.get("partsAvailability") or {}).get(part)
            if not info:
                continue
            key = f"{s.get('storeName', '?')}|{part}"
            out[key] = {
                "store": s.get("storeName", "?"),
                "item": label,
                "available": info.get("pickupDisplay") == "available",
                "detail": info.get("storePickupQuote") or info.get("pickupSearchQuote") or "",
            }
    if not out:
        raise ValueError("Stores returned, but none listed these part numbers")
    return out


def notify(title, message, priority="default", tags="", click=None):
    if not NTFY_TOPIC:
        print(f"[NTFY_TOPIC not set] {title}: {message}")
        return
    headers = {"Title": title, "Priority": priority, "Tags": tags}
    if click:
        headers["Click"] = click
    req = urllib.request.Request(
        f"https://ntfy.sh/{NTFY_TOPIC}",
        data=message.encode("utf-8"),
        headers=headers,
        method="POST",
    )
    urllib.request.urlopen(req, timeout=20)


def load_state():
    try:
        with open(STATE_FILE) as f:
            return json.load(f)
    except Exception:
        return {"stores": {}, "broken": False}


def save_state(state):
    with open(STATE_FILE, "w") as f:
        json.dump(state, f, indent=2, sort_keys=True)


def write_tracker(current):
    with open(TRACKER_FILE, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["Store", "Item", "Status", "Since", "Detail"])
        for v in sorted(current.values(), key=lambda x: (x["store"], x["item"])):
            w.writerow([v["store"], v["item"], "AVAILABLE" if v["available"] else "Unavailable", v["since"], v["detail"]])


def append_history(rows):
    new = not os.path.exists(HISTORY_FILE)
    with open(HISTORY_FILE, "a", newline="") as f:
        w = csv.writer(f)
        if new:
            w.writerow(["Time", "Store", "Item", "Status"])
        w.writerows(rows)


def main():
    state = load_state()

    try:
        current = parse(fetch())
    except Exception as e:  # fail loudly, but only alert once per outage
        print(f"ERROR: {e}", file=sys.stderr)
        if not state.get("broken"):
            try:
                notify("Duo tracker is broken", str(e), priority="high", tags="warning")
            except Exception as ne:
                print(f"Could not send alert: {ne}", file=sys.stderr)
            state["broken"] = True
            save_state(state)
        sys.exit(1)

    prev = state.get("stores", {})
    history_rows = []

    for key, v in current.items():
        was = prev.get(key, {}).get("available")  # None the first time we see it
        if v["available"] != was:
            v["since"] = now()
            history_rows.append([v["since"], v["store"], v["item"], "AVAILABLE" if v["available"] else "Unavailable"])
            if v["available"]:
                notify(
                    f"{v['item']} IN STOCK",
                    f"Pickup available at {v['store']}. {v['detail']}".strip(),
                    priority="urgent",
                    tags="iphone,rotating_light",
                    click=BUY_LINK,
                )
        else:
            v["since"] = prev[key].get("since", now())

    if state.get("broken"):
        notify("Duo tracker recovered", "Checks are working again.", tags="white_check_mark")

    state["stores"] = current
    state["broken"] = False
    save_state(state)
    write_tracker(current)
    if history_rows or not os.path.exists(HISTORY_FILE):
        append_history(history_rows)

    avail = [v for v in current.values() if v["available"]]
    print(f"{now()}: checked {len(current)} store/item pairs, {len(avail)} available")


if __name__ == "__main__":
    main()
