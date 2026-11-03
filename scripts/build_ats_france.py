"""Builds jobs/data/ats-france.txt: the ATS job boards of the actor's directory that serve offers in France.

Runs the actor once on every board of its directory (presetLists all-<ats>), France only, without descriptions, in
a storage folder of its own (never the night's registry), then keeps each board that returned at least one offer.
Run it in the scheduler container, so the requests leave by the proxy of SCRAPER_PROXY_URL, once a quarter:

    sudo docker cp scripts/build_ats_france.py tri-offres-scheduler:/tmp/
    sudo docker exec tri-offres-scheduler python /tmp/build_ats_france.py /data/ats-france.txt
    sudo docker cp tri-offres-scheduler:/data/ats-france.txt jobs/data/ats-france.txt

Reads SCRAPER_PYTHON, SCRAPER_DIR and SCRAPER_PROXY_URL from the environment, like jobs/scraper.py. Standard library
only: the container's Django is not needed.
"""
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

ATS = ("greenhouse", "ashby", "lever", "workday", "smartrecruiters", "workable", "personio", "rippling", "recruitee")
STORAGE = Path("/tmp/ats-france-storage")
ENVIRONMENT = ("PATH", "HOME", "LANG", "LC_ALL", "TZ")


def actor_input():
    # maxPresetBoards: the whole directory (23 714 boards on 15/10); the schema's 20 000 is not checked off Apify.
    return {"sources": ["ats"], "presetLists": [f"all-{ats}" for ats in ATS], "maxPresetBoards": 30000,
            "countries": ["FR"], "languages": [], "mode": "all", "includeDescription": False,
            "maxResultsPerSource": 0, "maxMinutesPerSource": 0}


def boards(items, directory):
    """The sorted "ats:slug" of the items, slugs written as in the directory: the actor writes source=<ats> and
    job_id="<slug in lower case>:<id>", and a Workday slug ("sanofi|wd3|SanofiCareers") keeps its case."""
    slugs = {(entry["ats"], entry["slug"].lower()): entry["slug"] for entry in directory}
    found = set()
    for item in items:
        ats, job_id = item.get("source"), item.get("job_id")
        if ats in ATS and isinstance(job_id, str) and ":" in job_id:
            slug = slugs.get((ats, job_id.partition(":")[0].lower()))
            if slug:  # only boards of the directory, never a string the actor made up
                found.add(f"{ats}:{slug}")
    return sorted(found)


def directory():
    path = Path(os.environ["SCRAPER_DIR"]) / "src" / "data" / "ats-directory.jsonl"
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def run(read_only=False):
    """Runs the actor, then reads its dataset; with read_only, reads the dataset of the last run only."""
    code = 0 if read_only else _run_actor()
    items = (json.loads(path.read_text()) for path in sorted((STORAGE / "datasets" / "default").glob("*.json"))
             if path.stem.isdigit())
    return code, boards(items, directory())


def _run_actor():
    shutil.rmtree(STORAGE, ignore_errors=True)
    store = STORAGE / "key_value_stores" / "default"
    store.mkdir(parents=True)
    (store / "INPUT.json").write_text(json.dumps(actor_input()))
    env = {name: os.environ[name] for name in ENVIRONMENT if name in os.environ}
    env["APIFY_LOCAL_STORAGE_DIR"] = str(STORAGE)
    proxy = os.environ.get("SCRAPER_PROXY_URL", "").strip()
    if not proxy:
        sys.exit("SCRAPER_PROXY_URL est vide : jamais l’IP de la box.")
    env["HTTPS_PROXY"] = env["HTTP_PROXY"] = proxy
    with open(STORAGE / "actor.log", "wb") as log:
        return subprocess.run([os.environ["SCRAPER_PYTHON"], "-m", "src"], cwd=os.environ["SCRAPER_DIR"], env=env,
                              stdin=subprocess.DEVNULL, stdout=log, stderr=subprocess.STDOUT).returncode


def self_check():
    known = [{"ats": "greenhouse", "slug": "doctolib"}, {"ats": "lever", "slug": "qonto"},
             {"ats": "workday", "slug": "sanofi|wd3|SanofiCareers"}]
    items = [{"source": "greenhouse", "job_id": "Doctolib:123"}, {"source": "greenhouse", "job_id": "doctolib:9"},
             {"source": "lever", "job_id": "qonto:a-b"}, {"source": "wttj", "job_id": "x:1"},
             {"source": "lever", "job_id": "no-colon"}, {"source": "ashby", "job_id": "unknown:1"},
             {"source": "workday", "job_id": "sanofi|wd3|sanoficareers:R123"}]
    found = boards(items, known)
    assert found == ["greenhouse:doctolib", "lever:qonto", "workday:sanofi|wd3|SanofiCareers"], found


if __name__ == "__main__":
    self_check()
    if len(sys.argv) not in (2, 3) or sys.argv[2:] not in ([], ["--relire"]):
        sys.exit("usage: build_ats_france.py <fichier de sortie> [--relire]   (--relire : dataset du dernier run)")
    code, found = run(read_only=sys.argv[2:] == ["--relire"])
    Path(sys.argv[1]).write_text("".join(f"{board}\n" for board in found))
    print(f"actor : code {code} ; {len(found)} boards avec au moins une offre en France -> {sys.argv[1]}")
    sys.exit(0 if code == 0 and found else 1)
