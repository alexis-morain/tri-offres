"""Domain rule of an offer (plan of step 4, decision 12), shared by the collection report and the counter (4.5)."""
from onboarding.rome import domain_for_rome


def offer_domains(source_domains, rome_code):
    """Domains an offer reaches, None for every person. A source with domains reaches them, plus the domain of the
    offer's ROME code; a general source reaches the ROME code's domain only, or everyone without a known code."""
    rome_domain = domain_for_rome(rome_code)
    found = {rome_domain} if rome_domain else set()
    if source_domains:
        return frozenset(source_domains) | found
    return frozenset(found) or None
