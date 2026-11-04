"""
Freerider watcher — checks Hertz Freerider and DriveBack for one-way trips
to Östersund and e-mails you when a new one appears.

Runs once per invocation (schedule it every 10 min with GitHub Actions,
cron, or Windows Task Scheduler). Remembers what it has already sent in
seen.json so you only get each trip once.
"""
from __future__ import annotations

import json
import math
import os
import smtplib
import sys
import unicodedata
from datetime import datetime
from email.message import EmailMessage
from pathlib import Path

import requests

# ---------------------------------------------------------------- config ---
# Origins: a trip matches if pickup is within radius_km of any point,
# OR the pickup name/city contains one of the keywords.
ORIGINS = {
    "Stockholm": {"lat": 59.3326, "lon": 18.0649, "radius_km": 35,
                  "keywords": ["stockholm", "bromma", "solna", "sundbyberg", "kista",
                               "nacka", "taby", "danderyd", "lidingo", "sollentuna",
                               "jarfalla", "huddinge", "haninge", "kungens kurva",
                               "skarholmen", "farsta", "spanga", "vallingby",
                               "hagersten", "alvsjo", "tyreso", "varmdo", "segeltorp",
                               "johanneshov", "akersberga", "upplands vasby"]},
    "Arlanda":   {"lat": 59.6498, "lon": 17.9238, "radius_km": 15,
                  "keywords": ["arlanda", "marsta"]},
    "Uppsala":   {"lat": 59.8586, "lon": 17.6389, "radius_km": 15,
                  "keywords": ["uppsala"]},
}
DESTINATION = {"name": "Östersund", "lat": 63.1792, "lon": 14.6357, "radius_km": 20,
               "keywords": ["ostersund", "froson", "are ostersund"]}

HERTZ_URL = "https://www.hertzfreerider.se/api/transport-routes/?country=SWEDEN"
HERTZ_SITE = "https://www.hertzfreerider.se/"
DRIVEBACK_URL = "https://www.driveback.se/resor"
DRIVEBACK_BOOK = "https://www.driveback.se/resor/{trip}/boka/{station}"

STATE_FILE = Path(__file__).with_name("seen.json")
HEADERS = {"User-Agent": "Mozilla/5.0 (personal trip alert)",
           "Accept": "application/json"}
TIMEOUT = 30


# --------------------------------------------------------------- helpers ---
def norm(s: str | None) -> str:
    s = unicodedata.normalize("NFKD", (s or "").lower())
    return "".join(c for c in s if not unicodedata.combining(c))


def km(lat1, lon1, lat2, lon2) -> float:
    r = 6371.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = p2 - p1, math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))


def place_matches(spec: dict, text: str, lat=None, lon=None) -> bool:
    t = norm(text)
    if any(k in t for k in spec["keywords"]):
        return True
    if lat is not None and lon is not None:
        return km(lat, lon, spec["lat"], spec["lon"]) <= spec["radius_km"]
    return False


def origin_label(text: str, lat=None, lon=None) -> str | None:
    # Check Arlanda/Uppsala first so they're labelled precisely
    for name in ("Arlanda", "Uppsala", "Stockholm"):
        if place_matches(ORIGINS[name], text, lat, lon):
            return name
    return None


def fmt(ts: str | None) -> str:
    if not ts:
        return "?"
    try:
        return datetime.fromisoformat(ts.replace("Z", "+00:00")).strftime("%a %d %b %H:%M")
    except ValueError:
        return ts


def get_json(url: str):
    r = requests.get(url, headers=HEADERS, timeout=TIMEOUT)
    r.raise_for_status()
    try:
        return r.json()
    except ValueError:
        # DriveBack may serve HTML without the JSON Accept header honoured
        r = requests.get(url.rstrip("/") + ".json", headers=HEADERS, timeout=TIMEOUT)
        r.raise_for_status()
        return r.json()


# --------------------------------------------------------------- sources ---
def hertz_matches(data) -> list[dict]:
    out = []
    for group in data or []:
        for route in group.get("routes", []):
            pu, rl = route.get("pickupLocation") or {}, route.get("returnLocation") or {}
            pu_text = f"{pu.get('name', '')} {pu.get('city', '')}"
            rl_text = f"{rl.get('name', '')} {rl.get('city', '')}"
            label = origin_label(pu_text, pu.get("geoLat"), pu.get("geoLon"))
            if not label:
                continue
            if not place_matches(DESTINATION, rl_text, rl.get("geoLat"), rl.get("geoLon")):
                continue
            out.append({
                "key": f"hertz:{route.get('id')}",
                "site": "Hertz Freerider",
                "origin": label,
                "from": pu.get("name"),
                "to": rl.get("name"),
                "pickup": fmt(route.get("availableAt")),
                "return_by": fmt(route.get("latestReturn")),
                "car": route.get("carModel"),
                "note": route.get("publicDescription") or "",
                "url": HERTZ_SITE,
            })
    return out


def driveback_matches(data) -> list[dict]:
    out = []
    for trip in data or []:
        fs = trip.get("from_station") or {}
        floc = fs.get("location") or {}
        f_text = f"{fs.get('area', '')} {floc.get('city', '')} {fs.get('address1', '')}"
        label = origin_label(f_text, floc.get("latitude"), floc.get("longitude"))
        if not label:
            continue
        for ts in trip.get("to_stations") or []:
            tloc = ts.get("location") or {}
            t_text = f"{ts.get('area', '')} {tloc.get('city', '')} {ts.get('address1', '')}"
            if not place_matches(DESTINATION, t_text, tloc.get("latitude"), tloc.get("longitude")):
                continue
            veh = trip.get("vehicle") or {}
            out.append({
                "key": f"driveback:{trip.get('id')}:{ts.get('id')}",
                "site": "DriveBack",
                "origin": label,
                "from": f"{fs.get('area')} ({floc.get('city')})",
                "to": f"{ts.get('area')} ({tloc.get('city')})",
                "pickup": fmt(trip.get("first_pickup")),
                "return_by": fmt(trip.get("last_deliver")),
                "car": f"{veh.get('car_model', '')} {veh.get('car_size', '')}".strip(),
                "note": f"min age {trip.get('min_age')}, deductible {trip.get('deductible')} kr"
                        + (", extra costs" if trip.get("extra_costs") else ""),
                "url": DRIVEBACK_BOOK.format(trip=trip.get("id"), station=ts.get("id")),
            })
    return out


# ----------------------------------------------------------------- email ---
def send_email(trips: list[dict]) -> None:
    host = os.environ.get("SMTP_HOST", "smtp.gmail.com")
    port = int(os.environ.get("SMTP_PORT", "587"))
    user, pwd = os.environ["SMTP_USER"], os.environ["SMTP_PASS"]
    to = os.environ.get("MAIL_TO", user)

    lines, html = [], []
    for t in trips:
        lines.append(f"[{t['site']}] {t['from']} → {t['to']}\n"
                     f"  Pickup from {t['pickup']}, return by {t['return_by']}\n"
                     f"  {t['car']}  {t['note']}\n  {t['url']}\n")
        html.append(f"<p><b>{t['site']}</b>: {t['from']} → <b>{t['to']}</b><br>"
                    f"Pickup from {t['pickup']} · return by {t['return_by']}<br>"
                    f"{t['car']} <i>{t['note']}</i><br>"
                    f"<a href=\"{t['url']}\">Book now →</a></p>")

    first = trips[0]
    subject = (f"🚗 Free car {first['origin']} → Östersund ({first['site']})"
               if len(trips) == 1 else f"🚗 {len(trips)} free cars to Östersund")

    msg = EmailMessage()
    msg["Subject"], msg["From"], msg["To"] = subject, user, to
    msg.set_content("\n".join(lines))
    msg.add_alternative("".join(html), subtype="html")

    with smtplib.SMTP(host, port, timeout=TIMEOUT) as s:
        s.starttls()
        s.login(user, pwd)
        s.send_message(msg)


# ------------------------------------------------------------------ main ---
def main() -> int:
    seen = set(json.loads(STATE_FILE.read_text())) if STATE_FILE.exists() else set()
    found, errors = [], []

    for name, url, parser in (("Hertz", HERTZ_URL, hertz_matches),
                              ("DriveBack", DRIVEBACK_URL, driveback_matches)):
        try:
            matches = parser(get_json(url))
            print(f"{name}: {len(matches)} matching trip(s)")
            found += matches
        except Exception as e:  # one site failing shouldn't block the other
            errors.append(f"{name}: {e}")
            print(f"{name} FAILED: {e}", file=sys.stderr)

    new = [t for t in found if t["key"] not in seen]
    if new:
        if os.environ.get("DRY_RUN"):
            print(json.dumps(new, indent=2, ensure_ascii=False))
        else:
            send_email(new)
            print(f"E-mailed {len(new)} new trip(s)")
        seen |= {t["key"] for t in new}
        STATE_FILE.write_text(json.dumps(sorted(seen), indent=1))
    else:
        print("Nothing new")

    return 1 if len(errors) == 2 else 0  # fail the run only if both sites are down


if __name__ == "__main__":
    sys.exit(main())
