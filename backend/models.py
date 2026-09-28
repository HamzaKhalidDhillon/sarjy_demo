from sqlalchemy import Column, DateTime, Integer, String, Text, func
from sqlalchemy.orm import declarative_base

Base = declarative_base()


class Memory(Base):
    __tablename__ = "memories"
    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(String, index=True)
    key = Column(String, index=True)
    value = Column(Text)


class Conversation(Base):
    __tablename__ = "conversations"
    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(String, index=True)
    title = Column(String, default="conversation")


class Message(Base):
    __tablename__ = "messages"
    id = Column(Integer, primary_key=True, index=True)
    conversation_id = Column(Integer, index=True)
    role = Column(String, index=True)  # 'user' or 'assistant'
    content = Column(Text)


class BookingAttempt(Base):
    """One row per attempted booking confirmation. The unique idempotency_key is what actually
    prevents double-booking Cal.com when a confirm is retried or duplicated."""
    __tablename__ = "booking_attempts"
    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(String, index=True)
    conversation_id = Column(Integer, index=True)
    idempotency_key = Column(String, unique=True, index=True)
    event_type_id = Column(Integer)
    requested_start = Column(String)  # ISO8601
    status = Column(String, default="pending")  # pending | confirmed | failed
    calcom_booking_uid = Column(String, nullable=True)
    calcom_booking_id = Column(Integer, nullable=True)
    error_message = Column(Text, nullable=True)
    created_at = Column(DateTime, server_default=func.now())
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now())


class ConversationState(Base):
    """1:1 with Conversation.id. Tracks where a multistep booking flow is so the agent can
    recover if the user goes off-script instead of losing the pending booking."""
    __tablename__ = "conversation_states"
    conversation_id = Column(Integer, primary_key=True)
    state = Column(String, default="idle")
    pending_event_type_id = Column(Integer, nullable=True)
    pending_slot_start = Column(String, nullable=True)
    pending_attendee_email = Column(String, nullable=True)
    target_booking_id = Column(Integer, nullable=True)  # BookingAttempt being cancelled/rescheduled
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now())


class User(Base):
    """Demo sign-in: the first login with a new username creates the account. The username is
    what every other table stores as user_id."""
    __tablename__ = "users"
    id = Column(Integer, primary_key=True, index=True)
    username = Column(String, unique=True, index=True)
    password_hash = Column(String)
    token = Column(String, unique=True, index=True, nullable=True)
    created_at = Column(DateTime, server_default=func.now())
