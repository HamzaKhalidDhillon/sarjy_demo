"""Explicit state machine for the meeting-booking multistep flow, so it can recover instead of
losing track when the user goes off-script mid-flow.
"""
from enum import Enum

from sqlalchemy.orm import Session

from backend.models import ConversationState as ConversationStateRow

CANCEL_PHRASES = ("never mind", "nevermind", "cancel that", "forget it", "stop the booking")


class BookingState(str, Enum):
    IDLE = "idle"
    COLLECTING_TIME = "collecting_time"
    AWAITING_CONFIRMATION = "awaiting_confirmation"
    AWAITING_CONTACT = "awaiting_contact"
    BOOKED = "booked"
    FAILED = "failed"
    # changing an existing booking
    CHOOSE_TO_CANCEL = "choose_to_cancel"
    CHOOSE_TO_RESCHEDULE = "choose_to_reschedule"
    CONFIRM_CANCEL = "confirm_cancel"
    RESCHEDULE_TIME = "reschedule_time"
    CONFIRM_RESCHEDULE = "confirm_reschedule"
    CHANGED = "changed"  # a cancel/reschedule was just completed


class ConversationStateMachine:
    def __init__(self, db: Session, conversation_id: int):
        self.db = db
        self.conversation_id = conversation_id
        self.row = self._load_or_create()

    def _load_or_create(self) -> ConversationStateRow:
        row = (
            self.db.query(ConversationStateRow)
            .filter(ConversationStateRow.conversation_id == self.conversation_id)
            .first()
        )
        if not row:
            row = ConversationStateRow(conversation_id=self.conversation_id, state=BookingState.IDLE.value)
            self.db.add(row)
            self.db.commit()
            self.db.refresh(row)
        return row

    @property
    def state(self) -> BookingState:
        return BookingState(self.row.state)

    def transition(self, new_state: BookingState, **fields) -> None:
        self.row.state = new_state.value
        for key, value in fields.items():
            setattr(self.row, key, value)
        self.db.add(self.row)
        self.db.commit()
        self.db.refresh(self.row)

    def maybe_cancel(self, message: str) -> bool:
        """Explicit cancel phrases reset to idle and clear any pending booking context."""
        # Nothing in progress (after a booking, "cancel that call" means the real booking, which
        # the agent handles), or the user is answering "should I cancel it?" themselves.
        if self.state in (BookingState.IDLE, BookingState.BOOKED, BookingState.CHANGED, BookingState.CONFIRM_CANCEL):
            return False
        lowered = message.lower()
        if any(phrase in lowered for phrase in CANCEL_PHRASES):
            self.transition(
                BookingState.IDLE,
                pending_event_type_id=None, pending_slot_start=None, pending_attendee_email=None,
                target_booking_id=None,
            )
            return True
        return False
