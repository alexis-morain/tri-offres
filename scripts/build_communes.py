#!/usr/bin/env python3
"""Builds jobs/data/communes.json.gz, the French communes with their postal codes and the point of their centre.

    python3 scripts/build_communes.py

Data: API Découpage administratif, https://geo.api.gouv.fr, Licence Ouverte Etalab 2.0. Paris, Lyon and Marseille
come whole (75056, 69123, 13055) with the postal codes of all their arrondissements. Run it again when communes
merge (each 1st of January, in practice), then run the tests (`jobs/test_geo.py`).

Each commune: [INSEE code, name, department code, [postal codes], latitude, longitude, population, region code], in
the layout jobs.geo defines; coordinates rounded to 4 decimals (about 10 m), null for a commune without a centre.
"""
import gzip
import json
import re
import sys
import urllib.request
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from jobs import geo  # noqa: E402  (the row layout; jobs.geo imports nothing from Django)

OUT = ROOT / "jobs" / "data" / "communes.json.gz"
SOURCE = "geo.api.gouv.fr (API Découpage administratif), Licence Ouverte Etalab 2.0"
URL = ("https://geo.api.gouv.fr/communes"
       "?fields=nom,code,codesPostaux,codeDepartement,codeRegion,centre,population&format=json")


def row(commune):
    """One commune of the API in the layout of jobs.geo. Regions are the 18 of two digits; the overseas
    collectivities (975, 977, 98x…) have none, whatever code the API gives them."""
    centre = (commune.get("centre") or {}).get("coordinates")
    region = commune.get("codeRegion")
    item = [None] * 8
    item[geo.INSEE], item[geo.NAME] = commune["code"], commune["nom"]
    item[geo.DEPARTMENT] = commune.get("codeDepartement")
    item[geo.POSTAL_CODES] = sorted(commune.get("codesPostaux") or [])
    if centre:
        item[geo.LONGITUDE], item[geo.LATITUDE] = (round(value, 4) for value in centre)
    item[geo.POPULATION] = commune.get("population") or 0
    item[geo.REGION] = region if region and re.fullmatch(r"\d\d", region) else None
    return item


def main():
    request = urllib.request.Request(URL, headers={"User-Agent": "tri-offres build_communes"})
    with urllib.request.urlopen(request, timeout=120) as response:
        communes = json.loads(response.read())
    rows = sorted(row(commune) for commune in communes)
    data = {"source": SOURCE, "built_on": date.today().isoformat(), "communes": rows}
    OUT.parent.mkdir(parents=True, exist_ok=True)
    raw = json.dumps(data, ensure_ascii=False, separators=(",", ":")).encode()
    OUT.write_bytes(gzip.compress(raw, mtime=0))
    without = sum(1 for item in rows if item[geo.LATITUDE] is None)
    print(f"{len(rows)} communes, {without} without a centre -> {OUT} ({OUT.stat().st_size // 1024} Ko)")


if __name__ == "__main__":
    main()
