"""How long a deleted account stays in the backups.

docker/litestream.yml keeps 30 days (snapshot retention 696 h plus the 24 h snapshot interval); the privacy page and
the account journal's duration (accounts/journal.py) read this constant. config/test_backups.py ties it to the file.
"""
BACKUP_DAYS = 30
