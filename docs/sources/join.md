# JOIN

- Key in the collector and in `jobs/actor_sources.py`: `join`
- Site: <https://join.com>
- Kind: Recruiting software: career pages that employers host on it
- Country: France only

A recruiting software (JOIN); offers come from the job pages of the employers using it.

## Who receives its offers

Every person. An offer whose title gives a ROME code goes to that code's domain only; an offer without a code stays a
candidate for everyone, and the scoring decides.

## What tri-offres keeps

The common fields of every source: title, employer, place, contract type, part time and remote work when given,
salary or daily rate when given, publication and expiry dates, the link to the offer's page at the source, and a
ROME code guessed from the title. Claude writes a short summary of each offer during the night. The full text of the
offer is used for that night only and deleted 7 days after collection; the offer is deleted after 45 days or once it
expires. No recruiter contact field is imported; a name or a contact written inside the text leaves with it.

Each offer card, in the mail and on the sort page, shows the source's name and links to the offer's page on the
source site, never to a direct application link.

## Attribution

tri-offres knows of no reuse licence nor attribution requirement published by this source. If the collector ever sends
one (`source_license`), it is shown next to the offer.

## Cutting the source

In the admin, Sources, tick the source and choose "Couper et supprimer ses offres": the source is disabled, its
offers and the answers they carry are deleted (superusers only). If a site asks to be removed, follow
[docs/retrait-source.md](../retrait-source.md).
