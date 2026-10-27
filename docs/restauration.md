# Restauration de la base

Ce document sert à l’opérateur, le jour où la base est perdue ou abîmée, et à l’essai à blanc d’avant la bêta (puis
une fois par trimestre). Il ne contient aucune valeur secrète : les espaces réservés entre chevrons
(`<dossier de l’app>`, `<hôte>`, `<étiquette>`) se remplacent à la main. Les clés R2 restent dans
`<dossier de l’app>/env/litestream.env`, jamais dans une commande ni dans ce dépôt.

## Ce qui est sauvegardé, et ce qui ne l’est pas

- Litestream (`docker/litestream.yml`, service `litestream` du profil `backup`) réplique `tri-offres.sqlite3` en
  continu vers Cloudflare R2, dans la juridiction UE. Un instantané par jour, gardé jusqu’à 696 h : le plus vieux
  point de restauration a donc 30 jours au plus (`config/backups.py`, `BACKUP_DAYS`, lue par la page de
  confidentialité).
- Pas de chiffrement côté client (la 0.5 n’a pas `age`) : R2 chiffre au repos, mais Cloudflare détient une copie
  complète de la base. La page `/confidentialite/` le dit.
- Le jeton R2 de Litestream lui est propre, limité à ce seul bucket, avec le droit de lecture, d’écriture **et de
  suppression** (Litestream efface ce qui sort de la rétention). Aucun autre service ne l’utilise.
- Le journal des comptes (`journal-comptes.txt`, à côté de la base) n’est **pas** répliqué : il doit survivre à
  une restauration de la base. Il se perd avec le disque (voir « Si le disque est perdu »).
- Les sessions Django sont dans la base : une base restaurée ramène celles d’il y a quelques heures.

## 0. Mesurer la rétention réelle (à chaque essai)

La rétention affichée au public est de 30 jours. Vérifier qu’elle est tenue :

```bash
sudo docker run --rm --env-file <dossier de l’app>/env/litestream.env \
  -v <dossier de l’app>/litestream.yml:/etc/litestream.yml:ro \
  litestream/litestream:0.5.17 ltx -config /etc/litestream.yml -level all /data/tri-offres.sqlite3
```

La colonne `created` du plus ancien point doit dater de moins de 30 jours. Sinon, baisser `snapshot.retention`
dans `docker/litestream.yml` (et le test `config/test_backups.py` qui lie les deux), redéployer, et noter l’écart.
Le grain de restauration dépend des fichiers LTX encore gardés : `-timestamp` choisit le dernier point au plus tôt
à cet instant.

## 1. Essai à blanc (ne touche pas la base en service)

Dans un dossier temporaire privé, effacé à la fin :

```bash
ESSAI=$(mktemp -d) && chmod 700 "$ESSAI"
sudo docker run --rm --env-file <dossier de l’app>/env/litestream.env \
  -v <dossier de l’app>/litestream.yml:/etc/litestream.yml:ro -v "$ESSAI":/essai \
  litestream/litestream:0.5.17 restore -config /etc/litestream.yml -integrity-check quick \
  -o /essai/essai.sqlite3 /data/tri-offres.sqlite3
sqlite3 "$ESSAI/essai.sqlite3" "PRAGMA integrity_check; SELECT count(*) FROM accounts_user;"
```

Variantes : `-timestamp 2026-10-20T06:00:00Z` (UTC, borne exclusive) pour une date précise, `-dry-run` pour voir
ce qui serait lu. Litestream refuse d’écraser un fichier non vide (`-force` l’y oblige : ne pas l’utiliser ici).
Le résultat attendu : `ok`, puis un nombre de comptes plausible. Puis :

```bash
rm -r "$ESSAI"
```

## 2. Restauration réelle

À faire app arrêtée **en entier** (web, planificateur, litestream) : le planificateur enverrait des mails sur une
base qui n’est pas encore remise à jour (suppressions et désinscriptions à rejouer d’abord).

1. Arrêter l’app : `midclt call app.stop tri-offres` (sur `<hôte>`). Noter l’heure.
2. Mettre la base actuelle de côté, avec ses fichiers annexes :
   ```bash
   cd <dossier de l’app>/data && mkdir -m 700 ../avant-restauration
   mv tri-offres.sqlite3 tri-offres.sqlite3-wal tri-offres.sqlite3-shm ../avant-restauration/ 2>/dev/null
   ```
   Ne pas effacer ce dossier avant d’avoir terminé : c’est le retour arrière.
3. Restaurer vers un fichier neuf (point le plus récent, ou `-timestamp` pour avant l’incident) :
   ```bash
   sudo docker run --rm --user 1000:1000 --env-file <dossier de l’app>/env/litestream.env \
     -v <dossier de l’app>/litestream.yml:/etc/litestream.yml:ro -v <dossier de l’app>/data:/data \
     litestream/litestream:0.5.17 restore -config /etc/litestream.yml -integrity-check quick \
     -o /data/tri-offres.sqlite3 /data/tri-offres.sqlite3
   ```
   Le fichier appartient à l’uid 1000 (`ls -l`). Noter l’instant du point restauré (`ltx`, colonne `created`, ou
   l’horodatage demandé), en UTC : c’est le `--depuis` de l’étape 5.
4. Repartir d’un état Litestream propre pour cette base. Les fichiers locaux de suivi (dossier caché
   `.tri-offres.sqlite3-litestream` dans `data/`) décrivent l’ancienne base ; sans réinitialisation, la
   réplication reprendrait à faux.
   ```bash
   sudo docker run --rm --user 1000:1000 -v <dossier de l’app>/litestream.yml:/etc/litestream.yml:ro \
     -v <dossier de l’app>/data:/data litestream/litestream:0.5.17 reset -config /etc/litestream.yml \
     /data/tri-offres.sqlite3
   ```
   Le prochain démarrage de Litestream prendra un instantané neuf. Contrepartie : les restaurations à une date
   *antérieure* à la réinitialisation sont ensuite perdues pour ce fil de réplication. Cette étape a été jouée sur
   un dépôt local (`file://`), pas contre R2 : le dire à la prochaine personne qui relit ce document, et la
   revérifier à l’essai à blanc suivant.
5. Rejouer le journal des comptes, **avant** de redémarrer le planificateur. D’abord à blanc :
   ```bash
   sudo docker run --rm --user 1000:1000 --env-file <dossier de l’app>/env/web.env \
     -v <dossier de l’app>/data:/data tri-offres:<étiquette> \
     python manage.py rejouer_journal --depuis 2026-10-20T05:50 --dry-run
   ```
   Viser environ dix minutes avant l’instant du point restauré : une ligne rejouée deux fois ne change rien. Une
   marge large est à éviter, car une adresse supprimée puis réinscrite entre-temps serait supprimée de nouveau.
   Relire la sortie (elle ne nomme les comptes que par leur clé numérique), puis relancer sans `--dry-run`.
   Les comptes de l’équipe ne sont jamais touchés ; aucun mail ne part ; la commande est rejouable.
6. Redémarrer : `midclt call app.start tri-offres`. Contrôles : `/healthz` répond 200, la connexion par code
   fonctionne, le tableau de bord de l’admin (`/admin/tableau/`) montre des inscriptions et un envoi récents, et
   `docker logs` du service `litestream` ne montre aucune erreur de synchronisation.
7. Si le point restauré date d’avant une suppression que le journal ne connaît pas (voir ci-dessous), supprimer de
   nouveau le compte à la main avant de rouvrir, et garder une trace de l’incident.
8. Quand tout est vérifié : `rm -r <dossier de l’app>/avant-restauration`.

### Ce qu’une restauration fait revenir

Un compte supprimé, une désinscription ou un réabonnement faits après le point restauré reviennent avec la base.
Le journal des comptes (`accounts/journal.py`) garde pour cela une ligne par action : date UTC, genre
(`suppression`, `desinscription`, `reabonnement`) et empreinte HMAC-SHA256 de l’adresse, jamais l’adresse. Elle est
écrite sur le serveur, hors base, et purgée à 31 jours par la nuit (`purge_journal`).

Limites à connaître :

- Une suppression faite depuis l’admin Django ne passe pas par le journal : seules les trois actions du site
  (supprimer mon compte, se désinscrire, se réabonner) y sont notées. Les rejouer à la main.
- L’empreinte est calculée avec `DJANGO_SECRET_KEY`. Si la clé a changé depuis (rotation, ou clé passée en
  `DJANGO_SECRET_KEY_FALLBACKS`), les anciennes lignes ne correspondent plus à aucune adresse et ne servent à rien.
  Changer la clé impose donc de ne pas restaurer dans les 31 jours qui suivent, ou de rejouer à la main.
- Si le disque est perdu, le journal l’est aussi. Reconstituer alors à la main les suppressions et désinscriptions
  des 30 derniers jours : les mails de confirmation envoyés (Resend) en gardent la trace.

## Se déconnecter partout

Après une restauration suspecte, une fuite de la clé ou un doute sur un cookie, vider les sessions (tout le monde
devra se reconnecter par code) :

```bash
sudo docker run --rm --user 1000:1000 --env-file <dossier de l’app>/env/web.env \
  -v <dossier de l’app>/data:/data tri-offres:<étiquette> \
  python manage.py shell -c "from django.contrib.sessions.models import Session; print(Session.objects.all().delete())"
```

Si la clé `DJANGO_SECRET_KEY` a fuité, la changer aussi (les sessions et les liens signés en dépendent), puis lire
la limite sur le journal des comptes ci-dessus.

## Repères

- Image Litestream épinglée par empreinte dans `docker-compose.yml` (version 0.5.17). Les options citées ici
  (`restore -o`, `-timestamp`, `-dry-run`, `-integrity-check`, `ltx -level all`, `reset`) sont celles de cette
  version : relire leur page de référence avant de monter la version.
- Une restauration n’a été jouée de bout en bout que sur un dépôt local. Le premier essai à blanc contre R2 reste à
  faire avant la bêta.
