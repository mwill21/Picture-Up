#!/usr/bin/env python3
"""Embed the picked photos from tools/picks.json into index.html and write CREDITS.md.

Candidates live on separate git branches (one per fetch round), each holding tools/out/.
Check each branch out (or `git archive` it) into a folder and pass the folders as
    python tools/build_groups.py 1=<folder with tools/out> 2=<folder> ...

The generated groups sit between BEGIN/END marker comments in index.html, so re-running
this replaces them cleanly. Nothing else in index.html is touched.
"""
import base64
import datetime
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
GROUPS = [("birds", "Birds", "🐦"), ("space", "Space", "🚀"), ("produce", "Fruits & Veggies", "🍎"), ("vehicles", "Trucks & Vehicles", "🚒")]
LABELS = {"Red-tailed Hawk": "Hawk"}  # photo not confirmed to be the red-tailed species
BEGIN, END = "  // BEGIN generated picture groups (tools/build_groups.py)\n", "  // END generated picture groups\n"


def main():
    rounds = dict(a.split("=", 1) for a in sys.argv[1:])
    if not rounds:
        sys.exit(__doc__)
    picks = json.load(open(os.path.join(HERE, "picks.json"), encoding="utf-8"))
    data = {}  # (round, gid) -> {label: item}
    blocks, credits = [], []
    for gid, name, emoji in GROUPS:
        order = [l for l in picks[gid]]
        cards = []
        for label in order:
            rnd, n = picks[gid][label].split(":")
            if rnd not in rounds:
                sys.exit(f"picks.json uses round {rnd} but no folder was given for it")
            key = (rnd, gid)
            if key not in data:
                path = os.path.join(rounds[rnd], "tools", "out", f"{gid}.json")
                data[key] = {i["label"]: i for i in json.load(open(path, encoding="utf-8"))["items"]}
            cand = data[key][label]["candidates"][int(n) - 1]
            raw = open(os.path.join(rounds[rnd], "tools", "out", "img", cand["file"]), "rb").read()
            shown = LABELS.get(label, label).replace("\\", "\\\\").replace("'", "\\'")
            cards.append(f"      {{label:'{shown}', emoji:'', img:'data:image/jpeg;base64,{base64.b64encode(raw).decode()}'}},")
            credits.append((name, LABELS.get(label, label), cand))
        blocks.append(f"  {{\n    id: '{gid}', name: '{name}', emoji: '{emoji}',\n    cards: [\n" + "\n".join(cards) + "\n    ]\n  }")
        print(f"{gid}: {len(cards)} cards")

    path = os.path.join(REPO, "index.html")
    html = open(path, encoding="utf-8").read()
    new = BEGIN + ",\n".join(blocks) + "\n" + END
    if BEGIN in html:
        a, b = html.index(BEGIN), html.index(END) + len(END)
        html = html[:a] + new + html[b:]
    else:
        start = html.index("const CATEGORIES = [")
        marker = "\n  }\n];\n"
        end = html.index(marker, start)
        html = html[:end] + "\n  },\n" + new + "];\n" + html[end + len(marker):]
    open(path, "w", encoding="utf-8").write(html)

    lines = ["# Image credits", "",
             "Photos in the Birds, Space, Fruits & Veggies and Trucks & Vehicles groups come from Wikimedia Commons.",
             "Each file was marked public domain or CC0 in its Commons license metadata when it was fetched, "
             f"and was reviewed by eye before use (built {datetime.date.today().isoformat()}). "
             "Images were center-cropped to a square and resized to 400x400.", "",
             "| Group | Card | Commons page | License | Author/source |", "|---|---|---|---|---|"]
    for g, label, c in credits:
        author = (c["author"] or "see page").replace("|", "/").replace("\n", " ").strip()
        lines.append(f"| {g} | {label} | {c['page']} | {c['license']} | {author} |")
    open(os.path.join(REPO, "CREDITS.md"), "w", encoding="utf-8").write("\n".join(lines) + "\n")
    print("index.html:", round(os.path.getsize(path) / 1e6, 2), "MB;", len(credits), "credit rows")


if __name__ == "__main__":
    main()
