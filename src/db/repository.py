"""
数据访问层(Repository 模式)

此模块封装所有数据库操作,为业务层提供数据访问接口

"""

import os
from dataclasses import dataclass
from difflib import SequenceMatcher

from astrbot.api import logger
from sqlalchemy import Engine, create_engine, or_
from sqlalchemy.orm import joinedload, scoped_session, sessionmaker

from ..domain.exceptions import DatabaseError
from .models import BangumiSubject, Base, Subscription


def _has_column(engine: Engine, table_name: str, column_name: str) -> bool:
    """检查表是否已有指定列"""
    from sqlalchemy import inspect

    inspector = inspect(engine)
    columns = [c["name"] for c in inspector.get_columns(table_name)]
    return column_name in columns


@dataclass(frozen=True)
class _CandidateScore:
    exact_id: int
    prefix_id: int
    name_contains: int
    similarity: float
    subject_id: str


class BangumiRepository:
    """
    番剧数据访问层
    """

    def __init__(self, db_path: str) -> None:
        """
        初始化数据访问层

        Args:
            db_path: 数据库文件路径
        """
        os.makedirs(os.path.dirname(db_path), exist_ok=True)
        self.db_path = db_path
        self._init_db()

    def _init_db(self) -> None:
        """
        初始化数据库连接和表结构
        """
        try:
            # 使用 sqlite
            engine = create_engine(f"sqlite:///{self.db_path}")
            # 创建表
            Base.metadata.create_all(engine)
            # 迁移:为已有表添加 broadcast_time 列
            self._run_migrations(engine)
            # 创建 session factory
            self.Session = scoped_session(sessionmaker(bind=engine))
        except Exception as e:
            raise DatabaseError(f"初始化数据库失败: {e}") from e

    @staticmethod
    def _run_migrations(engine: Engine) -> None:
        """运行数据库迁移,为已有表添加新列"""
        try:
            if not _has_column(engine, "bangumi_subjects", "broadcast_time"):
                from sqlalchemy import text

                with engine.connect() as conn:
                    conn.execute(
                        text(
                            "ALTER TABLE bangumi_subjects "
                            "ADD COLUMN broadcast_time VARCHAR"
                        )
                    )
                    conn.commit()
                    logger.info("数据库迁移:已添加 broadcast_time 列")
        except Exception as e:
            raise DatabaseError(f"数据库迁移失败: {e}") from e

    def update_subject(self, subject_id: str, **kwargs: str | int | None) -> bool:
        """
        更新或保存番剧信息

        Args:
            subject_id: 番剧 ID
            **kwargs: 支持传入 name, air_date, total_episodes, current_episode 等

        Returns:
            操作是否成功

        """
        session = self.Session()
        try:
            subject = (
                session.query(BangumiSubject)
                .filter_by(subject_id=str(subject_id))
                .first()
            )
            if not subject:
                name = kwargs.pop("name", "未知番剧")
                subject = BangumiSubject(
                    subject_id=str(subject_id), name=name, **kwargs
                )
                session.add(subject)
            else:
                for key, value in kwargs.items():
                    if hasattr(subject, key) and value is not None:
                        setattr(subject, key, value)
            session.commit()
            return True
        except Exception as e:
            logger.error(f"更新番剧信息失败: {e}")
            session.rollback()
            raise DatabaseError(f"更新番剧信息失败: {e}") from e
        finally:
            session.close()

    def add_subscription(self, group_id: str, subject_id: str) -> bool:
        """
        添加订阅关系

        Args:
            group_id: 群组 ID
            subject_id: 番剧 ID

        Returns:
            操作是否成功

        """
        session = self.Session()
        try:
            # 确保 Subject 存在
            subject = (
                session.query(BangumiSubject)
                .filter_by(subject_id=str(subject_id))
                .first()
            )
            if not subject:
                subject = BangumiSubject(subject_id=str(subject_id), name="未知番剧")
                session.add(subject)

            existing = (
                session.query(Subscription)
                .filter_by(group_id=str(group_id), subject_id=str(subject_id))
                .first()
            )

            if not existing:
                new_sub = Subscription(
                    group_id=str(group_id), subject_id=str(subject_id)
                )
                session.add(new_sub)

            session.commit()  # 单次 commit,保证原子性
            return True
        except Exception as e:
            logger.error(f"添加订阅失败: {e}")
            session.rollback()
            raise DatabaseError(f"添加订阅失败: {e}") from e
        finally:
            session.close()

    def remove_subscription(self, group_id: str, subject_id: str) -> bool:
        """
        移除订阅关系

        Args:
            group_id: 群组 ID
            subject_id: 番剧 ID

        Returns:
            操作是否成功

        """
        session = self.Session()
        try:
            normalized_subject_id = str(subject_id)
            sub = (
                session.query(Subscription)
                .filter_by(group_id=str(group_id), subject_id=normalized_subject_id)
                .first()
            )
            if sub:
                session.delete(sub)
                session.flush()
                remaining_subscription = (
                    session.query(Subscription)
                    .filter_by(subject_id=normalized_subject_id)
                    .first()
                )
                if not remaining_subscription:
                    subject = (
                        session.query(BangumiSubject)
                        .filter_by(subject_id=normalized_subject_id)
                        .first()
                    )
                    if subject:
                        session.delete(subject)
                session.commit()
                return True
            return False  # 订阅不存在
        except Exception as e:
            logger.error(f"移除订阅失败: {e}")
            session.rollback()
            raise DatabaseError(f"移除订阅失败: {e}") from e
        finally:
            session.close()

    def get_subscriptions(self, group_id: str) -> list[str]:
        """
        获取指定群组的所有订阅

        Args:
            group_id: 群组 ID

        Returns:
            订阅的番剧 ID 列表

        """
        session = self.Session()
        try:
            subs = session.query(Subscription).filter_by(group_id=str(group_id)).all()
            return [sub.subject_id for sub in subs]
        except Exception as e:
            logger.error(f"获取订阅失败: {e}")
            raise DatabaseError(f"获取订阅失败: {e}") from e
        finally:
            session.close()

    def get_subscribed_subjects(self, group_id: str) -> list[BangumiSubject]:
        """ponytail: 返回群组订阅的 BangumiSubject 完整对象, 含 broadcast_weekday.
        用于 /放送时间 表格展示(不再每次调 BGM calendar)."""
        session = self.Session()
        try:
            sub_ids = [
                sub.subject_id
                for sub in session.query(Subscription)
                .filter_by(group_id=str(group_id))
                .all()
            ]
            if not sub_ids:
                return []
            return (
                session.query(BangumiSubject)
                .filter(BangumiSubject.subject_id.in_(sub_ids))
                .all()
            )
        except Exception as e:
            logger.error(f"获取订阅番剧对象失败: {e}")
            raise DatabaseError(f"获取订阅番剧对象失败: {e}") from e
        finally:
            session.close()

    def get_monitored_subjects(self) -> list[BangumiSubject]:
        """
        获取所有已订阅的番剧列表,用于轮询更新

        Returns:
            番剧对象列表

        """
        session = self.Session()
        try:
            # Eager load subscriptions 避免 DetachedInstanceError
            subjects = (
                session.query(BangumiSubject)
                .options(joinedload(BangumiSubject.subscriptions))
                .all()
            )
            return subjects
        except Exception as e:
            logger.error(f"获取监控番剧失败: {e}")
            raise DatabaseError(f"获取监控番剧失败: {e}") from e
        finally:
            session.close()

    def update_subject_episode(self, subject_id: str, new_episode: int) -> bool:
        """
        更新番剧最新集数(快捷方法)

        Args:
            subject_id: 番剧 ID
            new_episode: 新的集数

        Returns:
            操作是否成功

        """
        return self.update_subject(subject_id, current_episode=new_episode)

    def subscribe_subject(
        self,
        group_id: str,
        subject_id: str,
        name: str,
        air_date: str = "",
        total_episodes: int = 0,
    ) -> bool:
        """
        原子性地 upsert 番剧信息并建立订阅关系

        将 update_subject + add_subscription 合并到单一事务中,
        避免两次独立调用之间发生异常导致脏数据

        Args:
            group_id: 群组 ID
            subject_id: 番剧 ID
            name: 番剧名称
            air_date: 开播日期
            total_episodes: 总集数

        Returns:
            操作是否成功
        """
        session = self.Session()
        try:
            # 1. upsert BangumiSubject
            subject = (
                session.query(BangumiSubject)
                .filter_by(subject_id=str(subject_id))
                .first()
            )
            if not subject:
                subject = BangumiSubject(
                    subject_id=str(subject_id),
                    name=name,
                    air_date=air_date,
                    total_episodes=total_episodes,
                )
                session.add(subject)
            else:
                subject.name = name
                if air_date:
                    subject.air_date = air_date
                if total_episodes:
                    subject.total_episodes = total_episodes

            # 2. 添加订阅关系(若不存在)
            existing = (
                session.query(Subscription)
                .filter_by(group_id=str(group_id), subject_id=str(subject_id))
                .first()
            )
            if not existing:
                session.add(
                    Subscription(group_id=str(group_id), subject_id=str(subject_id))
                )

            # 3. 单次 commit,保证 subject 与 subscription 同时成功或同时回滚
            session.commit()
            return True
        except Exception as e:
            logger.error(f"原子订阅失败: {e}")
            session.rollback()
            raise DatabaseError(f"原子订阅失败: {e}") from e
        finally:
            session.close()

    def get_subject_subscribers(self, subject_id: str) -> list[str]:
        """
        获取订阅了某番剧的所有群组 ID

        Args:
            subject_id: 番剧 ID

        Returns:
            群组 ID 列表

        """
        session = self.Session()
        try:
            subs = (
                session.query(Subscription).filter_by(subject_id=str(subject_id)).all()
            )
            return [sub.group_id for sub in subs]
        except Exception as e:
            logger.error(f"获取订阅群组失败: {e}")
            raise DatabaseError(f"获取订阅群组失败: {e}") from e
        finally:
            session.close()

    def set_subject_broadcast_time(
        self,
        subject_id: str,
        broadcast_time: str | None,
        broadcast_weekday: int | None = None,
    ) -> bool:
        """
        设置番剧的广播时间

        Args:
            subject_id: 番剧 ID
            broadcast_time: 播出时间,格式 "HH:MM"(支持 30h 制),如 "22:00"、"24:28"。设为 None 清除
            broadcast_weekday: ISO weekday 1-7 (周一=1); 设为 None 表示不变.

        Returns:
            操作是否成功
        """
        session = self.Session()
        try:
            subject = (
                session.query(BangumiSubject)
                .filter_by(subject_id=str(subject_id))
                .first()
            )
            if not subject:
                return False
            subject.broadcast_time = broadcast_time
            if broadcast_weekday is not None:
                subject.broadcast_weekday = broadcast_weekday
            session.commit()
            return True
        except Exception as e:
            logger.error(f"设置广播时间失败: {e}")
            session.rollback()
            raise DatabaseError(f"设置广播时间失败: {e}") from e
        finally:
            session.close()

    def get_subject_broadcast_time(self, subject_id: str) -> str | None:
        """
        获取番剧的广播时间

        Args:
            subject_id: 番剧 ID

        Returns:
            "HH:MM" 格式的时间,如 "22:00",未设置则返回 None
        """
        session = self.Session()
        try:
            subject = (
                session.query(BangumiSubject)
                .filter_by(subject_id=str(subject_id))
                .first()
            )
            if not subject:
                return None
            return subject.broadcast_time
        except Exception as e:
            logger.error(f"获取广播时间失败: {e}")
            raise DatabaseError(f"获取广播时间失败: {e}") from e
        finally:
            session.close()

    def batch_update_broadcast_times(
        self, mapping: dict[str, str | tuple[str, int | None]]
    ) -> int:
        """
        批量更新番剧广播时间(从 bgmlist API 填充用)

        Args:
            mapping: {subject_id: broadcast_time} 映射, 如 {"377130": "22:00"};
                     也可传 {(time, weekday)} 元组形式同步写入 weekday,
                     如 {"377130": ("24:28", 4)}.

        Returns:
            更新成功的数量

        Raises:
            DatabaseError: 数据库操作异常
        """
        session = self.Session()
        updated = 0
        try:
            ids = [str(sid) for sid in mapping]
            subjects = (
                session.query(BangumiSubject)
                .filter(BangumiSubject.subject_id.in_(ids))
                .all()
            )
            for subject in subjects:
                sid = subject.subject_id
                if sid not in mapping:
                    continue
                value = mapping[sid]
                if isinstance(value, tuple):
                    subject.broadcast_time = value[0]
                    subject.broadcast_weekday = value[1]
                else:
                    subject.broadcast_time = value
                updated += 1
            session.commit()
            return updated
        except Exception as e:
            logger.error(f"批量更新广播时间失败: {e}")
            session.rollback()
            raise DatabaseError(f"批量更新广播时间失败: {e}") from e
        finally:
            session.close()

    def get_subject_name(self, subject_id: str) -> str:
        """
        获取番剧名称

        Args:
            subject_id: 番剧 ID

        Returns:
            番剧名称，未找到返回 "未知番剧"

        Raises:
            DatabaseError: 数据库操作异常
        """
        session = self.Session()
        try:
            subject = (
                session.query(BangumiSubject)
                .filter_by(subject_id=str(subject_id))
                .first()
            )
            return subject.name if subject and subject.name else "未知番剧"
        except Exception as e:
            logger.error(f"获取番剧名称失败: {e}")
            return "未知番剧"
        finally:
            session.close()

    def get_all_subscribed_groups(self) -> list[str]:
        """
        获取所有拥有订阅的群组 ID

        Returns:
            群组 ID 列表

        """
        session = self.Session()
        try:
            groups = session.query(Subscription.group_id).distinct().all()
            return [g[0] for g in groups]
        except Exception as e:
            logger.error(f"获取所有订阅群组失败: {e}")
            raise DatabaseError(f"获取所有订阅群组失败: {e}") from e
        finally:
            session.close()

    def find_group_subscription_candidates(
        self, group_id: str, keyword: str, limit: int = 5
    ) -> list[BangumiSubject]:
        """
        在指定群组的订阅中查找与关键词匹配的番剧候选

        匹配优先级:
        1. subject_id 精确匹配
        2. subject_id 前缀匹配
        3. name 包含匹配(忽略大小写)
        4. name 相似度(SequenceMatcher)
        """
        session = self.Session()
        try:
            normalized_keyword = str(keyword).strip()
            if not normalized_keyword:
                return []

            keyword_lower = normalized_keyword.lower()
            search_pattern = f"%{normalized_keyword}%"
            base_query = session.query(BangumiSubject).join(
                Subscription, Subscription.subject_id == BangumiSubject.subject_id
            )
            base_query = base_query.filter(Subscription.group_id == str(group_id))

            direct_candidates = base_query.filter(
                or_(
                    BangumiSubject.subject_id == normalized_keyword,
                    BangumiSubject.subject_id.like(f"{normalized_keyword}%"),
                    BangumiSubject.name.ilike(search_pattern),
                )
            ).all()
            if direct_candidates:
                return self._rank_candidates(
                    direct_candidates,
                    normalized_keyword,
                    keyword_lower,
                    limit=limit,
                )

            all_candidates = base_query.all()
            return self._rank_candidates(
                all_candidates,
                normalized_keyword,
                keyword_lower,
                limit=limit,
                min_similarity=0.35,
            )
        except Exception as e:
            logger.error(f"查询群组订阅候选失败: {e}")
            raise DatabaseError(f"查询群组订阅候选失败: {e}") from e
        finally:
            session.close()

    @staticmethod
    def _rank_candidates(
        candidates: list[BangumiSubject],
        normalized_keyword: str,
        keyword_lower: str,
        limit: int,
        min_similarity: float = 0.0,
    ) -> list[BangumiSubject]:
        scored_candidates: list[tuple[_CandidateScore, BangumiSubject]] = []
        for subject in candidates:
            subject_id = str(subject.subject_id or "")
            name = str(subject.name or "")
            name_lower = name.lower()
            score = _CandidateScore(
                exact_id=int(subject_id == normalized_keyword),
                prefix_id=int(subject_id.startswith(normalized_keyword)),
                name_contains=int(keyword_lower in name_lower),
                similarity=SequenceMatcher(None, keyword_lower, name_lower).ratio(),
                subject_id=subject_id,
            )
            if (
                score.exact_id == 0
                and score.prefix_id == 0
                and score.name_contains == 0
                and score.similarity < min_similarity
            ):
                continue
            scored_candidates.append((score, subject))

        scored_candidates.sort(
            key=lambda item: (
                -item[0].exact_id,
                -item[0].prefix_id,
                -item[0].name_contains,
                -item[0].similarity,
                item[0].subject_id,
            )
        )
        return [subject for _, subject in scored_candidates[:limit]]
