"""Test helpers shared by the apps' test suites."""
from datetime import timedelta

from jobs.models import JobOffer, Source
from jobs.text import fold_title


def make_source(**kwargs):
    return Source.objects.create(**{"name": "Profession Spectacle", "kind": Source.Kind.MODULE, **kwargs})


def make_offer(source, external_id="1", collected_at=None, **kwargs):
    """An offer of `source`; `collected_at` (auto_now_add, hence the update) when given."""
    fields = {"title": "Chargée de production", "employer": "Théâtre", "url": "https://example.fr/1", **kwargs}
    fields.setdefault("title_folded", fold_title(fields["title"]))
    offer = JobOffer.objects.create(source=source, external_id=external_id, **fields)
    if collected_at is not None:
        JobOffer.objects.filter(pk=offer.pk).update(collected_at=collected_at)
        offer.collected_at = collected_at
    return offer


class OfferMaker:
    """Mixin of the test cases that make many offers of `self.source`, numbered, collected `ago` before `self.now`.
    offer_defaults() gives the fields of offer number `number` that a call does not set."""

    def offer_defaults(self, number):
        return {}

    def offer(self, ago=timedelta(hours=1), source=None, **fields):
        self.offer_count = getattr(self, "offer_count", 0) + 1
        return make_offer(source or self.source, external_id=str(self.offer_count), collected_at=self.now - ago,
                          **{**self.offer_defaults(self.offer_count), **fields})

    def offers(self, count, **fields):
        return [self.offer(**fields) for _ in range(count)]
