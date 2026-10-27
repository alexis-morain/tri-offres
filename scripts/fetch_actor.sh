#!/bin/sh
# Fill vendor/actor with the actor's code at a fixed commit, and vendor/actor-venv with its dependencies
# (the design plan, section 3). The actor is sold: vendor/ is never committed.
#
#   scripts/fetch_actor.sh [ref]    ref defaults to the commit in ACTOR_REF
#
# ACTOR_REPO points to another clone of Francophone-Jobs-Scraper; PYTHON to the interpreter of the venv.
set -eu

root=$(cd "$(dirname "$0")/.." && pwd)
repo=${ACTOR_REPO:-$root/../Francophone-Jobs-Scraper}
ref=${1:-$(cat "$root/ACTOR_REF")}
commit=$(git -C "$repo" rev-parse --verify --quiet "$ref^{commit}") || {
    echo "Ref $ref inconnue dans $repo." >&2
    exit 1
}

rm -rf "$root/vendor/actor"
mkdir -p "$root/vendor/actor"
git -C "$repo" archive "$commit" actor | tar -x -C "$root/vendor/actor" --strip-components 1
echo "$commit" > "$root/vendor/actor/REF"

if [ -z "${PYTHON:-}" ]; then
    PYTHON=$(command -v python3.12 || echo "$root/.venv/bin/python")
fi
rm -rf "$root/vendor/actor-venv"
"$PYTHON" -m venv "$root/vendor/actor-venv"
"$root/vendor/actor-venv/bin/pip" install --quiet --disable-pip-version-check -r "$root/vendor/actor/requirements.txt"
echo "Actor $commit dans vendor/actor, dépendances dans vendor/actor-venv."
if [ "$commit" != "$(cat "$root/ACTOR_REF")" ]; then
    echo "Attention : ce commit n’est pas celui de ACTOR_REF, l’image refusera de se construire." >&2
fi
