#!/usr/bin/env python3
"""Arval AutoSelect monitor: wysyla na Telegram nowe auta z oferty Arvala.

Wymaga zmiennych srodowiskowych: TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID.
Stan (widziane ID aut) trzymany w seen.json, filtry w config.json.
Tylko biblioteka standardowa Pythona.
"""
import json
import os
import sys
import time
import urllib.parse
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
SEEN_FILE = os.path.join(HERE, "seen.json")
CONFIG_FILE = os.path.join(HERE, "config.json")

API = "https://portalapi-prod.autoselect.cloud/api/Announcements/17"
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/128.0 Safari/537.36",
    "Accept": "application/json",
    "Origin": "https://autoselect.arval.pl",
    "Referer": "https://autoselect.arval.pl/",
}
MAX_SEEN = 5000


def http_json(url, data=None, headers=None, retries=3):
    body = None
    if data is not None:
        body = urllib.parse.urlencode(data).encode()
    last = None
    for attempt in range(retries):
        try:
            req = urllib.request.Request(url, data=body, headers=headers or {})
            with urllib.request.urlopen(req, timeout=30) as r:
                return json.loads(r.read().decode("utf-8"))
        except Exception as e:  # noqa: BLE001
            last = e
            time.sleep(3 * (attempt + 1))
    raise RuntimeError(f"Request failed: {url}: {last}")


def fetch_all(purchase_option):
    cars, page = [], 1
    while True:
        params = {
            "orderBy": "createdAt|desc",
            "pageNumber": page,
            "pageSize": 100,
            "priceType": "gross",
            "purchaseOption": purchase_option,
        }
        d = http_json(API + "?" + urllib.parse.urlencode(params, safe="|"), headers=HEADERS)
        block = d["announcements"]
        cars.extend(block.get("announcements") or [])
        if page >= (block.get("allPageQuantity") or 1) or page >= 20:
            return cars
        page += 1


def price_of(car):
    if car.get("purchaseOption") == "release":
        return car.get("reLeasePriceGross") or 0
    return car.get("salePriceGross") or 0


def matches(car, f):
    d = car.get("details") or {}
    makes = [m.lower() for m in f.get("makes") or []]
    if makes and (car.get("make") or "").lower() not in makes:
        return False
    fuels = [x.lower() for x in f.get("fuel") or []]
    if fuels and (d.get("fuelTypeLabel") or "").lower() not in fuels:
        return False
    bodies = [x.lower() for x in f.get("body_types") or []]
    if bodies and (car.get("bodyType") or "").lower() not in bodies:
        return False
    if f.get("gearbox") and (d.get("gearbox") or "").lower() != f["gearbox"].lower():
        return False
    if f.get("max_price") and price_of(car) > f["max_price"]:
        return False
    if f.get("max_mileage") and (d.get("mileage") or 0) > f["max_mileage"]:
        return False
    if f.get("min_year") and (d.get("registrationYear") or 0) < f["min_year"]:
        return False
    return True


def fmt_num(x):
    return f"{int(round(x)):,}".replace(",", " ")


def caption(car):
    d = car.get("details") or {}
    title = f"{car.get('make', '')} {car.get('model', '')}".strip()
    if car.get("purchaseOption") == "release":
        price = f"{fmt_num(price_of(car))} zł/mies. brutto (wynajem)"
    else:
        price = f"{fmt_num(price_of(car))} zł brutto"
    lines = [
        f"🚗 <b>{title}</b>",
        car.get("trim") or "",
        f"💰 {price}",
        f"📅 {d.get('registrationYear', '?')} · {fmt_num(d.get('mileage') or 0)} km",
        f"⛽ {d.get('fuelTypeLabel') or '?'} · {d.get('gearbox') or '?'} · {car.get('bodyType') or ''}",
        f"📍 {car.get('location') or '?'}",
        f'<a href="{car.get("offerUrl")}">Zobacz ofertę</a>',
    ]
    return "\n".join(l for l in lines if l)


def tg(method, data):
    token = os.environ["TELEGRAM_BOT_TOKEN"]
    return http_json(f"https://api.telegram.org/bot{token}/{method}", data=data)


def notify(car):
    chat = os.environ["TELEGRAM_CHAT_ID"]
    text = caption(car)
    if car.get("mainImage"):
        try:
            tg("sendPhoto", {"chat_id": chat, "photo": car["mainImage"],
                             "caption": text, "parse_mode": "HTML"})
            return
        except Exception as e:  # noqa: BLE001
            print(f"sendPhoto failed, fallback to text: {e}", file=sys.stderr)
    tg("sendMessage", {"chat_id": chat, "text": text, "parse_mode": "HTML"})


def load_json(path, default):
    try:
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)
    except FileNotFoundError:
        return default


def main():
    config = load_json(CONFIG_FILE, {})
    options = config.get("purchase_options") or ["release"]
    filters = config.get("filters") or {}

    seen_list = load_json(SEEN_FILE, None)
    first_run = seen_list is None
    seen = set(seen_list or [])

    cars = []
    for opt in options:
        cars.extend(fetch_all(opt))
    if not cars:
        print("API zwróciło 0 aut, pomijam (prawdopodobnie chwilowy błąd).")
        return

    new = [c for c in cars if c["id"] not in seen]
    # najstarsze najpierw, zeby w Telegramie najnowsze byly na dole
    new.sort(key=lambda c: c.get("createdAt") or "")
    to_send = [c for c in new if matches(c, filters)]

    if first_run:
        chat = os.environ["TELEGRAM_CHAT_ID"]
        tg("sendMessage", {"chat_id": chat, "text":
            f"✅ Monitor Arval działa. W ofercie jest teraz {len(cars)} aut "
            f"({len(to_send)} pasuje do filtrów). Od teraz dostaniesz powiadomienie o każdym nowym."})
        print(f"Pierwsze uruchomienie: zapamiętano {len(cars)} aut.")
    else:
        for c in to_send[:30]:  # bezpiecznik na wypadek masowego wrzutu
            notify(c)
            time.sleep(1.2)
        if len(to_send) > 30:
            tg("sendMessage", {"chat_id": os.environ["TELEGRAM_CHAT_ID"], "text":
                f"…i jeszcze {len(to_send) - 30} nowych aut. Zobacz: "
                "https://autoselect.arval.pl/uzywane-samochody/?priceType=gross&purchaseOption=release"})
        print(f"Nowych: {len(new)}, wysłano: {len(to_send)}.")

    ordered = [c["id"] for c in cars] + [i for i in (seen_list or []) if i not in {c["id"] for c in cars}]
    with open(SEEN_FILE, "w", encoding="utf-8") as fh:
        json.dump(ordered[:MAX_SEEN], fh)


if __name__ == "__main__":
    main()
