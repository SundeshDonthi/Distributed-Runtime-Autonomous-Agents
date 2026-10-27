from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import DateTime, ForeignKey, Integer, JSON, String, Text
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


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
