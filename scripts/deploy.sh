#!/usr/bin/env bash
# shellcheck disable=SC2029
# Déploie tri-offres sur l’hôte : application personnalisée « tri-offres » (services web, scheduler, litestream).
# Pas à pas, avec les actions qui reviennent à Alexis : docs/deploiement.md. Procédure de restauration :
# docs/restauration.md. Ce script est écrit pour être relu et lancé à la main, depuis le poste de travail.
#
# Ce que ça pose sur l’hôte, dans $TRI_DEPLOY_DIR :
#   src/              le code du commit HEAD (git archive) plus vendor/actor s'il est là ; vidé à chaque passage
#   data/             la base SQLite, le registre de l'actor, le journal des comptes : JAMAIS touché (créé à
#                     l'uid 1000, en 750, seulement s'il manque)
#   env/              700 root. web.env, scheduler.env, litestream.env : 600 root, construits par LISTE BLANCHE de
#                     clés lues dans ENV_FILE, jamais une copie du .env. Aucune valeur n'est affichée ni passée
#                     en argument de commande : elles voyagent sur l'entrée standard de `sudo tee`.
#   litestream.yml    copie de docker/litestream.yml, montée en lecture seule
#   et une image tri-offres:<commit court> construite sur l’hôte (les images précédentes restent : retour arrière),
#   puis la custom app (`midclt call app.create` ou `app.update`) dont le compose ne contient AUCUN secret.
#
# Le jeton Max, APIFY_* et toute clé hors liste blanche ne partent jamais. L’hôte tourne en CLAUDE_ENGINE=api,
# derrière le tunnel (TRUST_CF_CONNECTING_IP=1), avec DJANGO_DEBUG=0, SCRAPER_PROXY_REQUIRED=1 et DEV_EMAILS vide :
# ces valeurs sont posées par le script, quoi que dise ENV_FILE.
# Exception possible : TRI_DEPLOY_ENGINE=oauth, exporté dans le shell, déploie un site pour les
# seules adresses de DEV_EMAILS, sur le jeton Max. Le jeton part alors (web et scheduler), DEV_EMAILS vient de
# ENV_FILE, aucune clé OpenRouter ne part et BETA_CAPACITY vaut le nombre d'adresses de DEV_EMAILS. Les garde-fous
# de llm/core.py et mail.send refusent tout autre compte.
#
# Le dépôt est public : aucune valeur d'infrastructure ici. Tout vient de variables d'environnement obligatoires,
# sans défaut, à exporter dans le shell (pas dans le dépôt) :
#   TRI_DEPLOY_HOST     nom ou adresse de l’hôte
#   TRI_DEPLOY_USER     utilisateur SSH (sudo sans mot de passe)
#   TRI_DEPLOY_KEY      chemin de la clé SSH privée
#   TRI_DEPLOY_LAN_IP   adresse LAN de l’hôte sur laquelle le port est publié (jamais 0.0.0.0)
#   TRI_DEPLOY_PORT     port publié pour cloudflared (vers le port 8000 du conteneur web)
#   TRI_DEPLOY_DOMAIN   domaine public du site
#   TRI_DEPLOY_DIR      dossier de l'app sur l’hôte (chemin absolu, deux niveaux au moins)
# Facultatifs : TRI_DEPLOY_ENGINE (api par défaut, ou oauth, voir plus haut), ENV_FILE (défaut .env du dépôt), COMPOSE_OUT (défaut /tmp/tri-offres-compose.yaml, --dry-run).
# Pour les tests seulement : TRI_DEPLOY_KEEP_DIR, dossier privé (700) où --dry-run recopie les trois fichiers
# d'environnement avant de les effacer.
#
# Usage : scripts/deploy.sh [--dry-run | --build-only]
#   --dry-run     aucune connexion : garde-fous, fichiers d'environnement (dossier temporaire privé, effacé),
#                 archive du code vérifiée, compose écrit dans COMPOSE_OUT, NOMS des clés retenues.
#   --build-only  code, image et tests de l'image sur l’hôte ; ni fichiers d'environnement ni app touchés.
#   (rien)        déploiement complet.
set -euo pipefail
umask 077
cd "$(dirname "$0")/.."

APP_NAME="tri-offres"
ENV_FILE="${ENV_FILE:-.env}"
COMPOSE_OUT="${COMPOSE_OUT:-/tmp/tri-offres-compose.yaml}"
# Resource limits of the compose file. The scheduler runs the actor and the notation: measure on the hôte (docs).
WEB_MEM="1g"; WEB_PIDS=256
SCHEDULER_MEM="2g"; SCHEDULER_PIDS=512
LITESTREAM_MEM="256m"; LITESTREAM_PIDS=100

die() { printf 'ERREUR : %s\n' "$*" >&2; exit 1; }
warn() { printf 'ATTENTION : %s\n' "$*" >&2; }
info() { printf '%s\n' "$*"; }

usage() {
  cat >&2 <<'TXT'
Usage : scripts/deploy.sh [--dry-run | --build-only]
  --dry-run     aucune connexion, fichiers produits en local puis effacés
  --build-only  code, image et tests de l’image sur l’hôte, sans toucher l’app
  (rien)        déploiement complet
Variables obligatoires : TRI_DEPLOY_HOST, TRI_DEPLOY_USER, TRI_DEPLOY_KEY, TRI_DEPLOY_LAN_IP, TRI_DEPLOY_PORT,
TRI_DEPLOY_DOMAIN, TRI_DEPLOY_DIR (voir l’en-tête du script et docs/deploiement.md).
TXT
}

ENGINE="${TRI_DEPLOY_ENGINE:-api}"
case "$ENGINE" in api|oauth) ;; *) die "TRI_DEPLOY_ENGINE : api ou oauth." ;; esac

MODE="deploy"
case "${1:-}" in
  "") ;;
  --dry-run) MODE="dry-run" ;;
  --build-only) MODE="build-only" ;;
  -h|--help) usage; exit 0 ;;
  *) usage; exit 2 ;;
esac
[ "$#" -le 1 ] || { usage; exit 2; }

# --- Infrastructure variables: all required, none has a default, checked before anything else ---
missing=""
for name in TRI_DEPLOY_HOST TRI_DEPLOY_USER TRI_DEPLOY_KEY TRI_DEPLOY_LAN_IP TRI_DEPLOY_PORT TRI_DEPLOY_DOMAIN \
            TRI_DEPLOY_DIR; do
  [ -n "${!name:-}" ] || missing="$missing $name"
done
[ -z "$missing" ] || die "variable(s) obligatoire(s) absente(s) ou vide(s) :$missing. Les exporter dans le shell (docs/deploiement.md)."

octet='(25[0-5]|2[0-4][0-9]|1[0-9]{2}|[1-9]?[0-9])'
re_host='^[A-Za-z0-9][A-Za-z0-9.:_-]*$'
re_user='^[A-Za-z_][A-Za-z0-9_-]*$'
re_ip="^($octet\\.){3}$octet\$"
re_domain='^[a-z0-9]([a-z0-9-]*[a-z0-9])?(\.[a-z0-9]([a-z0-9-]*[a-z0-9])?)+$'
re_dir='^(/[A-Za-z0-9._-]+){2,}$'
re_hostname='^[a-z0-9]([a-z0-9-]*[a-z0-9])?(\.[a-z0-9]([a-z0-9-]*[a-z0-9])?)*$'
[[ $TRI_DEPLOY_HOST =~ $re_host ]] || die "TRI_DEPLOY_HOST : nom ou adresse invalide."
[[ $TRI_DEPLOY_USER =~ $re_user ]] || die "TRI_DEPLOY_USER : nom d’utilisateur invalide."
[[ $TRI_DEPLOY_LAN_IP =~ $re_ip ]] || die "TRI_DEPLOY_LAN_IP : adresse IPv4 attendue."
[ "$TRI_DEPLOY_LAN_IP" != "0.0.0.0" ] || die "TRI_DEPLOY_LAN_IP : jamais 0.0.0.0, l’adresse LAN de l’hôte."
[[ $TRI_DEPLOY_PORT =~ ^[0-9]{1,5}$ ]] && [ "$((10#$TRI_DEPLOY_PORT))" -ge 1 ] && [ "$((10#$TRI_DEPLOY_PORT))" -le 65535 ] \
  || die "TRI_DEPLOY_PORT : numéro de port entre 1 et 65535 attendu."
[[ $TRI_DEPLOY_DOMAIN =~ $re_domain ]] || die "TRI_DEPLOY_DOMAIN : nom de domaine seul attendu (sans schéma, port ni chemin)."
[[ $TRI_DEPLOY_DIR =~ $re_dir ]] || die "TRI_DEPLOY_DIR : chemin absolu de deux niveaux au moins, sans espace ni caractère spécial."
case "$TRI_DEPLOY_DIR/" in
  */../*|*/./*) die "TRI_DEPLOY_DIR : « . » et « .. » sont refusés." ;;
esac
# shellcheck disable=SC2088
case "$TRI_DEPLOY_KEY" in
  '~/'*) TRI_DEPLOY_KEY="$HOME/${TRI_DEPLOY_KEY#'~/'}" ;; # a tilde that was exported inside quotes
esac
case "$TRI_DEPLOY_KEY" in
  -*|*$'\n'*) die "TRI_DEPLOY_KEY : chemin invalide." ;;
esac
if [ "$MODE" != "dry-run" ]; then
  [ -r "$TRI_DEPLOY_KEY" ] || die "TRI_DEPLOY_KEY : clé SSH illisible."
  command -v ssh >/dev/null 2>&1 || die "ssh est introuvable."
  command -v python3 >/dev/null 2>&1 || die "python3 est introuvable (JSON de midclt, masquage des secrets)."
fi
KEEP_DIR="${TRI_DEPLOY_KEEP_DIR:-}"
if [ -n "$KEEP_DIR" ] && [ "$MODE" = "dry-run" ]; then
  [ -n "$(find "$KEEP_DIR" -maxdepth 0 -type d -perm 700 -user "$(id -u)" 2>/dev/null)" ] \
    || die "TRI_DEPLOY_KEEP_DIR : dossier existant, en 700, à vous, attendu."
fi

# --- Private working folder, removed on every exit ---
STAGE=""
cleanup() {
  case "$STAGE" in
    */tri-offres-deploy.*) rm -rf -- "$STAGE" ;;
  esac
}
trap cleanup EXIT
trap 'exit 130' INT
trap 'exit 143' TERM HUP
STAGE_BASE="${TMPDIR:-/tmp}"
STAGE="$(mktemp -d "${STAGE_BASE%/}/tri-offres-deploy.XXXXXX")"
chmod 700 "$STAGE"

# --- Guards before any connection: clean tree, known HEAD, actor at the pinned commit, pinned Litestream image ---
git rev-parse --verify --quiet HEAD >/dev/null || die "HEAD inconnu : le dépôt n’a aucun commit."
[ -z "$(git status --porcelain)" ] || die "l’arbre git n’est pas propre : committer ou écarter les changements avant de déployer."
COMMIT="$(git rev-parse --short=12 HEAD)"
IMAGE="$APP_NAME:$COMMIT"

[ -f ACTOR_REF ] || die "ACTOR_REF est absent."
ACTOR_REF_VALUE="$(tr -d '[:space:]' < ACTOR_REF)"
HAS_ACTOR=0
if [ -f vendor/actor/REF ]; then
  [ "$(tr -d '[:space:]' < vendor/actor/REF)" = "$ACTOR_REF_VALUE" ] \
    || die "vendor/actor n’est pas au commit de ACTOR_REF : relancer scripts/fetch_actor.sh."
  HAS_ACTOR=1
elif [ -d vendor/actor ]; then
  die "vendor/actor n’a pas de fichier REF : relancer scripts/fetch_actor.sh."
else
  warn "l’image sera construite SANS l’actor (vendor/actor est absent) : la collecte échouera CHAQUE NUIT, la notation, l’envoi et les purges tourneront quand même. Lancer scripts/fetch_actor.sh puis relancer ce script pour un déploiement complet."
fi

LITESTREAM_IMAGE="$(grep -E '^[[:space:]]+image: litestream/litestream:[0-9][^[:space:]]*@sha256:[0-9a-f]{64}[[:space:]]*$' \
  docker-compose.yml | sed -E 's/^[[:space:]]+image:[[:space:]]+//; s/[[:space:]]+$//' || true)"
case "$LITESTREAM_IMAGE" in
  ""|*$'\n'*) die "docker-compose.yml : une seule image Litestream épinglée par empreinte sha256 est attendue." ;;
esac
[ -f docker/litestream.yml ] || die "docker/litestream.yml est absent."
[ -f Dockerfile ] || die "Dockerfile est absent."

# --- Environment files, built from a whitelist, one value at a time (the env file is never sourced) ---
WS=$' \t'
VALUE=""
DEV_EMAILS_VALUE=""
MISSING=""
CAPS="BETA_CAPACITY LOGIN_MAIL_DAILY_CAP CV_DAILY_CAP ON_DEMAND_DAILY_CAP SUMMARY_DAILY_CAP NIGHT_SCORE_CAP PREPASS_NIGHT_CAP"

# VALUE = the value of the last `key=` line of ENV_FILE, outer blanks and one pair of quotes removed. Status 1 when
# the key is absent. A quote that is not closed on its line is a value on several lines: refused.
env_value() {
  local line lead trail
  line="$(grep -E "^$1=" -- "$ENV_FILE" | tail -n 1 || true)"
  [ -n "$line" ] || return 1
  VALUE="${line#*=}"
  lead="${VALUE%%[!"$WS"]*}"
  VALUE="${VALUE#"$lead"}"
  trail="${VALUE##*[!"$WS"]}"
  VALUE="${VALUE%"$trail"}"
  case "$VALUE" in
    '""'|"''") VALUE="" ;;
    \"?*\") VALUE="${VALUE:1:${#VALUE}-2}" ;;
    \'?*\') VALUE="${VALUE:1:${#VALUE}-2}" ;;
    \"*|\'*) die "$1 : guillemet non fermé ou valeur sur plusieurs lignes dans $ENV_FILE." ;;
  esac
  return 0
}

# Refuses what Compose would read differently from the file (S20). Names the key, never the value.
check_value() {
  case "$VALUE" in *'$'*) die "$1 : la valeur contient « \$ », que Compose interpréterait." ;; esac
  [[ $VALUE != *[[:cntrl:]]* ]] || die "$1 : la valeur contient un retour à la ligne, un retour chariot ou un autre caractère de contrôle."
  case "$VALUE" in *\\*) die "$1 : la valeur contient une barre oblique inverse." ;; esac
  case "$VALUE" in *" #"*) die "$1 : la valeur contient « # » après une espace (début de commentaire pour Compose)." ;; esac
}

v_secret_key() {
  [ "${#VALUE}" -ge 50 ] || die "$1 : 50 caractères au moins (python manage.py shell -c \"from django.core.management.utils import get_random_secret_key as g; print(g())\", sans « \$ »)."
  case "$VALUE" in *django-insecure-*) die "$1 : clé de développement (django-insecure-) refusée." ;; esac
}
v_https() {
  case "$VALUE" in https://?*) ;; *) die "$1 : URL en https:// attendue." ;; esac
  [[ $VALUE != *[[:space:]]* ]] || die "$1 : URL sans espace attendue."
}
v_proxy() {
  case "$VALUE" in http://?*|https://?*) ;; *) die "$1 : URL en http:// ou https:// attendue." ;; esac
  [[ $VALUE != *[[:space:]]* ]] || die "$1 : URL sans espace attendue."
}
v_int() { [[ $VALUE =~ ^[0-9]+$ ]] || die "$1 : nombre entier attendu."; }
v_fernet() { [[ $VALUE =~ ^[A-Za-z0-9_-]{43}=(,[A-Za-z0-9_-]{43}=)*$ ]] || die "$1 : clés Fernet (44 caractères) séparées par des virgules attendues."; }
v_emails() {
  [[ $VALUE =~ ^[^,@[:space:]]+@[^,@[:space:]]+(,[^,@[:space:]]+@[^,@[:space:]]+)*$ ]] || die "$1 : adresses séparées par des virgules attendues."
  DEV_EMAILS_VALUE="$VALUE"
}
v_host() { [[ $VALUE =~ $re_hostname ]] || die "$1 : nom d’hôte seul attendu (sans schéma, port ni chemin)."; }

put() { # put FILE KEY (the value is in VALUE)
  [[ $2 =~ ^[A-Z][A-Z0-9_]*$ ]] || die "nom de clé refusé : $2."
  printf '%s=%s\n' "$2" "$VALUE" >> "$STAGE/$1.env"
}
put_fixed() { VALUE="$3"; put "$1" "$2"; }

take() { # take FILE KEY req|opt [validator]
  local file="$1" key="$2" mode="$3" check="${4:-}"
  if env_value "$key" && [ -n "$VALUE" ]; then
    check_value "$key"
    if [ -n "$check" ]; then "$check" "$key"; fi
    put "$file" "$key"
  elif [ "$mode" = "req" ]; then
    case " $MISSING " in *" $key "*) ;; *) MISSING="$MISSING $key" ;; esac
  fi
}

put_node_fixed() {
  put_fixed "$1" DJANGO_DEBUG 0
  put_fixed "$1" SITE_URL "https://$TRI_DEPLOY_DOMAIN"
  put_fixed "$1" DJANGO_ALLOWED_HOSTS "$TRI_DEPLOY_DOMAIN"
  put_fixed "$1" CLAUDE_ENGINE "$ENGINE"
  put_fixed "$1" TRUST_CF_CONNECTING_IP 1
  put_fixed "$1" SCRAPER_PROXY_REQUIRED 1
  if [ "$ENGINE" = "api" ]; then
    put_fixed "$1" DEV_EMAILS ""
    # The site's OpenRouter key serves the shared calls only; a person's calls go out on their own key, sealed with
    # these Fernet keys (llm/api.py, accounts/openrouter.py). No ANTHROPIC_API_KEY: the engine never reads it.
    take "$1" OPENROUTER_API_KEY req
    take "$1" OPENROUTER_KEYS_FERNET req v_fernet
  else
    take "$1" DEV_EMAILS req v_emails
    take "$1" CLAUDE_CODE_OAUTH_TOKEN req
    put_fixed "$1" BETA_CAPACITY "$(printf '%s' "$DEV_EMAILS_VALUE" | tr ',' '\n' | grep -c .)"
  fi
}

build_env_files() {
  local key
  [ -f "$ENV_FILE" ] && [ -r "$ENV_FILE" ] || die "ENV_FILE introuvable ou illisible : $ENV_FILE."
  [ "$(LC_ALL=C tr -d '\000' < "$ENV_FILE" | wc -c)" -eq "$(wc -c < "$ENV_FILE")" ] || die "$ENV_FILE contient un octet NUL."
  : > "$STAGE/web.env"; : > "$STAGE/scheduler.env"; : > "$STAGE/litestream.env"

  if env_value CLAUDE_ENGINE && [ -n "$VALUE" ] && [ "$VALUE" != "$ENGINE" ]; then
    warn "CLAUDE_ENGINE de $ENV_FILE est ignoré : l’hôte tourne en $ENGINE (TRI_DEPLOY_ENGINE)."
  fi

  # web: the site. No proxy, no ping URL, no alert address (S19).
  put_node_fixed web
  take web DJANGO_SECRET_KEY req v_secret_key
  take web DJANGO_SECRET_KEY_FALLBACKS opt
  take web RESEND_API_KEY req
  take web LEGAL_ADDRESS req
  take web LEGAL_SIRET req
  take web LEGAL_NAME req
  take web DEFAULT_FROM_EMAIL req
  take web MAIL_REPLY_TO req
  take web ADMIN_ACCESS_TEAM_DOMAIN opt v_host
  take web ADMIN_ACCESS_AUD opt
  take web HSTS_SECONDS opt v_int
  for key in $CAPS; do [ "$ENGINE:$key" = oauth:BETA_CAPACITY ] || take web "$key" opt v_int; done

  # scheduler: what the night needs, with the proxy, the ping URL and the alert address.
  put_node_fixed scheduler
  take scheduler DJANGO_SECRET_KEY req v_secret_key
  take scheduler DJANGO_SECRET_KEY_FALLBACKS opt
  take scheduler RESEND_API_KEY req
  take scheduler LEGAL_NAME req
  take scheduler DEFAULT_FROM_EMAIL req
  take scheduler MAIL_REPLY_TO req
  take scheduler SCRAPER_PROXY_URL req v_proxy
  take scheduler HEALTHCHECKS_PING_URL opt v_https
  take scheduler ALERT_EMAIL opt
  for key in SCRAPER_MAX_RESULTS_PER_SOURCE SCRAPER_MAX_MINUTES_PER_SOURCE SCRAPER_TIMEOUT_S $CAPS; do
    [ "$ENGINE:$key" = oauth:BETA_CAPACITY ] || take scheduler "$key" opt v_int
  done

  # litestream: the R2 keys, nothing else.
  take litestream LITESTREAM_ACCESS_KEY_ID req
  take litestream LITESTREAM_SECRET_ACCESS_KEY req
  take litestream LITESTREAM_R2_BUCKET req
  take litestream LITESTREAM_R2_ENDPOINT req v_https
  take litestream LITESTREAM_HEARTBEAT_URL opt v_https

  [ -z "$MISSING" ] || die "clé(s) absente(s) ou vide(s) dans $ENV_FILE :$MISSING."

  grep -q '^HEALTHCHECKS_PING_URL=' "$STAGE/scheduler.env" \
    || warn "HEALTHCHECKS_PING_URL est vide : aucun ping healthchecks.io, rien ne préviendra si le planificateur s’arrête."
  if ! grep -q '^LITESTREAM_HEARTBEAT_URL=' "$STAGE/litestream.env"; then
    printf 'LITESTREAM_HEARTBEAT_URL=\n' >> "$STAGE/litestream.env"
    warn "LITESTREAM_HEARTBEAT_URL est vide : rien ne préviendra si la sauvegarde s’arrête."
  fi
  if ! grep -q '^ADMIN_ACCESS_AUD=' "$STAGE/web.env" || ! grep -q '^ADMIN_ACCESS_TEAM_DOMAIN=' "$STAGE/web.env"; then
    warn "ADMIN_ACCESS_AUD ou ADMIN_ACCESS_TEAM_DOMAIN manque : l’admin du site restera fermé (404) tant que Cloudflare Access n’est pas configuré."
  fi
}

# Checks the files that were just built, whatever the code above did (S19, S20, S31).
verify_env_files() {
  local name file
  for name in web scheduler litestream; do
    file="$STAGE/$name.env"
    [ -s "$file" ] || die "$name.env est vide."
    grep -qvE '^[A-Z][A-Z0-9_]*=[^$]*$' "$file" && die "$name.env : une ligne n’est pas une affectation simple."
    [ -z "$(cut -d= -f1 "$file" | sort | uniq -d)" ] || die "$name.env : clé en double."
    grep -qE '^APIFY_[A-Z0-9_]*=' "$file" && die "$name.env : clé interdite."
    [ "$ENGINE" = "oauth" ] && [ "$name" != "litestream" ] && continue
    grep -qE '^CLAUDE_CODE_OAUTH_TOKEN=' "$file" && die "$name.env : clé interdite."
  done
  for name in web scheduler; do
    file="$STAGE/$name.env"
    grep -qx 'DJANGO_DEBUG=0' "$file" || die "$name.env : DJANGO_DEBUG=0 manque."
    grep -qx 'SCRAPER_PROXY_REQUIRED=1' "$file" || die "$name.env : SCRAPER_PROXY_REQUIRED=1 manque."
    grep -qx "CLAUDE_ENGINE=$ENGINE" "$file" || die "$name.env : CLAUDE_ENGINE=$ENGINE manque."
    if [ "$ENGINE" = "api" ]; then
      grep -qx 'DEV_EMAILS=' "$file" || die "$name.env : DEV_EMAILS doit être vide."
    else
      grep -qE '^DEV_EMAILS=.+' "$file" || die "$name.env : DEV_EMAILS ne doit pas être vide en oauth."
      grep -qE '^(ANTHROPIC_API_KEY|OPENROUTER_API_KEY|OPENROUTER_KEYS_FERNET)=' "$file" \
        && die "$name.env : pas de clé OpenRouter ni Anthropic en oauth."
    fi
    grep -q '^LITESTREAM_' "$file" && die "$name.env : une clé R2 n’a rien à faire ici."
    [ "$ENGINE" != "api" ] || grep -qE '^OPENROUTER_KEYS_FERNET=.+' "$file" \
      || die "$name.env : OPENROUTER_KEYS_FERNET manque."
  done
  grep -q '^ADMIN_ACCESS_' "$STAGE/scheduler.env" && die "scheduler.env : les clés de l’admin sont réservées au site."
  grep -qE '^(SCRAPER_PROXY_URL|HEALTHCHECKS_PING_URL|ALERT_EMAIL)=' "$STAGE/web.env" \
    && die "web.env : le proxy, le ping et l’alerte sont réservés au planificateur."
  grep -qE '^(OPENROUTER|ANTHROPIC)_' "$STAGE/litestream.env" && die "litestream.env : aucune clé API ne lui va."
  grep -qvE '^LITESTREAM_[A-Z0-9_]*=' "$STAGE/litestream.env" && die "litestream.env : seules les clés LITESTREAM_ y entrent."
  return 0
}

print_kept_names() {
  local name
  info "Clés retenues (noms seulement) :"
  for name in web scheduler litestream; do
    printf '  %s.env : %s\n' "$name" "$(cut -d= -f1 "$STAGE/$name.env" | tr '\n' ' ')"
  done
}

# --- Code archive: git archive HEAD plus vendor/actor (a symbolic link vendor is followed by the -C) ---
TAR_OPTS=()
for opt in --no-xattrs --no-mac-metadata; do
  if tar "$opt" -cf /dev/null -C "$STAGE" . >/dev/null 2>&1; then TAR_OPTS+=("$opt"); fi
done

build_archive() {
  local tree="$STAGE/tree"
  mkdir -p "$tree"
  git archive --format=tar HEAD | tar -xf - -C "$tree"
  if [ "$HAS_ACTOR" = 1 ]; then
    mkdir -p "$tree/vendor/actor"
    tar -C vendor/actor --exclude __pycache__ --exclude '*.pyc' --exclude .git -cf - . | tar -xf - -C "$tree/vendor/actor"
  fi
  # umask 077 left the tree owner-only: the image copies it as root and runs it as the app user.
  chmod -R u+rwX,go+rX "$tree"
  COPYFILE_DISABLE=1 tar ${TAR_OPTS[@]+"${TAR_OPTS[@]}"} -czf "$STAGE/src.tgz" -C "$tree" .
  tar -tzf "$STAGE/src.tgz" > "$STAGE/src.list"
  rm -rf -- "$tree"
  if grep -qE '(^|/)\.env($|\.bak\.)|(^|/)\._' "$STAGE/src.list"; then
    die "l’archive du code contient un fichier .env ou un fichier de métadonnées macOS."
  fi
  [ "$HAS_ACTOR" = 0 ] || grep -qx './vendor/actor/REF' "$STAGE/src.list" || die "vendor/actor/REF n’est pas dans l’archive."
  local actor_note=", SANS l’actor"
  if [ "$HAS_ACTOR" = 1 ]; then actor_note=", l’actor est inclus (vendor/actor/REF)"; fi
  info "Archive du code : $(wc -l < "$STAGE/src.list" | tr -d ' ') entrées$actor_note."
}

# --- Compose of the app: no secret, the env files stay on the hôte ---
render_compose() {
  local dir="$TRI_DEPLOY_DIR"
  cat <<YAML
# tri-offres : généré par scripts/deploy.sh (commit $COMMIT). Aucun secret ici : les variables viennent des
# fichiers $dir/env/*.env (600, root). L'image est construite sur l’hôte, jamais tirée d'un registre.
services:
  web:
    image: "$IMAGE"
    container_name: tri-offres-web
    pull_policy: never
    init: true
    restart: unless-stopped
    env_file:
      - "$dir/env/web.env"
    ports:
      - "$TRI_DEPLOY_LAN_IP:$TRI_DEPLOY_PORT:8000"
    volumes:
      - "$dir/data:/data"
    healthcheck:
      test: ["CMD", "python", "docker/healthcheck.py"]
      interval: 30s
      timeout: 5s
      start_period: 30s
      retries: 3
    cap_drop:
      - ALL
    security_opt:
      - no-new-privileges:true
    mem_limit: $WEB_MEM
    pids_limit: $WEB_PIDS
    logging:
      driver: json-file
      options:
        max-size: "20m"
        max-file: "3"

  scheduler:
    image: "$IMAGE"
    container_name: tri-offres-scheduler
    pull_policy: never
    init: true
    restart: unless-stopped
    command: ["python", "manage.py", "planificateur"]
    env_file:
      - "$dir/env/scheduler.env"
    volumes:
      - "$dir/data:/data"
    cap_drop:
      - ALL
    security_opt:
      - no-new-privileges:true
    mem_limit: $SCHEDULER_MEM
    pids_limit: $SCHEDULER_PIDS
    logging:
      driver: json-file
      options:
        max-size: "20m"
        max-file: "3"

  litestream:
    image: $LITESTREAM_IMAGE
    container_name: tri-offres-litestream
    restart: unless-stopped
    command: ["replicate", "-config", "/etc/litestream.yml"]
    user: "1000:1000"
    env_file:
      - "$dir/env/litestream.env"
    volumes:
      - "$dir/data:/data"
      - "$dir/litestream.yml:/etc/litestream.yml:ro"
    cap_drop:
      - ALL
    security_opt:
      - no-new-privileges:true
    mem_limit: $LITESTREAM_MEM
    pids_limit: $LITESTREAM_PIDS
    logging:
      driver: json-file
      options:
        max-size: "20m"
        max-file: "3"
YAML
}

write_compose_out() {
  local tmp
  [ ! -d "$COMPOSE_OUT" ] || die "COMPOSE_OUT est un dossier."
  tmp="$(mktemp "$COMPOSE_OUT.XXXXXX")"
  cat "$STAGE/compose.yaml" > "$tmp"
  chmod 644 "$tmp"
  mv -f -- "$tmp" "$COMPOSE_OUT"
}

SSH_HINT="ssh -i $TRI_DEPLOY_KEY $TRI_DEPLOY_USER@$TRI_DEPLOY_HOST"
print_next_steps() {
  cat <<TXT

À vérifier après un déploiement (rien ne le fait à votre place) :
  journaux des trois conteneurs :
    $SSH_HINT 'sudo docker logs --tail 50 tri-offres-web'
    $SSH_HINT 'sudo docker logs --tail 50 tri-offres-scheduler'
    $SSH_HINT 'sudo docker logs --tail 50 tri-offres-litestream'
  /healthz, par la sonde du conteneur (doit répondre « OK ») :
    $SSH_HINT 'sudo docker exec tri-offres-web python docker/healthcheck.py && echo OK'
  l’admin, derrière Cloudflare Access : ouvrir https://$TRI_DEPLOY_DOMAIN/admin/ (la page de connexion
  Access d’abord, puis celle de Django ; sans l’accès, une erreur 404).
Suite : docs/deploiement.md (tunnel, Access, premier compte staff, essai de restauration).
TXT
}

# --- Remote side ---
SSH_OPTS=(-i "$TRI_DEPLOY_KEY" -o IdentitiesOnly=yes -o BatchMode=yes -o ConnectTimeout=25 -o StrictHostKeyChecking=yes)
nas() { ssh -n "${SSH_OPTS[@]}" "$TRI_DEPLOY_USER@$TRI_DEPLOY_HOST" "$@"; }
nas_in() { ssh "${SSH_OPTS[@]}" "$TRI_DEPLOY_USER@$TRI_DEPLOY_HOST" "$@"; }
REMOTE_DIR="$TRI_DEPLOY_DIR"
REMOTE_SRC="$TRI_DEPLOY_DIR/src"

# Masks every value of the env files (length 8 and more, the site's own name excepted) in what a remote command
# printed. The values are read from the files, never passed as arguments.
redact() {
  python3 -c '
import sys
secrets = set()
for path in sys.argv[1:]:
    try:
        lines = open(path, encoding="utf-8").read().splitlines()
    except OSError:
        continue
    for line in lines:
        key, _, value = line.partition("=")
        if key not in ("SITE_URL", "DJANGO_ALLOWED_HOSTS") and len(value) >= 8:
            secrets.add(value)
ordered = sorted(secrets, key=len, reverse=True)
sys.stdin.reconfigure(errors="replace")
for line in sys.stdin:
    for value in ordered:
        line = line.replace(value, "[MASQUÉ]")
    sys.stdout.write(line)
    sys.stdout.flush()
' "$STAGE/web.env" "$STAGE/scheduler.env" "$STAGE/litestream.env"
}

remote_prepare() {
  info "Dossiers de l’app sur l’hôte (data/ n’est créé que s’il manque)..."
  nas "sudo -n true" || die "sudo sans mot de passe indisponible pour $TRI_DEPLOY_USER sur l’hôte."
  nas "sudo sh -c 'set -e; mkdir -p $REMOTE_DIR/env; chown root:root $REMOTE_DIR $REMOTE_DIR/env; chmod 750 $REMOTE_DIR; chmod 700 $REMOTE_DIR/env; if [ ! -d $REMOTE_DIR/data ]; then mkdir $REMOTE_DIR/data; chown 1000:1000 $REMOTE_DIR/data; chmod 750 $REMOTE_DIR/data; fi'"
}

remote_code() {
  info "Dépôt du code dans $REMOTE_SRC (vidé avant)..."
  nas "sudo rm -rf -- $REMOTE_SRC && sudo mkdir -m 755 $REMOTE_SRC"
  nas_in "sudo tar -xzf - -C $REMOTE_SRC --no-same-owner" < "$STAGE/src.tgz"
}

remote_env() {
  local name modes
  info "Dépôt des fichiers d’environnement (600 root, par l’entrée standard de sudo tee)..."
  for name in web scheduler litestream; do
    nas_in "umask 077 && sudo tee $REMOTE_DIR/env/$name.env >/dev/null && sudo chown root:root $REMOTE_DIR/env/$name.env && sudo chmod 600 $REMOTE_DIR/env/$name.env" < "$STAGE/$name.env"
  done
  nas "sudo install -o root -g root -m 644 $REMOTE_SRC/docker/litestream.yml $REMOTE_DIR/litestream.yml"
  modes="$(nas "sudo stat -c '%a %U:%G' $REMOTE_DIR/env/web.env $REMOTE_DIR/env/scheduler.env $REMOTE_DIR/env/litestream.env")"
  [ "$modes" = "$(printf '600 root:root\n600 root:root\n600 root:root')" ] || die "les fichiers d’environnement de l’hôte n’ont pas les droits 600 root:root."
}

remote_image() {
  info "Construction de l’image $IMAGE sur l’hôte..."
  nas "sudo docker build -t $IMAGE $REMOTE_SRC" 2>&1 | redact || die "la construction de l’image a échoué (l’app n’a pas été touchée)."
  info "Tests de l’image (sans réseau, sans volume, sans variables)..."
  nas "sudo docker run --rm --network none --cap-drop ALL --security-opt no-new-privileges:true --entrypoint python $IMAGE manage.py test onboarding.test_cv_extract" 2>&1 | redact \
    || die "les tests de l’extracteur de CV échouent dans l’image : arrêt avant de toucher l’app."
}

remote_app() {
  local exists tmp_remote log_remote action
  exists="$(nas "midclt call app.query '[[\"id\",\"=\",\"$APP_NAME\"]]'" | python3 -c 'import json,sys; print(len(json.load(sys.stdin)))')"
  tmp_remote="$(nas "umask 077 && mktemp /tmp/tri-offres-app.XXXXXX")"
  log_remote="$(nas "umask 077 && mktemp /tmp/tri-offres-app-log.XXXXXX")"
  [[ $tmp_remote =~ ^/tmp/tri-offres-app\.[A-Za-z0-9]+$ && $log_remote =~ ^/tmp/tri-offres-app-log\.[A-Za-z0-9]+$ ]] \
    || die "mktemp distant : chemin inattendu."
  if [ "$exists" = "0" ]; then
    action="create"
    python3 -c '
import json, sys
print(json.dumps({"app_name": sys.argv[1], "custom_app": True, "custom_compose_config_string": sys.stdin.read()}))
' "$APP_NAME" < "$STAGE/compose.yaml" | nas_in "umask 077 && cat > $tmp_remote"
    nas "midclt call -j app.create \"\$(cat $tmp_remote)\" > $log_remote 2>&1; rc=\$?; rm -f $tmp_remote; exit \$rc" \
      || action="failed-create"
  else
    action="update"
    python3 -c '
import json, sys
print(json.dumps({"custom_compose_config_string": sys.stdin.read()}))
' < "$STAGE/compose.yaml" | nas_in "umask 077 && cat > $tmp_remote"
    nas "midclt call -j app.update $APP_NAME \"\$(cat $tmp_remote)\" > $log_remote 2>&1; rc=\$?; rm -f $tmp_remote; exit \$rc" \
      || action="failed-update"
  fi
  case "$action" in
    failed-*)
      echo "ERREUR : app.${action#failed-} a échoué, détail (valeurs secrètes masquées) :" >&2
      nas "cat $log_remote; rm -f $log_remote" | redact >&2 || true
      exit 1 ;;
  esac
  nas "rm -f $log_remote"
  info "App « $APP_NAME » : app.$action réussi."
}

confirm() {
  [ -t 0 ] || return 0
  local answer
  printf '%s [o/N] ' "$1"
  read -r answer
  case "$answer" in o|O|oui|OUI) ;; *) die "abandon." ;; esac
}

# --- Run ---
info "scripts/deploy.sh, mode : $MODE, moteur : $ENGINE, commit $COMMIT."
if [ "$MODE" != "build-only" ]; then
  build_env_files
  verify_env_files
  print_kept_names
fi
build_archive

if [ "$MODE" != "build-only" ]; then
  render_compose > "$STAGE/compose.yaml"
fi

if [ "$MODE" = "dry-run" ]; then
  write_compose_out
  if [ -n "$KEEP_DIR" ]; then
    for name in web scheduler litestream; do cp "$STAGE/$name.env" "$KEEP_DIR/$name.env"; done
  fi
  info "Compose écrit dans $COMPOSE_OUT (à valider : docker compose -f $COMPOSE_OUT config)."
  info "Dry-run terminé : aucune connexion n’a été ouverte, rien n’a été déposé. Les fichiers d’environnement sont effacés."
  print_next_steps
  exit 0
fi

if [ "$MODE" = "deploy" ]; then
  confirm "Déployer le commit $COMMIT sur $TRI_DEPLOY_HOST (site https://$TRI_DEPLOY_DOMAIN) ?"
fi
remote_prepare
remote_code
remote_image
if [ "$MODE" = "build-only" ]; then
  info "Image $IMAGE construite et testée. L’app n’a pas été touchée."
  exit 0
fi
remote_env
remote_app
info "Déployé : $IMAGE."
print_next_steps
