"""Names of OnboardingEvent rows, in one place."""

CODE_REQUESTED = "code_requested"
CODE_ENTERED = "code_entered"
LINK_USED = "link_used"
# One CV analysis started (onboarding/cv.py), failures included: the daily limits count these.
CV_PARSED = "cv_parsed"
# The summary (onboarding/summary.py): written, rewritten after a correction, or not written (details: error class).
# One summary call started (generation or correction), failures included: the daily limits count these.
SUMMARY_REQUESTED = "summary_requested"
SUMMARY_GENERATED = "summary_generated"
SUMMARY_CORRECTED = "summary_corrected"
SUMMARY_FAILED = "summary_failed"

# The end of the questionnaire (onboarding/finish.py) and the single reminder (onboarding/reminder.py).
ONBOARDING_COMPLETED = "onboarding_completed"
WELCOME_SENT = "welcome_sent"
REMINDER_SENT = "reminder_sent"


def step(slug):
    """A questionnaire screen validated (onboarding/flow.py), logged on every validation, edits included."""
    return f"step:{slug}"
