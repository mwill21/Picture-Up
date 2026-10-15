#!/usr/bin/env python3
"""Fetch public-domain / CC0 photos from Wikimedia Commons for Picture Up!

For every card listed in GROUPS below, this searches Commons, keeps only files whose
license metadata says public domain or CC0 (no attribution required, no restrictions),
checks that the title/description actually names the subject, then saves up to
--candidates square 400x400 JPEGs per card plus a JSON record of where each came from.
It also writes contact sheets (tools/out/sheets/*.jpg) so the picks can be reviewed by eye.

Usage (from the repo root):
    python -m venv .venv && source .venv/bin/activate      # Windows: .venv\\Scripts\\activate
    pip install requests pillow
    python tools/fetch_images.py                    # all groups
    python tools/fetch_images.py birds space        # only some groups
    python tools/fetch_images.py birds:Owl          # only one card (to re-pick it)

Re-running resumes: cards already in tools/out/<group>.json are skipped.
Wikimedia rate-limits heavy use, so requests are spaced out and Retry-After is honored.
"""
import argparse
import io
import json
import os
import re
import sys
import time

import requests
from PIL import Image, ImageDraw

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, "out")
IMG = os.path.join(OUT, "img")
SHEETS = os.path.join(OUT, "sheets")
API = "https://commons.wikimedia.org/w/api.php"
SIZE = 400

S = requests.Session()
S.headers["User-Agent"] = "PictureUpImageFetcher/1.0 (https://github.com/mwill21/Picture-Up) python-requests"

GROUPS = {
    "birds": ("Birds", "🐦", [
        ("Bald Eagle", "bald eagle"), ("Owl", "great horned owl"), ("Flamingo", "flamingo"), ("Peacock", "peacock"),
        ("Robin", "american robin"), ("Blue Jay", "blue jay"), ("Cardinal", "northern cardinal"), ("Hummingbird", "hummingbird"),
        ("Mallard Duck", "mallard duck"), ("Swan", "mute swan"), ("Macaw", "macaw parrot"), ("Toucan", "toucan"),
        ("Pelican", "pelican"), ("Puffin", "atlantic puffin"), ("Woodpecker", "woodpecker"), ("Red-tailed Hawk", "red-tailed hawk"),
        ("Canada Goose", "canada goose"), ("Crow", "american crow"), ("Seagull", "seagull"), ("Ostrich", "ostrich")]),
    "space": ("Space", "🚀", [
        ("Moon", "full moon"), ("Sun", "sun solar disk SDO"), ("Earth", "earth blue marble"), ("Mercury", "planet mercury messenger"),
        ("Venus", "planet venus"), ("Mars", "planet mars"), ("Jupiter", "planet jupiter"), ("Saturn", "planet saturn rings"),
        ("Uranus", "planet uranus"), ("Neptune", "planet neptune"), ("Pluto", "pluto new horizons"), ("Space Shuttle", "space shuttle launch"),
        ("Rocket", "saturn v rocket"), ("Space Station", "international space station"), ("Milky Way", "milky way galaxy"), ("Nebula", "orion nebula"),
        ("Comet", "comet"), ("Solar Eclipse", "total solar eclipse"), ("Hubble Telescope", "hubble space telescope"), ("Mars Rover", "curiosity rover mars")]),
    "produce": ("Fruits & Veggies", "🍎", [
        ("Apple", "red apple fruit"), ("Banana", "banana fruit"), ("Orange", "orange fruit"), ("Strawberry", "strawberry"),
        ("Grapes", "grapes"), ("Watermelon", "watermelon"), ("Pineapple", "pineapple"), ("Pear", "pear fruit"),
        ("Cherries", "cherries"), ("Lemon", "lemon fruit"), ("Carrot", "carrot"), ("Broccoli", "broccoli"),
        ("Tomato", "tomato"), ("Corn", "corn on the cob"), ("Potato", "potato"), ("Cucumber", "cucumber"),
        ("Pumpkin", "pumpkin"), ("Bell Pepper", "bell pepper"), ("Peas", "green peas pods"), ("Onion", "onion")]),
    "vehicles": ("Trucks & Vehicles", "🚒", [
        ("Fire Truck", "fire truck"), ("Dump Truck", "dump truck"), ("Garbage Truck", "garbage truck"), ("Tractor", "tractor"),
        ("School Bus", "school bus"), ("Ambulance", "ambulance"), ("Police Car", "police car"), ("Train", "locomotive train"),
        ("Airplane", "airplane in flight"), ("Helicopter", "helicopter"), ("Bicycle", "bicycle"), ("Motorcycle", "motorcycle"),
        ("Sailboat", "sailboat"), ("Cement Mixer", "cement mixer truck"), ("Excavator", "excavator"), ("Bulldozer", "bulldozer"),
        ("Tow Truck", "tow truck"), ("Pickup Truck", "pickup truck"), ("Taxi", "taxi cab"), ("Hot Air Balloon", "hot air balloon")]),
}

# Tried in order; the OR form is not supported by Commons search, so each is its own query.
LICENSE_QUERIES = ['insource:"PD-USGov"', 'incategory:"CC-Zero"', 'insource:"PD-self"']

BAD_TITLE = re.compile(
    r"\b(mite|larva|microscop\w*|tick|map|diagram|logo|drawing|painting|stamp|cartoon|illustration|icon|chart|"
    r"skeleton|fossil|egg|nest|sign|poster|crash|accident|wreck|flag|coat of arms)\b", re.I)

# Extra words that may name a card besides its label (regex alternatives).
SYNONYMS = {
    "Owl": "owl", "Seagull": "gull", "Mallard Duck": "mallard|duck", "Red-tailed Hawk": "hawk", "Canada Goose": "goose|geese",
    "Space Station": "station|iss", "Hubble Telescope": "hubble", "Mars Rover": "rover", "Milky Way": "milky",
    "Solar Eclipse": "eclipse", "Rocket": "rocket|saturn v", "Space Shuttle": "shuttle", "Cherries": "cherr(?:y|ies)",
    "Peas": "pea|peas", "Bell Pepper": "pepper", "Hot Air Balloon": "balloon", "Cement Mixer": "mixer|concrete",
    "Tow Truck": "tow|wrecker", "Pickup Truck": "pickup", "Taxi": "taxi|cab", "Fire Truck": "fire (?:truck|engine)|firetruck|fire apparatus",
    "Dump Truck": "dump", "Garbage Truck": "garbage|refuse|waste", "Police Car": "police", "School Bus": "school bus",
    "Train": "train|locomotive", "Airplane": "airplane|aircraft|plane|jet", "Bicycle": "bicycle|bike",
    "Sailboat": "sailboat|sailing|yacht|sloop", "Bulldozer": "bulldozer|dozer", "Sun": "sun|solar",
}


def req(url, delay, **kw):
    """GET with Retry-After handling."""
    for _ in range(12):
        try:
            r = S.get(url, timeout=60, **kw)
        except requests.RequestException as e:
            print("   network error, retrying:", str(e)[:80], flush=True)
            time.sleep(5)
            continue
        if r.status_code == 429:
            wait = int(r.headers.get("Retry-After", "15")) + 3
            print(f"   rate-limited, waiting {wait}s", flush=True)
            time.sleep(wait)
            continue
        r.raise_for_status()
        time.sleep(delay)
        return r
    raise RuntimeError("too many retries: " + url[:80])


def val(md, key):
    return (md.get(key) or {}).get("value", "") or ""


def license_ok(md):
    lic = val(md, "LicenseShortName").lower().strip()
    is_free = lic.startswith(("public domain", "cc0", "pd-", "pd ")) or lic in ("pd", "pdm")
    if not is_free:
        return False, lic
    if val(md, "AttributionRequired").lower() == "true" or val(md, "NonFree").lower() == "true":
        return False, lic
    if val(md, "Restrictions").strip():  # e.g. trademarked, personality rights
        return False, lic
    return True, lic


def relevant(label, title, md):
    desc = re.sub(r"<[^>]+>", " ", val(md, "ImageDescription"))
    text = (title + " " + desc).lower()
    pat = SYNONYMS.get(label) or "|".join(
        w[:-1] if w.endswith("s") and len(w) > 3 else w for w in re.split(r"[ -]+", label.lower()))
    return re.search(r"\b(?:" + pat + r")(?:s|es)?\b", text) is not None


def search(q, lq, delay):
    p = dict(action="query", format="json", generator="search", gsrsearch=f"{q} {lq} filetype:bitmap",
             gsrnamespace=6, gsrlimit=30, prop="imageinfo", iiprop="url|size|mime|extmetadata", iiurlwidth=900,
             iiextmetadatafilter="LicenseShortName|AttributionRequired|NonFree|Restrictions|Artist|ImageDescription")
    pages = (req(API, delay, params=p).json().get("query") or {}).get("pages", {})
    return sorted(pages.values(), key=lambda x: x.get("index", 0))


def candidates(q, delay):
    for lq in LICENSE_QUERIES:
        yield from search(q, lq, delay)


def square(im):
    im = im.convert("RGB")
    w, h = im.size
    s = min(w, h)
    left, top = (w - s) // 2, (h - s) // 2
    return im.crop((left, top, left + s, top + s)).resize((SIZE, SIZE), Image.LANCZOS)


def fetch_card(gid, label, q, want, used, delay):
    found = []
    for pg in candidates(q, delay):
        if len(found) >= want:
            break
        ii = (pg.get("imageinfo") or [None])[0]
        title = pg["title"]
        if not ii or title in used or BAD_TITLE.search(title) or ii["mime"] != "image/jpeg":
            continue
        if min(ii["width"], ii["height"]) < 500 or not 0.45 <= ii["width"] / ii["height"] <= 2.2:
            continue
        md = ii.get("extmetadata", {})
        if not relevant(label, title, md):
            continue
        ok, lic = license_ok(md)
        if not ok:
            continue
        try:
            im = Image.open(io.BytesIO(req(ii.get("thumburl") or ii["url"], delay).content))
        except Exception as e:  # unreadable download: skip this candidate
            print("   skipped", title, str(e)[:60])
            continue
        fn = f"{gid}_{re.sub(r'[^a-z0-9]+', '-', label.lower())}_{len(found) + 1}.jpg"
        square(im).save(os.path.join(IMG, fn), "JPEG", quality=78, optimize=True, progressive=True)
        found.append(dict(file=fn, title=title, page=ii["descriptionurl"], license=lic,
                          author=re.sub(r"<[^>]+>", "", val(md, "Artist")).strip()[:120]))
        used.add(title)
    return found


def make_sheets(gid, name, items, per_sheet=5):
    thumb, label_w = 200, 150
    for n in range(0, len(items), per_sheet):
        chunk = items[n:n + per_sheet]
        sheet = Image.new("RGB", (label_w + 3 * thumb, len(chunk) * (thumb + 10)), "white")
        d = ImageDraw.Draw(sheet)
        for r, it in enumerate(chunk):
            y = r * (thumb + 10)
            d.text((6, y + 6), f"{n + r + 1}. {it['label']}", fill="black")
            for c, cand in enumerate(it["candidates"]):
                im = Image.open(os.path.join(IMG, cand["file"])).resize((thumb, thumb))
                sheet.paste(im, (label_w + c * thumb, y))
        sheet.save(os.path.join(SHEETS, f"{gid}_{n // per_sheet + 1}.jpg"), quality=80)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("only", nargs="*", help="group ids (birds space produce vehicles) or group:Label")
    ap.add_argument("--candidates", type=int, default=3, help="candidates to keep per card (default 3)")
    ap.add_argument("--delay", type=float, default=1.5, help="seconds between requests (default 1.5)")
    a = ap.parse_args()
    for d in (IMG, SHEETS):
        os.makedirs(d, exist_ok=True)

    wanted = {}
    for spec in a.only or list(GROUPS):
        gid, _, label = spec.partition(":")
        if gid not in GROUPS:
            sys.exit(f"unknown group '{gid}'; choose from {', '.join(GROUPS)}")
        wanted.setdefault(gid, set())
        if label:
            wanted[gid].add(label)

    used = set()
    for gid, only_labels in wanted.items():
        name, emoji, cards = GROUPS[gid]
        path = os.path.join(OUT, f"{gid}.json")
        data = json.load(open(path, encoding="utf-8")) if os.path.exists(path) else {"id": gid, "name": name, "emoji": emoji, "items": []}
        by_label = {it["label"]: it for it in data["items"]}
        for it in data["items"]:
            used.update(c["title"] for c in it["candidates"])
        for label, q in cards:
            if only_labels and label not in only_labels:
                continue
            if label in by_label and by_label[label]["candidates"] and not only_labels:
                continue  # already done; name it explicitly (group:Label) to redo
            found = fetch_card(gid, label, q, a.candidates, used, a.delay)
            print(f"{'OK  ' if found else 'MISS'} {gid:9} {label:18} {len(found)} candidate(s)", flush=True)
            by_label[label] = dict(label=label, candidates=found)
            data["items"] = [by_label[l] for l, _ in cards if l in by_label]
            json.dump(data, open(path, "w", encoding="utf-8"), indent=1, ensure_ascii=False)
        make_sheets(gid, name, [it for it in data["items"] if it["candidates"]])
    print("\nDone. Review tools/out/sheets/*.jpg, then see the cards marked MISS above.")


if __name__ == "__main__":
    main()
