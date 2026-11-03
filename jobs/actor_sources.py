"""Sources of the actor collected for France (plan of step 4, section 3 bis).

Migrations 0005, 0009, 0012, 0013 and 0014 copy them."""
from typing import NamedTuple


class ActorSource(NamedTuple):
    key: str  # key of the source in the actor
    name: str
    url: str
    domains: list  # codes of onboarding/rome.DOMAINS; [] means every person


# Asked for `ats`, the actor writes the platform as the source of each offer: collect files them under `ats`.
ATS_PLATFORMS = frozenset({"greenhouse", "ashby", "lever", "workday", "smartrecruiters", "workable", "personio",
                           "rippling", "recruitee"})

ACTOR_SOURCES = [
    ActorSource("wttj", "Welcome to the Jungle", "https://www.welcometothejungle.com", []),
    ActorSource("hellowork", "HelloWork", "https://www.hellowork.com", []),
    ActorSource("meteojob", "Meteojob", "https://www.meteojob.com", []),
    ActorSource("free_work_jobs", "Free-Work, emplois", "https://www.free-work.com", ["M18"]),
    ActorSource("free_work", "Free-Work, missions", "https://www.free-work.com", ["M18"]),
    ActorSource("collective", "Collective", "https://www.collective.work", []),
    ActorSource("codeur", "Codeur.com", "https://www.codeur.com", []),
    ActorSource("taleez", "Taleez", "https://www.taleez.com", []),
    ActorSource("teamtailor", "Teamtailor", "https://www.teamtailor.com", []),
    ActorSource("profilculture", "ProfilCulture", "https://www.profilculture.com", ["E", "K", "L"]),
    ActorSource("livreshebdo", "Livres Hebdo", "https://www.livreshebdo.fr", ["E", "K", "L"]),
    ActorSource("agences_livre", "Agences régionales du livre", "", ["E", "K", "L"]),
    ActorSource("eures", "EURES (France Travail)", "https://eures.europa.eu", []),
    ActorSource("choisir_service_public", "Choisir le service public", "https://choisirleservicepublic.gouv.fr", []),
    ActorSource("emploi_territorial", "Emploi territorial", "https://www.emploi-territorial.fr", []),
    ActorSource("service_civique", "Service Civique", "https://www.service-civique.gouv.fr", []),
    # 18/10, migration 0009 (the design plan): FHF and the French ATS of the second wave.
    ActorSource("fhf", "FHF, Fédération hospitalière de France", "https://emploi.fhf.fr", ["J"]),
    ActorSource("digitalrecruiters", "DigitalRecruiters", "https://www.digitalrecruiters.com", []),
    ActorSource("flatchr", "Flatchr", "https://www.flatchr.io", []),
    ActorSource("breezy", "Breezy HR", "https://breezy.hr", []),
    ActorSource("join", "JOIN", "https://join.com", []),
    ActorSource("softy", "Softy", "https://www.softy.pro", []),
    # 29/10, migration 0012 (plan of 29/10): IT missions, like Free-Work.
    ActorSource("freelance_informatique", "Freelance-Informatique", "https://www.freelance-informatique.fr", ["M18"]),
    # 29/10, migration 0013 (plan of 29/10): publishing sources. Beetween hosts employers of every sector.
    ActorSource("beetween", "Beetween", "https://www.beetween.fr", []),
    ActorSource("editis", "Editis", "https://editis.gestmax.fr", ["E", "K", "L"]),
    # 30/10, migration 0014: career sites of the ATS platforms, boards listed in jobs/data/ats-france.txt.
    ActorSource("ats", "ATS (Greenhouse, Lever, Workday…)", "", []),
]
