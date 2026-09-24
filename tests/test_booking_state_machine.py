import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from backend.agent.state import BookingState, ConversationStateMachine
from backend.models import Base


@pytest.fixture
def db():
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
    Base.metadata.create_all(bind=engine)
    session = sessionmaker(bind=engine)()
    yield session
    session.close()


def test_new_conversation_starts_idle(db):
    sm = ConversationStateMachine(db, conversation_id=1)
    assert sm.state == BookingState.IDLE


def test_transition_persists_across_reload(db):
    sm = ConversationStateMachine(db, conversation_id=1)
    sm.transition(BookingState.COLLECTING_TIME, pending_event_type_id=42)

    reloaded = ConversationStateMachine(db, conversation_id=1)
    assert reloaded.state == BookingState.COLLECTING_TIME
    assert reloaded.row.pending_event_type_id == 42


def test_maybe_cancel_resets_to_idle_and_clears_pending(db):
    sm = ConversationStateMachine(db, conversation_id=1)
    sm.transition(BookingState.AWAITING_CONFIRMATION, pending_slot_start="2026-09-25T15:00:00Z")

    cancelled = sm.maybe_cancel("actually, never mind")

    assert cancelled
    assert sm.state == BookingState.IDLE
    assert sm.row.pending_slot_start is None


def test_maybe_cancel_returns_false_when_idle(db):
    sm = ConversationStateMachine(db, conversation_id=1)
    assert sm.maybe_cancel("never mind") is False
    assert sm.state == BookingState.IDLE


def test_maybe_cancel_false_without_cancel_phrase(db):
    sm = ConversationStateMachine(db, conversation_id=1)
    sm.transition(BookingState.COLLECTING_TIME)
    assert sm.maybe_cancel("how about 3pm tomorrow") is False
    assert sm.state == BookingState.COLLECTING_TIME
