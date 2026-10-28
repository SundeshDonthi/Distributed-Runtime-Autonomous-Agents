from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import Boolean, DateTime, Float, ForeignKey, Integer, JSON, String, Text
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _new_id() -> str:
    return uuid.uuid4().hex


class Base(DeclarativeBase):
    pass


class WorkflowRow(Base):
    __tablename__ = "workflows"

    id: Mapped[str] = mapped_column(String, primary_key=True)
    objective: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(String, nullable=False, default="PLANNING")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    tasks: Mapped[list["TaskRow"]] = relationship(
        back_populates="workflow", cascade="all, delete-orphan"
    )


class TaskRow(Base):
    __tablename__ = "tasks"

    id: Mapped[str] = mapped_column(String, primary_key=True)
    workflow_id: Mapped[str] = mapped_column(ForeignKey("workflows.id"), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False)
    required_capabilities: Mapped[list] = mapped_column(JSON, default=list)
    status: Mapped[str] = mapped_column(String, nullable=False, default="PENDING")
    input: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    output: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    retry_count: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    workflow: Mapped["WorkflowRow"] = relationship(back_populates="tasks")
    dependencies: Mapped[list["TaskDependencyRow"]] = relationship(
        foreign_keys="TaskDependencyRow.task_id", cascade="all, delete-orphan"
    )


class TaskDependencyRow(Base):
    __tablename__ = "task_dependencies"

    task_id: Mapped[str] = mapped_column(
        ForeignKey("tasks.id"), primary_key=True
    )
    depends_on_task_id: Mapped[str] = mapped_column(
        ForeignKey("tasks.id"), primary_key=True
    )


class ToolCallRow(Base):
    """One row per actual tool invocation (Section 11's ToolCall / Section
    15G.1). This is the ground truth a router's "prior outcomes" are computed
    from (Section 15B.3) -- distinct from `tasks`, since Phase 4's retries
    will eventually make several tool calls map to one task.

    `id` is an autoincrementing integer rather than this project's usual UUID
    string, specifically so "most recent N calls" (the observed-stats window,
    Section 15B.3) can be ordered deterministically by insertion order rather
    than by a wall-clock timestamp that two fast, consecutive calls could tie
    on.
    """

    __tablename__ = "tool_calls"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    task_id: Mapped[str] = mapped_column(ForeignKey("tasks.id"), nullable=False)
    tool_name: Mapped[str] = mapped_column(String, nullable=False)
    input: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    output: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    latency_ms: Mapped[float] = mapped_column(Float, nullable=False)
    status: Mapped[str] = mapped_column(String, nullable=False)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class RoutingDecisionRow(Base):
    """Section 15D.1's "routing_decision" event / Section 15G.2's
    `routing_decisions` table: which tool a capability-addressed task was
    routed to, and what the candidate set looked like at decision time.
    """

    __tablename__ = "routing_decisions"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=_new_id)
    task_id: Mapped[str] = mapped_column(ForeignKey("tasks.id"), nullable=False)
    workflow_id: Mapped[str] = mapped_column(ForeignKey("workflows.id"), nullable=False)
    capability: Mapped[str] = mapped_column(String, nullable=False)
    model_version: Mapped[str] = mapped_column(String, nullable=False, default="heuristic-v1")
    weights: Mapped[dict] = mapped_column(JSON, nullable=False)
    selected_tool: Mapped[str] = mapped_column(String, nullable=False)
    # True when `selected_tool` was picked by random exploration rather than
    # being the heuristic's top-ranked candidate (Section 15D.2: "track
    # selection bias: the unchosen candidates have unknown outcomes unless
    # tested in controlled evaluations") -- lets a training step distinguish
    # genuinely exploratory data from the heuristic's own greedy choices.
    was_exploration: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    candidates: Mapped[list["RoutingCandidateRow"]] = relationship(
        back_populates="decision", cascade="all, delete-orphan"
    )
    outcome: Mapped["RoutingOutcomeRow | None"] = relationship(
        back_populates="decision", cascade="all, delete-orphan", uselist=False
    )


class RoutingCandidateRow(Base):
    """Section 15G.2's `routing_candidates`: every tool that was eligible for
    a decision, not just the winner -- needed to reconstruct what the router
    chose *not* to do, and eventually to train a ranker rather than a
    single-label classifier.
    """

    __tablename__ = "routing_candidates"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=_new_id)
    decision_id: Mapped[str] = mapped_column(ForeignKey("routing_decisions.id"), nullable=False)
    tool_name: Mapped[str] = mapped_column(String, nullable=False)
    score: Mapped[float] = mapped_column(Float, nullable=False)
    rank: Mapped[int] = mapped_column(Integer, nullable=False)

    decision: Mapped["RoutingDecisionRow"] = relationship(back_populates="candidates")


class RoutingOutcomeRow(Base):
    """Section 15D.1's "routing_feedback"/"task_outcome", simplified to what's
    actually derivable at this scope: whether the chosen tool succeeded, how
    long it took, and its error if it didn't. One outcome per decision.
    """

    __tablename__ = "routing_outcomes"

    decision_id: Mapped[str] = mapped_column(
        ForeignKey("routing_decisions.id"), primary_key=True
    )
    success: Mapped[bool] = mapped_column(Boolean, nullable=False)
    latency_ms: Mapped[float] = mapped_column(Float, nullable=False)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    decision: Mapped["RoutingDecisionRow"] = relationship(back_populates="outcome")
