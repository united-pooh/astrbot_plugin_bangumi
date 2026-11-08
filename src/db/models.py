"""
数据库 ORM 模型定义

此模块包含所有 SQLAlchemy ORM 模型,用于定义数据库表结构和关系

"""

from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Integer, String, func
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    pass


class BangumiSubject(Base):
    """
    番剧条目模型
    """

    __tablename__ = "bangumi_subjects"

    subject_id: Mapped[str] = mapped_column(String, primary_key=True)
    name: Mapped[str | None] = mapped_column(String, nullable=True)
    air_date: Mapped[str | None] = mapped_column(String, nullable=True)  # 开播日期/时间
    total_episodes: Mapped[int] = mapped_column(Integer, default=0)
    current_episode: Mapped[int] = mapped_column(
        Integer, default=0
    )  # Latest observed episode; delivery state belongs to each subscription.
    broadcast_time: Mapped[str | None] = mapped_column(
        String, nullable=True
    )  # 播出时间,格式: HH:MM,如 "22:00"。从 bgmlist API 自动填充或手动设置
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=func.now(), onupdate=func.now()
    )

    # 建立与 Subscription 的一对多关系
    subscriptions: Mapped[list["Subscription"]] = relationship(
        back_populates="subject", cascade="all, delete-orphan"
    )

    def __repr__(self) -> str:
        return f"<BangumiSubject(id={self.subject_id}, name={self.name})>"

    def __str__(self) -> str:
        return f"{self.name} ({self.subject_id}) [{self.current_episode}/{self.total_episodes}]"


class Subscription(Base):
    """
    订阅关系模型
    """

    __tablename__ = "subscriptions"

    group_id: Mapped[str] = mapped_column(String, primary_key=True)
    subject_id: Mapped[str] = mapped_column(
        String, ForeignKey("bangumi_subjects.subject_id"), primary_key=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime, default=func.now())
    last_notified_episode: Mapped[int] = mapped_column(Integer, default=0)
    notification_error: Mapped[str | None] = mapped_column(String, nullable=True)

    # 建立与 BangumiSubject 的多对一关系
    subject: Mapped[BangumiSubject] = relationship(back_populates="subscriptions")

    def __repr__(self) -> str:
        return f"<Subscription(id={self.subject_id}, group_id={self.group_id}, created_at={self.created_at})>"

    def __str__(self) -> str:
        return f"- 群 {self.group_id} 订阅了 {self.subject.name} ({self.subject.subject_id})"
