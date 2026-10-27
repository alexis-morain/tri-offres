from django.db import models


class TaskRun(models.Model):
    """One pass of a `manage.py nuit <slot>` run (the design plan). No uniqueness:
    --force replays a slot and adds a line. Errors are one short line, never a trace."""

    SLOT_CHOICES = [("nuit", "Nuit"), ("matin", "Matin"), ("soir", "Soir")]
    STATUS_CHOICES = [("running", "En cours"), ("ok", "Réussi"), ("failed", "En échec"),
                      ("interrupted", "Interrompu")]

    slot = models.CharField("créneau", max_length=10, choices=SLOT_CHOICES)
    day = models.DateField("jour (heure de Paris)")
    started_at = models.DateTimeField("début")
    finished_at = models.DateTimeField("fin", null=True, blank=True)
    status = models.CharField("état", max_length=12, choices=STATUS_CHOICES, default="running")
    # List of {"name", "status" (ok, failed, skipped), "seconds", "error"}.
    steps = models.JSONField("étapes", default=list, blank=True)
    forced = models.BooleanField("rejoué de force", default=False)

    class Meta:
        verbose_name = "passage de tâche"
        verbose_name_plural = "passages de tâches"
        ordering = ["-started_at"]
        indexes = [models.Index(fields=["slot", "day"])]

    def __str__(self):
        return f"{self.slot} {self.day}"

    @property
    def duration(self):
        return None if self.finished_at is None else self.finished_at - self.started_at


class AlertSent(models.Model):
    """One alert reason mailed for a slot and a day (S5): the same (slot, day, reason) never goes out twice, and the
    rows of the last 24 hours bound the mails the site sends. A mail with several reasons writes one row each, all
    with the same `sent_at`."""

    slot = models.CharField("créneau", max_length=10, choices=TaskRun.SLOT_CHOICES)
    day = models.DateField("jour (heure de Paris)")
    reason = models.CharField("motif", max_length=60)
    sent_at = models.DateTimeField("envoyé le")

    class Meta:
        verbose_name = "alerte envoyée"
        verbose_name_plural = "alertes envoyées"
        ordering = ["-sent_at"]
        constraints = [models.UniqueConstraint(fields=["slot", "day", "reason"], name="alertsent_unique_reason")]
        indexes = [models.Index(fields=["sent_at"])]

    def __str__(self):
        return f"{self.slot} {self.day} {self.reason}"
