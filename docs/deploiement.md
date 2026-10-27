# Déploiement sur l’hôte

Ce document sert à l’opérateur, du premier déploiement aux mises à jour. Il ne contient aucune valeur réelle ni aucun
secret : les espaces réservés entre chevrons (`<domaine>`, `<ip-lan>`, `<port>`, `<hôte>`,
`<dossier de l’app>`, `<équipe>`) se remplacent à la main, et les vraies valeurs restent dans votre shell et
dans le `.env`. Le script `scripts/deploy.sh` ne s’est encore jamais exécuté contre le
hôte : la première fois, lire sa sortie ligne à ligne.

Chaque étape dit qui la fait, la commande, et le contrôle attendu. L’ordre compte : les étapes 1 à 6 préparent le
`.env`, la 7 déploie, les suivantes ouvrent le site.

Règle pour le `.env` : un agent n’y écrit jamais. L’opérateur y ajoute chaque ligne lui-même (ou avec la commande
donnée ci-dessous, chemin complet). Pour vérifier ce qui est posé sans afficher une seule
valeur :

```bash
cut -d= -f1 <chemin du dépôt>/.env | sort
```
Variante de développement : `export TRI_DEPLOY_ENGINE=oauth` avant l’étape 7 déploie sur le jeton d’un abonnement
Claude pour les seules adresses de `DEV_EMAILS` (pas de clé API). Aucune autre personne ne peut alors utiliser le
site, et la base ainsi écrite ne passe jamais en `api` : en production, repartir d’une base neuve.
site, et la base ainsi écrite ne passe jamais en `api` : la bêta externe repart d'une base neuve.

## 1. Avant tout : clé Anthropic, base neuve, moteur `api`

Qui : l’opérateur.

1. Dans la console Anthropic, créer une clé API réservée à l’hôte et lui poser un plafond de dépense mensuel.
2. L’ajouter au `.env` (le chemin est celui du dépôt, la valeur se colle au clavier, jamais dans une commande
   gardée dans l’historique du shell) :
   ```bash
   read -rs CLE && printf 'ANTHROPIC_API_KEY=%s\n' "$CLE" >> <chemin du dépôt>/.env; unset CLE
   ```
3. La base de l’hôte part neuve. Ne jamais y copier une base locale : une base où le moteur `oauth` a écrit des
   résumés ne passe jamais en production (règle du jeton d’un abonnement Claude : il ne sert jamais d’autres personnes). L’hôte tourne en `CLAUDE_ENGINE=api`,
   rien d’autre : le script le pose lui-même et ignore ce que dit le `.env` (il prévient s’il diffère).

Contrôle : `cut -d= -f1 <chemin du dépôt>/.env | grep -x ANTHROPIC_API_KEY` affiche le nom.

## 2. R2 : le bucket de sauvegarde

Qui : l’opérateur, dans le tableau de bord Cloudflare.

1. Créer un bucket de sauvegarde (`<nom du bucket>`) avec la **juridiction UE**. Elle se choisit à la création et ne
   se change plus : un bucket créé hors UE est à supprimer et à refaire.
2. Règle de cycle de vie du bucket : suppression des objets au bout de 31 jours (la rétention annoncée au public
   est de 30 jours, voir `docs/restauration.md`).
3. Créer un jeton API R2 limité à ce seul bucket, avec le droit de lecture et d’écriture d’objets **et de
   suppression** (Litestream efface ce qui sort de la rétention ; sans suppression, le bucket gonfle jusqu’à ce que
   seule la règle de cycle de vie le vide).
4. Noter les quatre variables dans le `.env` : `LITESTREAM_ACCESS_KEY_ID`, `LITESTREAM_SECRET_ACCESS_KEY`,
   `LITESTREAM_R2_BUCKET` (le nom du bucket) et `LITESTREAM_R2_ENDPOINT` (l’URL `https://…` de la juridiction UE
   donnée par Cloudflare). Elles vont dans `litestream.env` seulement, jamais dans l’appli.

Contrôle : les quatre noms sont dans la liste de clés du `.env`.

## 3. healthchecks.io : quatre checks

Qui : l’opérateur, sur healthchecks.io.

1. Créer trois checks, fuseau `Europe/Paris`, avec ces slugs exacts (le code ajoute le slug à la clé de ping) :

   | Slug | Planification (cron) | Délai de grâce |
   |---|---|---|
   | `tri-offres-nuit` | `0 4 * * *` | 3 h |
   | `tri-offres-matin` | `0 7 * * 1-5` | 1 h |
   | `tri-offres-soir` | `0 18 * * *` | 1 h |

2. Créer un quatrième check pour le battement de Litestream (nom libre, par exemple `tri-offres-litestream`),
   période d’un jour au plus. Copier son URL de ping complète.
3. Dans le `.env` : `HEALTHCHECKS_PING_URL` vaut l’adresse de ping du projet **sans slug**
   (`https://<hôte de ping>/<clé de ping>`), et `LITESTREAM_HEARTBEAT_URL` vaut l’URL complète du quatrième check.
   Ces deux adresses sont des secrets : celui qui les connaît peut envoyer de faux pings.
4. Brancher la notification du projet (mail ou autre) sur l’opérateur, pour que le silence d’un check arrive quelque part.

Contrôle : après le premier déploiement et le premier créneau, les checks passent au vert dans healthchecks.io.
Sans ces deux variables, le script avertit et déploie quand même : rien ne préviendrait alors d’un arrêt.

## 4. Proxy HTTP pour la collecte

Qui : l’opérateur, sur l’hôte. La collecte sort par un proxy HTTP (par exemple celui d’un conteneur VPN), jamais par
l’adresse de l’hôte (`SCRAPER_PROXY_REQUIRED=1` est posé par le script : sans proxy, `collecte` refuse de tourner).

1. Mettre en service un proxy HTTP derrière le VPN de votre choix, joignable depuis le réseau Docker ou le LAN de
   l’hôte, avec un utilisateur et un mot de passe tirés au hasard.
2. Ajouter dans le `.env` de tri-offres :
   ```bash
   read -rs URL && printf 'SCRAPER_PROXY_URL=%s\n' "$URL" >> <chemin du dépôt>/.env; unset URL
   ```
   avec une adresse de la forme `http://<utilisateur du proxy>:<mot de passe du proxy>@<hôte du proxy>:<port>`.
   Le script refuse `$`, `#` précédé d’une espace, barre oblique inverse et retours à la ligne dans les valeurs :
   tirer un mot de passe alphanumérique.

Contrôle : l’adresse de sortie vue par le proxy n’est pas celle de l’hôte. À lancer après le déploiement (étape 7),
depuis le conteneur du planificateur, qui porte déjà l’adresse du proxy dans son environnement (rien de secret dans
la ligne de commande) :
```bash
ssh <utilisateur>@<hôte> "sudo docker exec tri-offres-scheduler python -c \"import os, urllib.request as u; p = os.environ['SCRAPER_PROXY_URL']; print(u.build_opener(u.ProxyHandler({'http': p, 'https': p})).open('https://ifconfig.me', timeout=20).read().decode())\""
```
doit afficher l’adresse de sortie du VPN, différente de celle de l’hôte (`curl -s https://ifconfig.me` depuis l’hôte).

## 5. Les autres variables du `.env`

Qui : l’opérateur. Chaque ligne s’ajoute avec `read -rs` comme ci-dessus, ou à la main dans un éditeur ouvert sur le
chemin complet du `.env`.

| Variable | Rôle | Remarque |
|---|---|---|
| `DJANGO_SECRET_KEY` | clé du site | propre à l’hôte, 50 caractères au moins, sans `$` ; ne pas reprendre celle d’un poste de développement |
| `RESEND_API_KEY` | envoi des mails | clé existante ou clé dédiée |
| `DEFAULT_FROM_EMAIL` | expéditeur des mails, par exemple `tri-offres <bonjour@<domaine d’envoi>>` | obligatoire : le site refuse de démarrer sans lui dès que `RESEND_API_KEY` est posée |
| `MAIL_REPLY_TO` | adresse de réponse et contact des pages légales | obligatoire, comme ci-dessus |
| `LEGAL_NAME`, `LEGAL_ADDRESS`, `LEGAL_SIRET` | éditeur des mentions légales | `LEGAL_NAME` obligatoire, comme ci-dessus |
| `ADMIN_ACCESS_TEAM_DOMAIN` | domaine d’équipe Cloudflare Access (`<équipe>.cloudflareaccess.com`) | étape 8 |
| `ADMIN_ACCESS_AUD` | valeur AUD de l’application Access | étape 8 |
| `HSTS_SECONDS` | durée HSTS | `3600` d’abord, voir étape 8 |
| `ALERT_EMAIL` | adresse qui reçoit les alertes | facultative |

Pour tirer une clé de site propre :

```bash
.venv/bin/python manage.py shell -c "from django.core.management.utils import get_random_secret_key as g; print(g())"
```

Si elle contient un `$`, recommencer : le script le refuse (Compose l’interpréterait).

Contrôle : `scripts/deploy.sh --dry-run` (étape 7) ne se plaint d’aucune clé
manquante.

## 6. Resend : suivi des clics coupé

Qui : l’opérateur, dans le tableau de bord Resend, sur le domaine d’envoi `<domaine d’envoi>`.

Désactiver le suivi des clics (« Click tracking »). Sinon Resend réécrit les liens des mails et le jeton de
désinscription en un clic casse (rappel de l’étape 7 du projet). Le suivi des ouvertures peut rester coupé aussi.

Contrôle : envoyer un mail à soi-même et vérifier que le lien de désinscription pointe vers `<domaine>` et non vers
un domaine de Resend.

## 7. Déployer : à sec, puis pour de vrai

Qui : l’opérateur, depuis le poste de travail, dans le dépôt, arbre git propre (le script déploie `HEAD`, pas le dossier de travail).

Prérequis : la clé d’hôte de l’hôte est déjà connue (`ssh <utilisateur>@<hôte>` une fois à la main, en comparant
l’empreinte) ; le script refuse sinon (`StrictHostKeyChecking=yes`, aucune confiance au premier contact).
L’utilisateur SSH doit avoir `sudo` sans mot de passe sur l’hôte.

Exporter les sept variables dans le shell (pas dans le dépôt, pas dans le `.env`) :

```bash
export TRI_DEPLOY_HOST=<hôte>
export TRI_DEPLOY_USER=<utilisateur>
export TRI_DEPLOY_KEY=<chemin de la clé SSH>
export TRI_DEPLOY_LAN_IP=<ip-lan>
export TRI_DEPLOY_PORT=<port>
export TRI_DEPLOY_DOMAIN=<domaine>
export TRI_DEPLOY_DIR=<dossier de l’app>
```

`<ip-lan>` est l’adresse LAN de l’hôte, jamais `0.0.0.0` : le port n’est publié que sur le LAN, pour `cloudflared`.
`<dossier de l’app>` est un chemin absolu d’au moins deux niveaux, sans espace.

1. À sec : aucune connexion, rien n’est posé.
   ```bash
   scripts/deploy.sh --dry-run
   ```
   Contrôle : sortie sans erreur, noms des clés retenues par fichier (`web.env`, `scheduler.env`,
   `litestream.env`), jamais une valeur ; aucun `CLAUDE_CODE_OAUTH_TOKEN` ni `APIFY_*` ; le nombre d’entrées de
   l’archive du code. Le compose produit est dans `/tmp/tri-offres-compose.yaml` : le relire (aucun secret, port publié sur
   `<ip-lan>:<port>`, trois services).
   Si le script écrit « SANS l’actor » : `vendor/actor` manque. Lancer `scripts/fetch_actor.sh`, puis relancer. Sans
   l’actor, la collecte échouerait chaque nuit.
2. Construire sans toucher à l’app (optionnel mais conseillé la première fois) :
   ```bash
   scripts/deploy.sh --build-only
   ```
   Contrôle : l’image `tri-offres:<commit court>` est construite sur l’hôte, puis le test d’extraction du CV
   (`onboarding.test_cv_extract`, sans réseau ni volume) passe dans l’image. Les tests « conteneur seulement » sautés
   sur macOS tournent donc ici.
3. Déployer :
   ```bash
   scripts/deploy.sh
   ```
   Le script demande une confirmation (`o`) quand il tourne dans un terminal. Il pose le code, les fichiers
   d’environnement (600, propriétaire root), construit l’image, lance les tests, puis crée ou met à jour l’application
   personnalisée `tri-offres`.
   Contrôle : la fin de la sortie liste les commandes de vérification. Les lancer : journaux des trois conteneurs
   (`tri-offres-web`, `tri-offres-scheduler`, `tri-offres-litestream`) sans erreur, et la sonde `/healthz` répond
   « OK ».

Limites mémoire du compose (`web` 1 Go, planificateur 2 Go, Litestream 256 Mo) : ce sont des estimations. Les
mesurer sur l’hôte après la première nuit (`sudo docker stats --no-stream`) et ajuster `WEB_MEM`, `SCHEDULER_MEM` et
`LITESTREAM_MEM` en tête de `scripts/deploy.sh`. Le système de fichiers des conteneurs n’est pas en lecture seule :
cela n’a pas été vérifié.

## 8. Tunnel Cloudflare, Access devant l’admin, HSTS

Qui : l’opérateur, dans Cloudflare Zero Trust.

1. Route de tunnel (tunnel déjà en service) : nom d’hôte public `<domaine>` vers `http://<ip-lan>:<port>`.
2. **Cloudflare Access devant l’admin.** Créer une application Access de type « auto-hébergée » sur
   `<domaine>/admin*`, avec une politique « Autoriser » limitée à l’adresse de l’administrateur. Noter le domaine
   d’équipe (`<équipe>.cloudflareaccess.com`) et la valeur AUD de l’application, puis les poser dans le `.env`
   (`ADMIN_ACCESS_TEAM_DOMAIN`, `ADMIN_ACCESS_AUD`), et relancer `scripts/deploy.sh`. Sans ces deux
   variables, l’admin reste fermée (réponse 404) : c’est voulu : le site n’ouvre jamais l’admin sans Access.
3. **HSTS.** Le premier déploiement tourne avec `HSTS_SECONDS=3600` (une heure). Après une semaine sans incident
   (HTTPS partout, aucun mail ni lien en `http://`), passer à `HSTS_SECONDS=31536000` (un an),
   redéployer. Ne pas viser un an tout de suite : un navigateur qui a vu l’en-tête s’y tient.

Contrôles :
- `https://<domaine>/` charge le site, et `/healthz` répond.
- `https://<domaine>/admin/` affiche d’abord la page de connexion Access, puis celle de Django ; depuis un autre
  navigateur non connecté à Access, la page n’est pas servie.
- `curl -sI https://<domaine>/ | grep -i strict-transport` montre la durée attendue.

## 9. Mises à jour automatiques de l’hôte : jamais pour cette application

Qui : l’opérateur.

Si l’hôte met ses applications à jour tout seul, exclure `tri-offres` : l’image est construite localement
(`pull_policy: never`), une mise à jour automatique ne la reconstruirait pas et pourrait remplacer le compose par un
état que le script n’a pas écrit.

## 10. Premier compte staff, essai de restauration, premier mail

Qui : l’opérateur.

1. Créer le premier compte staff dans le conteneur du site :
   ```bash
   ssh -t <utilisateur>@<hôte> 'sudo docker exec -it tri-offres-web python manage.py createsuperuser'
   ```
   Contrôle : connexion à `https://<domaine>/admin/` (après Access), puis le tableau de bord
   `/admin/tableau/` s’affiche.
2. Essai de restauration à blanc contre R2 : suivre `docs/restauration.md`, sections 0 et 1 (ne touche pas la base
   en service). Contrôle : `PRAGMA integrity_check` répond `ok` et la rétention mesurée est inférieure à 30 jours.
   C’est le premier essai de bout en bout contre R2 : en garder la trace.
3. Se déconnecter partout au besoin : voir « Se déconnecter partout » dans `docs/restauration.md`.
4. Attendre le créneau de 7 h d’un jour ouvré. Critère de fin de l’étape 10 : l’opérateur reçoit le premier mail du
   matin, et les trois checks (nuit, matin, soir) et le battement de Litestream sont verts dans healthchecks.io.

## 11. Mettre à jour, revenir en arrière

Qui : l’opérateur.

- **Mise à jour** : committer, puis relancer `scripts/deploy.sh --dry-run` et `scripts/deploy.sh`. Le code est
  remplacé (`src/` est vidé puis recréé), `data/` n’est jamais touché, une nouvelle image `tri-offres:<commit court>`
  est construite et l’ancienne reste sur l’hôte. Les migrations de la base s’appliquent au démarrage.
- **Retour arrière** : `git checkout <ancien commit>` (arbre propre), puis `scripts/deploy.sh`. L’image de l’ancien
  commit reste sur l’hôte, mais le script reconstruit tout de même celle du commit sorti. Les migrations sont à sens
  unique : si la dernière a changé le schéma, le code ancien peut ne plus lire la base. Dans ce cas, restaurer la
  base (`docs/restauration.md`, section 2) avant de redéployer. Penser à revenir ensuite sur `main`.
- **Déploiement qui échoue** : les tests de l’image et la construction passent avant tout changement de l’app ; un
  échec à cet endroit laisse donc l’app telle quelle. Un échec de `app.update` lui-même n’a pas été éprouvé : lire
  les journaux XX avant de relancer.
- **Changement d’une variable du `.env`** : modifier la ligne, relancer `scripts/deploy.sh` (les
  fichiers d’environnement sont reconstruits à chaque passage).
- **Clé de site changée** : lire la limite sur le journal des comptes dans `docs/restauration.md`.
