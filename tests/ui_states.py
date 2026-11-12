"""Sample batches that put the main window in each §7.3 status-panel state."""

from __future__ import annotations

from WeFlow.core.state import Batch

CARD = "/Volumes/EOS_DIGITAL"


def batch(state: str, **changes: object) -> Batch:
    base = Batch(
        id=1000,
        event_id="",
        event_name="Smith Wedding",
        label="EOS_DIGITAL",
        source=CARD,
        is_card=True,
        destination="smugmug",
        state=state,
        verified=state not in ("copying", "error"),
        total=248,
        copied=248,
        started=1000.0,
    )
    for key, value in changes.items():
        setattr(base, key, value)
    return base


# state name -> (batch or None, card still in?, paused?)
PANEL_STATES: dict[str, tuple[Batch | None, bool, bool]] = {
    "idle": (None, False, False),
    "paused": (None, False, True),
    "copying": (batch("copying", copied=96, skipped=4), True, False),
    "safe": (batch("processing", processed=31), True, False),
    "working-processing": (batch("processing", processed=120), False, False),
    "working-uploading": (batch("uploading", processed=248, uploaded=77), False, False),
    "ready": (batch("ready", ready=248), True, False),
    "held": (batch("held", processed=248, held=248), False, False),
    "done": (batch("done", processed=248, uploaded=248), False, False),
    "attention": (
        batch(
            "attention",
            processed=248,
            uploaded=236,
            failed=12,
            error="12 photos failed. SmugMug sign-in has expired. Reconnect it in Settings.",
        ),
        False,
        False,
    ),
    "copy-error": (
        batch("error", copied=40, error="The card was removed or could not be read."),
        True,
        False,
    ),
}
EXPECTED_PANEL = {
    "idle": "idle",
    "paused": "paused",
    "copying": "copying",
    "safe": "safe",
    "working-processing": "working",
    "working-uploading": "working",
    "ready": "ready",
    "held": "held",
    "done": "done",
    "attention": "attention",
    "copy-error": "copy_error",
}
EARLIER = [
    batch(
        "uploading", id=900, label="CARD_B", copied=180, processed=180, uploaded=64, started=900.0
    ),
    batch("held", id=800, label="CARD_C", copied=96, processed=96, held=96, started=800.0),
    batch("done", id=700, label="CARD_D", copied=312, processed=312, uploaded=312, started=700.0),
]
