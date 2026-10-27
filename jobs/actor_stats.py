"""Format of the RUN_STATS record the actor writes at the end of its run (the design plan).

Constants only, shared by the actor's runner (jobs/scraper.py, which reads and checks the record) and the import
(jobs/collect.py).
"""
RUN_STATS_VERSION = 1
RUN_STATS_COUNTS = ("pushed", "filtered", "dupes", "cross_dupes", "already_seen", "invalid")
# Bounds of a sane record: beyond them the record is unusable. Compared before any conversion, so a huge JSON
# integer never reaches float() nor math.isfinite(), which raise OverflowError on it.
RUN_STATS_MAX_COUNT = 10_000_000  # exclusive
RUN_STATS_MAX_SECONDS = 1_000_000  # exclusive
