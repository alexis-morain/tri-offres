"""Chip lists and examples of the "prefer" and "words" blocks, kept in the code.

Labels are written as they read inside a sentence; `shown()` gives them a capital initial for the chips.
The jobs offered are the ROME professional domains of the person's domains (onboarding/rome.py), completed by the
lists written by hand for the culture and growth niches. Codes are unique across every list (ROME codes in
capitals, the others in lowercase): a code alone says which list it comes from.
"""
from onboarding import rome

VALUES = [
    ("meaning", "le sens du travail"),
    ("stability", "la stabilité"),
    ("learning", "apprendre"),
    ("autonomy", "l’autonomie"),
    ("team", "l’équipe"),
    ("pay", "la rémunération"),
    ("proximity", "la proximité"),
    ("flexible_hours", "la souplesse des horaires"),
]

JOBS = {
    "culture": [
        ("mediation", "médiation"),
        ("production_admin", "production et administration"),
        ("communication", "communication"),
        ("editorial", "édition et éditorial"),
        ("book_trade", "diffusion et commerce du livre"),
        ("library", "bibliothèque et documentation"),
        ("teaching", "enseignement et formation"),
        ("nonprofit_projects", "coordination de projets associatifs"),
        ("sponsorship", "mécénat et partenariats"),
    ],
    "growth": [
        ("sdr_bdr", "SDR ou BDR"),
        ("growth_marketing", "growth marketing"),
        ("outbound", "outbound"),
        ("sales_ops", "sales ops"),
        ("revops", "RevOps"),
        ("lifecycle_crm", "lifecycle et CRM"),
        ("account_executive", "account executive"),
    ],
}

# A hand-written list joins the jobs as soon as one of the chosen domains is one of its domains.
NICHES = {
    "culture": {"domains": {"E", "K", "L"}, "label": "Plus précis, pour la culture et l’ESS"},
    "growth": {"domains": {"D", "M17"}, "label": "Plus précis, pour le growth et la vente"},
}

# One list for every domain. Culture, ESS and startup will also switch on the niche sources (step 4).
STRUCTURES = [
    ("company", "entreprise"),
    ("startup", "startup"),
    ("association_ess", "association et ESS"),
    ("culture", "culture"),
    ("public_sector", "secteur public"),
    ("health_social", "établissement de santé ou social"),
    ("education", "enseignement"),
]

# The employer's sector, a preference weighed in the score (never a filter).
# Industries a person would name, apart from the job families (rome.py) and from the structures above: the public
# sector, associations, health establishments and schools stay in STRUCTURES. Codes differ from every other list.
SECTORS = [
    ("real_estate", "immobilier"),
    ("banking_insurance", "banque et assurance"),
    ("industry", "industrie"),
    ("construction", "bâtiment et travaux publics"),
    ("retail", "commerce et distribution"),
    ("healthcare", "santé et médico-social"),
    ("culture_media", "culture et médias"),
    ("digital", "numérique et logiciels"),
    ("consulting", "conseil et services aux entreprises"),
    ("tourism_hospitality", "tourisme, hôtellerie et restauration"),
    ("transport_logistics", "transport et logistique"),
    ("energy_environment", "énergie et environnement"),
    ("food_agri", "agroalimentaire et agriculture"),
    ("education_training", "éducation et formation"),
]

# Two examples under each free text field, written to fit any domain.
EXAMPLES = {
    "seek": ("Une équipe à taille humaine, où je touche à plusieurs sujets.",
             "Un poste où je vois le résultat concret de mon travail."),
    "refuse": ("Les horaires décalés et le travail du week-end.",
               "Les postes payés surtout à la commission."),
}

LABELS = dict(VALUES + rome.PROFESSIONAL_DOMAINS + STRUCTURES + SECTORS)
for _items in JOBS.values():
    LABELS.update(_items)


def upper_first(text):
    return text[:1].upper() + text[1:]


def shown(items):
    """(code, label) pairs as the chips show them."""
    return [(code, upper_first(label)) for code, label in items]


def job_groups(domains):
    """[(group label, [(code, label as the chips show it), ...]), ...]: one group per chosen domain with its ROME
    professional domains, then one per niche list that one of the domains switches on."""
    groups = [(rome.DOMAIN_LABELS[domain], rome.professional_domains(domain)) for domain in domains]
    for niche, data in NICHES.items():
        if data["domains"] & set(domains):
            groups.append((data["label"], shown(JOBS[niche])))
    return groups


def job_codes(domains):
    """The job codes a person with these domains may pick."""
    return {code for _, items in job_groups(domains) for code, _ in items}
