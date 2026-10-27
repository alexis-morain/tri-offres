"""The ROME lists the questionnaire offers: the 22 domains of France Travail's job search, and the 110 professional
domains (second level of the ROME) used as the list of jobs.

Sources, checked on 12/10/2026:
- the 22 domains and their labels: the « Domaine » filter of https://candidat.francetravail.fr/offres/recherche ;
- the 110 professional domains: ROME 4.0 open data, « Arborescence principale » of 15/06/2026 (version 61),
  https://www.data.gouv.fr/datasets/repertoire-operationnel-des-metiers-et-des-emplois-rome (Licence Ouverte).

Labels are copied word for word, with French typography only: typographic apostrophe, accented capitals
(« Études »), « œ ». The ROME changes twice a year: profiles and sources store the codes, never the labels.

Codes are those of France Travail's filter. Three of them name part of a ROME domain only: « M » is « Achats /
Comptabilité / Gestion » (M11 and M12, the other M domains are listed on their own), « C » leaves out C15
(« Immobilier ») and « L » leaves out L14 (« Sport »).
"""

import re


# The 22 domains, in the order France Travail shows them (alphabetical by label).
DOMAINS = [
    ("M", "Achats / Comptabilité / Gestion"),
    ("B", "Arts / Artisanat d’art"),
    ("C", "Banque / Assurance"),
    ("F", "Bâtiment / Travaux Publics"),
    ("D", "Commerce / Vente"),
    ("E", "Communication / Multimédia"),
    ("M14", "Conseil / Études"),
    ("M13", "Direction d’entreprise"),
    ("A", "Espaces verts et naturels / Agriculture / Pêche / Soins aux animaux"),
    ("G", "Hôtellerie - Restauration / Tourisme / Animation"),
    ("C15", "Immobilier"),
    ("H", "Industrie"),
    ("M18", "Informatique / Télécommunication"),
    ("I", "Installation / Maintenance"),
    ("M17", "Marketing / Stratégie commerciale"),
    ("M15", "Ressources Humaines"),
    ("J", "Santé"),
    ("M16", "Secrétariat / Assistanat"),
    ("K", "Services à la personne / à la collectivité"),
    ("L", "Spectacle"),
    ("L14", "Sport"),
    ("N", "Transport / Logistique"),
]

# The 110 professional domains, in ROME order.
PROFESSIONAL_DOMAINS = [
    ("A11", "Engins agricoles et forestiers"),
    ("A12", "Espaces naturels et espaces verts"),
    ("A13", "Études et assistance technique"),
    ("A14", "Production"),
    ("A15", "Soins aux animaux"),
    ("B11", "Arts plastiques"),
    ("B12", "Céramique"),
    ("B13", "Décoration"),
    ("B14", "Fibres et papier"),
    ("B15", "Instruments de musique"),
    ("B16", "Métal, verre, bijouterie et horlogerie"),
    ("B17", "Taxidermie"),
    ("B18", "Tissu et cuirs"),
    ("C11", "Assurance"),
    ("C12", "Banque"),
    ("C13", "Finance"),
    ("C14", "Gestion administrative banque et assurances"),
    ("C15", "Immobilier"),
    ("D11", "Commerce alimentaire et métiers de bouche"),
    ("D12", "Commerce non alimentaire et de prestations de confort"),
    ("D13", "Direction de magasin de détail"),
    ("D14", "Force de vente"),
    ("D15", "Grande distribution"),
    ("E11", "Édition et communication"),
    ("E12", "Images et sons"),
    ("E13", "Industries graphiques"),
    ("E14", "Publicité"),
    ("F11", "Conception et études"),
    ("F12", "Conduite et encadrement de chantier - travaux"),
    ("F13", "Engins de chantier"),
    ("F14", "Extraction"),
    ("F15", "Montage de structures"),
    ("F16", "Second œuvre"),
    ("F17", "Travaux et gros œuvre"),
    ("G11", "Accueil et promotion touristique"),
    ("G12", "Animation d’activités de loisirs"),
    ("G13", "Conception, commercialisation et vente de produits touristiques"),
    ("G14", "Gestion et direction"),
    ("G15", "Personnel d’étage en hôtellerie"),
    ("G16", "Production culinaire"),
    ("G17", "Accueil en hôtellerie"),
    ("G18", "Service"),
    ("H11", "Affaires et support technique client"),
    ("H12", "Conception, recherche, études et développement"),
    ("H13", "Hygiène Sécurité Environnement -HSE- industriels"),
    ("H14", "Méthodes et gestion industrielles"),
    ("H15", "Qualité et analyses industrielles"),
    ("H21", "Alimentaire"),
    ("H22", "Bois"),
    ("H23", "Chimie et pharmacie"),
    ("H24", "Cuir et textile"),
    ("H25", "Direction, encadrement et pilotage de fabrication et production industrielles"),
    ("H26", "Électronique et électricité"),
    ("H27", "Énergie"),
    ("H28", "Matériaux de construction, céramique et verre"),
    ("H29", "Mécanique, travail des métaux et outillage"),
    ("H31", "Papier et carton"),
    ("H32", "Plastique, caoutchouc"),
    ("H33", "Préparation et conditionnement"),
    ("H34", "Traitements thermiques et traitements de surfaces"),
    ("I11", "Encadrement"),
    ("I12", "Entretien technique"),
    ("I13", "Équipements de production, équipements collectifs"),
    ("I14", "Équipements domestiques et informatique"),
    ("I15", "Travaux d’accès difficile"),
    ("I16", "Véhicules, engins, aéronefs"),
    ("J11", "Praticiens médicaux"),
    ("J12", "Praticiens médico-techniques"),
    ("J13", "Professionnels médico-techniques"),
    ("J14", "Rééducation et appareillage"),
    ("J15", "Soins paramédicaux"),
    ("K11", "Accompagnement de la personne"),
    ("K12", "Action sociale, socio-éducative et socio-culturelle"),
    ("K13", "Aide à la vie quotidienne"),
    ("K14", "Conception et mise en œuvre des politiques publiques"),
    ("K15", "Contrôle public"),
    ("K16", "Culture et gestion documentaire"),
    ("K17", "Défense, sécurité publique et secours"),
    ("K18", "Développement territorial et emploi"),
    ("K19", "Droit"),
    ("K21", "Formation initiale et continue"),
    ("K22", "Nettoyage et propreté industriels"),
    ("K23", "Propreté et environnement urbain"),
    ("K24", "Recherche"),
    ("K25", "Sécurité privée"),
    ("K26", "Services funéraires"),
    ("L11", "Animation de spectacles"),
    ("L12", "Artistes - interprètes du spectacle"),
    ("L13", "Conception et production de spectacles"),
    ("L14", "Sport professionnel"),
    ("L15", "Techniciens du spectacle"),
    ("M11", "Achats"),
    ("M12", "Comptabilité et gestion"),
    ("M13", "Direction d’entreprise"),
    ("M14", "Organisation et études"),
    ("M15", "Ressources humaines"),
    ("M16", "Secrétariat et assistance"),
    ("M17", "Stratégie commerciale, marketing et supervision des ventes"),
    ("M18", "Systèmes d’information et de télécommunication"),
    ("N11", "Magasinage, manutention des charges et déménagement"),
    ("N12", "Organisation de la circulation des marchandises"),
    ("N13", "Personnel d’encadrement de la logistique"),
    ("N21", "Personnel navigant du transport aérien"),
    ("N22", "Personnel sédentaire du transport aérien"),
    ("N31", "Personnel navigant du transport maritime et fluvial"),
    ("N32", "Personnel sédentaire du transport maritime et fluvial"),
    ("N41", "Personnel de conduite du transport routier"),
    ("N42", "Personnel d’encadrement du transport routier"),
    ("N43", "Personnel navigant du transport terrestre"),
    ("N44", "Personnel sédentaire du transport ferroviaire et réseau filo guidé"),
]

DOMAIN_LABELS = dict(DOMAINS)
PROFESSIONAL_DOMAIN_LABELS = dict(PROFESSIONAL_DOMAINS)
# The search screen takes two domains at most. A stored value may hold three: the culture accounts of before the
# domains were migrated to E, K and L, and have to drop one when they open the screen again.
MAX_DOMAINS = 2
MAX_STORED = 3
# Domains of France Travail's filter that are a professional domain themselves.
_WHOLE = {"C15", "L14", "M13", "M14", "M15", "M16", "M17", "M18"}


def professional_domains(domain):
    """(code, label) of the professional domains of one of the 22 domains, in ROME order."""
    if domain in _WHOLE:
        return [(domain, PROFESSIONAL_DOMAIN_LABELS[domain])]
    if domain == "M":
        return [item for item in PROFESSIONAL_DOMAINS if item[0] in ("M11", "M12")]
    return [item for item in PROFESSIONAL_DOMAINS if item[0][0] == domain and item[0] not in _WHOLE]


def normalize_domains(value, cap=MAX_STORED):
    """`Profile.domains`: known codes without duplicates, in the order of DOMAINS, 0 to 3 of them ([] for None).
    `cap=None` for `Source.domains`, which may serve any number. Raises ValueError when anything does not fit."""
    if value is None:
        return []
    if not isinstance(value, list) or not all(isinstance(code, str) and code in DOMAIN_LABELS for code in value):
        raise ValueError(f"domains: unexpected value {value!r}")
    chosen = set(value)
    if cap is not None and len(chosen) > cap:
        raise ValueError(f"domains: at most {cap}")
    return [code for code, _ in DOMAINS if code in chosen]


# Professional domain (three first characters of a ROME code) -> the one domain of DOMAINS that holds it.
_DOMAIN_OF_PROFESSIONAL = {
    professional: domain for domain, _ in DOMAINS for professional, _ in professional_domains(domain)
}


# A ROME fiche code: a known professional domain and two ASCII digits. The one definition, used by is_rome_code and
# as is by the SQL of jobs/filters.py (Django's REGEXP on SQLite is Python's re.search).
ROME_CODE_PATTERN = r"^(?:" + "|".join(sorted(_DOMAIN_OF_PROFESSIONAL)) + r")[0-9]{2}\Z"


def is_rome_code(code):
    return isinstance(code, str) and re.search(ROME_CODE_PATTERN, code) is not None


def domain_for_rome(code):
    """The domain of DOMAINS holding a ROME fiche (« M1805 » gives « M18 », « M1203 » gives « M »), None when
    the code is not a ROME code (is_rome_code)."""
    return _DOMAIN_OF_PROFESSIONAL[code[:3]] if is_rome_code(code) else None
