"""A fake actor for the tests of jobs/scraper.py, run by the real subprocess (`python -m src`): no network.

It writes where the actor's SDK writes off the platform, under APIFY_LOCAL_STORAGE_DIR: one JSON file per item in
datasets/default, RUN_STATS (no extension) in key_value_stores/default, its registry in a named store. Unlike the
SDK, it never purges anything, so the tests see what the collection itself clears. Its behaviour comes from
behaviour.json next to this package (the environment is filtered, it cannot carry it), and it leaves what it
received in the storage: fake-input.json, fake-env.json.
"""
import json
import os
import sys
import time
from pathlib import Path

behaviour = json.loads((Path(__file__).parent.parent / "behaviour.json").read_text())
storage = Path(os.environ["APIFY_LOCAL_STORAGE_DIR"])
default_store = storage / "key_value_stores" / "default"
actor_input = json.loads((default_store / "INPUT.json").read_text())
(storage / "fake-input.json").write_text(json.dumps(actor_input))
(storage / "fake-env.json").write_text(json.dumps(dict(os.environ)))
print("fake actor: started")
print("fake actor: a warning", file=sys.stderr)
print("x " * (behaviour.get("log_bytes", 0) // 2))  # spaces keep the log from reading as one long token
print(behaviour.get("log_text", ""))

registry = storage / "key_value_stores" / "francophone-jobs-registry"
registry.mkdir(parents=True, exist_ok=True)
seen_path = registry / "SEEN-test"
seen = json.loads(seen_path.read_text()) if seen_path.exists() else []
(storage / "fake-registry-before.json").write_text(json.dumps(seen))
seen_path.write_text(json.dumps(seen + [actor_input.get("registryKey")]))

dataset = storage / "datasets" / "default"
dataset.mkdir(parents=True, exist_ok=True)
(dataset / "__metadata__.json").write_text(json.dumps({"item_count": len(behaviour.get("items", []))}))
for number, item in enumerate(behaviour.get("items", []), start=1):
    path = dataset / f"{number:09d}.json"
    if isinstance(item, dict) and "__raw__" in item:
        path.write_bytes(item["__raw__"].encode("latin-1"))
    else:
        path.write_text(json.dumps(item))

if "run_stats" in behaviour:
    value = behaviour["run_stats"]
    default_store.joinpath("RUN_STATS").write_text(value if isinstance(value, str) else json.dumps(value))
time.sleep(behaviour.get("sleep", 0))
sys.exit(behaviour.get("exit", 0))
