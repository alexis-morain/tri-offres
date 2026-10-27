# Sources

tri-offres reads its offers from 22 French sources, listed in `jobs/actor_sources.py` and created in the database by
migrations `jobs/migrations/0005_actor_sources.py` and `0009_more_actor_sources.py`. They are collected by an external
program that is not part of this repository (see the README, "The collector is not included").

| Source | Key | Kind | Domains |
|---|---|---|---|
| [Welcome to the Jungle](wttj.md) | `wttj` | Job board | everyone |
| [HelloWork](hellowork.md) | `hellowork` | Job board | everyone |
| [Meteojob](meteojob.md) | `meteojob` | Job board | everyone |
| [Free-Work, emplois](free_work_jobs.md) | `free_work_jobs` | Job board | `M18` |
| [Free-Work, missions](free_work.md) | `free_work` | Freelance platform | `M18` |
| [Collective](collective.md) | `collective` | Freelance platform | everyone |
| [Codeur.com](codeur.md) | `codeur` | Freelance platform | everyone |
| [Taleez](taleez.md) | `taleez` | Recruiting software: career pages that employers host on it | everyone |
| [Teamtailor](teamtailor.md) | `teamtailor` | Recruiting software: career pages that employers host on it | everyone |
| [ProfilCulture](profilculture.md) | `profilculture` | Sector site (culture and book trade) | `E`, `K`, `L` |
| [Livres Hebdo](livreshebdo.md) | `livreshebdo` | Sector site (culture and book trade) | `E`, `K`, `L` |
| [Agences régionales du livre](agences_livre.md) | `agences_livre` | Sector site (culture and book trade) | `E`, `K`, `L` |
| [EURES (France Travail)](eures.md) | `eures` | Public sector site | everyone |
| [Choisir le service public](choisir_service_public.md) | `choisir_service_public` | Public sector site | everyone |
| [Emploi territorial](emploi_territorial.md) | `emploi_territorial` | Public sector site | everyone |
| [Service Civique](service_civique.md) | `service_civique` | Public sector site | everyone |
| [FHF, Fédération hospitalière de France](fhf.md) | `fhf` | Public sector site | `J` |
| [DigitalRecruiters](digitalrecruiters.md) | `digitalrecruiters` | Recruiting software: career pages that employers host on it | everyone |
| [Flatchr](flatchr.md) | `flatchr` | Recruiting software: career pages that employers host on it | everyone |
| [Breezy HR](breezy.md) | `breezy` | Recruiting software: career pages that employers host on it | everyone |
| [JOIN](join.md) | `join` | Recruiting software: career pages that employers host on it | everyone |
| [Softy](softy.md) | `softy` | Recruiting software: career pages that employers host on it | everyone |

"everyone" means a generalist source: each of its offers goes to the domain of its ROME code when the title gives one,
otherwise to every person. Domain codes come from `onboarding/rome.py`.

Each site has its own terms of use. Anyone running an instance is responsible for checking them and for answering a
site's request; [docs/retrait-source.md](../retrait-source.md) describes how a source is cut and its offers deleted.

A new source gets its sheet here in the same change that adds it to `jobs/actor_sources.py`.
