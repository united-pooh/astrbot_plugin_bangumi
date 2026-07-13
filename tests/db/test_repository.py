import sqlite3
from pathlib import Path

import pytest

from astrbot_plugin_bangumi.src.db import BangumiRepository
from astrbot_plugin_bangumi.src.domain.exceptions import DatabaseError


def _build_repository(tmp_path: Path) -> BangumiRepository:
    return BangumiRepository(str(tmp_path / "data" / "bangumi.db"))


def test_repository_subscription_crud(tmp_path: Path) -> None:
    repository = _build_repository(tmp_path)

    assert repository.add_subscription("group_1", "1") is True
    assert repository.get_subscriptions("group_1") == ["1"]
    assert repository.get_subject_subscribers("1") == ["group_1"]
    assert repository.get_all_subscribed_groups() == ["group_1"]
    assert repository.remove_subscription("group_1", "1") is True
    assert repository.remove_subscription("group_1", "1") is False


def test_subscribe_subject_upserts_subject_and_is_idempotent(tmp_path: Path) -> None:
    repository = _build_repository(tmp_path)

    assert repository.subscribe_subject("group_1", "1", "旧名", "2024-01-01", 12)
    assert repository.subscribe_subject("group_1", "1", "新名", "2024-02-01", 24)

    subjects = repository.get_monitored_subjects()
    assert len(subjects) == 1
    assert str(subjects[0].name) == "新名"
    assert int(subjects[0].total_episodes) == 24
    assert repository.get_subscriptions("group_1") == ["1"]


def test_remove_last_subscription_deletes_monitored_subject(tmp_path: Path) -> None:
    repository = _build_repository(tmp_path)

    assert repository.subscribe_subject("g1", "100", "Subject 100")

    assert repository.remove_subscription("g1", "100") is True
    assert repository.get_monitored_subjects() == []


def test_remove_one_of_multiple_subscriptions_keeps_subject(tmp_path: Path) -> None:
    repository = _build_repository(tmp_path)

    assert repository.subscribe_subject("g1", "100", "Subject 100")
    assert repository.subscribe_subject("g2", "100", "Subject 100")

    assert repository.remove_subscription("g1", "100") is True
    assert repository.get_subject_subscribers("100") == ["g2"]
    subjects = repository.get_monitored_subjects()
    assert len(subjects) == 1
    assert str(subjects[0].subject_id) == "100"


def test_update_subject_ignores_none_values(tmp_path: Path) -> None:
    repository = _build_repository(tmp_path)

    repository.update_subject("1", name="初始", current_episode=1)
    repository.update_subject("1", name=None, current_episode=2)

    subject = repository.get_monitored_subjects()[0]
    assert str(subject.name) == "初始"
    assert int(subject.current_episode) == 2


def test_broadcast_time_crud_and_batch_update(tmp_path: Path) -> None:
    repository = _build_repository(tmp_path)
    assert repository.subscribe_subject("group_1", "100", "Alpha")
    assert repository.subscribe_subject("group_1", "200", "Beta")

    assert repository.get_subject_broadcast_time("100") is None
    assert repository.set_subject_broadcast_time("100", "22:00") is True
    assert repository.get_subject_broadcast_time("100") == "22:00"
    assert repository.set_subject_broadcast_time("missing", "23:00") is False

    assert (
        repository.batch_update_broadcast_times(
            {"100": "23:30", "200": "18:00", "missing": "01:00"}
        )
        == 2
    )
    assert repository.get_subject_broadcast_time("100") == "23:30"
    assert repository.get_subject_broadcast_time("200") == "18:00"
    assert repository.get_subject_name("100") == "Alpha"
    assert repository.get_subject_name("missing") == "未知番剧"


def test_manual_lock_blocks_batch_update_and_clears_on_reset(
    tmp_path: Path,
) -> None:
    repository = _build_repository(tmp_path)
    assert repository.subscribe_subject("group_1", "100", "Alpha")
    assert repository.subscribe_subject("group_1", "200", "Beta")

    assert repository.set_subject_broadcast_time("100", "22:00") is True
    assert (
        repository.batch_update_broadcast_times({"100": "23:30", "200": "18:00"}) == 1
    )
    assert repository.get_subject_broadcast_time("100") == "22:00"
    assert repository.get_subject_broadcast_time("200") == "18:00"

    assert repository.set_subject_broadcast_time("100", None) is True
    assert repository.batch_update_broadcast_times({"100": "23:30"}) == 1
    assert repository.get_subject_broadcast_time("100") == "23:30"


def test_init_db_migrates_existing_database_with_missing_broadcast_time(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "data" / "bangumi.db"
    db_path.parent.mkdir(parents=True)
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            "CREATE TABLE bangumi_subjects ("
            "subject_id VARCHAR PRIMARY KEY, "
            "name VARCHAR, "
            "air_date VARCHAR, "
            "total_episodes INTEGER, "
            "current_episode INTEGER, "
            "updated_at DATETIME)"
        )

    repository = BangumiRepository(str(db_path))

    assert repository.set_subject_broadcast_time("missing", "22:00") is False
    with sqlite3.connect(db_path) as conn:
        columns = [
            row[1] for row in conn.execute("PRAGMA table_info(bangumi_subjects)")
        ]
    assert "broadcast_time" in columns


def test_run_migrations_raises_database_error_on_schema_failure() -> None:
    with pytest.raises(DatabaseError, match="数据库迁移失败"):
        BangumiRepository._run_migrations(object())  # type: ignore[arg-type]


def test_find_group_subscription_candidates_ranking_and_group_scope(
    tmp_path: Path,
) -> None:
    repository = _build_repository(tmp_path)
    repository.subscribe_subject("group_1", "123", "Alpha", "", 0)
    repository.subscribe_subject("group_1", "1234", "Alpha Extra", "", 0)
    repository.subscribe_subject("group_1", "2", "Beta Alpha", "", 0)
    repository.subscribe_subject("group_2", "999", "Alpha", "", 0)

    candidates = repository.find_group_subscription_candidates("group_1", "123")

    assert [str(subject.subject_id) for subject in candidates] == ["123", "1234"]
