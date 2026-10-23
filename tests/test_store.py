import json
import os
import subprocess
import sys
from datetime import UTC, datetime, time, timedelta
from pathlib import Path

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from alarm_clock.model import ONCE, WEEKDAYS, AlarmError, AlarmState
from alarm_clock.store import AlarmStore, StateStore, StoreError, atomic_write_json

T0 = datetime(2026, 10, 23, 12, 0, tzinfo=UTC)
LATER = T0 + timedelta(hours=1)


@pytest.fixture
def alarms(tmp_path: Path) -> AlarmStore:
    return AlarmStore(tmp_path / "alarms.json")


def test_missing_file_means_no_alarms(alarms: AlarmStore) -> None:
    assert alarms.load() == []
    assert alarms.signature() is None


def test_round_trip(alarms: AlarmStore) -> None:
    added = alarms.add(time(7, 30), WEEKDAYS, "Standup", 9, now=T0)
    assert added.id == 1
    assert added.anchor == T0
    assert AlarmStore(alarms.path).load() == [added]


def test_file_is_human_readable(alarms: AlarmStore) -> None:
    alarms.add(time(7, 30), frozenset({0, 2}), "Gym", 5, now=T0)
    doc = json.loads(alarms.path.read_text())
    assert doc["schema_version"] == 1
    assert doc["alarms"][0]["at"] == "07:30"
    assert doc["alarms"][0]["days"] == ["mon", "wed"]


def test_ids_are_never_reused(alarms: AlarmStore) -> None:
    alarms.add(time(7, 0), ONCE, "", 9, now=T0)
    second = alarms.add(time(8, 0), ONCE, "", 9, now=T0)
    alarms.remove(second.id)
    assert alarms.add(time(9, 0), ONCE, "", 9, now=T0).id == 3


@settings(max_examples=50)
@given(ops=st.lists(st.one_of(st.just("add"), st.integers(0, 10)), max_size=25))
def test_no_id_is_ever_issued_twice(
    tmp_path_factory: pytest.TempPathFactory, ops: list[object]
) -> None:
    """Any interleaving of adds and removes (by position in the current list)."""
    store = AlarmStore(tmp_path_factory.mktemp("ids") / "alarms.json")
    issued: list[int] = []
    for op in ops:
        current = store.load()
        if op == "add":
            new_id = store.add(time(7, 0), ONCE, "", 9, now=T0).id
            assert new_id not in issued
            issued.append(new_id)
        elif isinstance(op, int) and current:
            store.remove(current[op % len(current)].id)


def test_unknown_id_is_a_user_error(alarms: AlarmStore) -> None:
    with pytest.raises(AlarmError, match="no alarm with id 7"):
        alarms.get(7)
    with pytest.raises(AlarmError, match="no alarm with id 7"):
        alarms.remove(7)
    with pytest.raises(AlarmError, match="no alarm with id 7"):
        alarms.update(7, T0, label="x")


def test_changing_when_it_fires_re_arms(alarms: AlarmStore) -> None:
    a = alarms.add(time(7, 30), WEEKDAYS, "", 9, now=T0)
    assert alarms.update(a.id, LATER, at=time(8, 0)).anchor == LATER
    b = alarms.add(time(7, 30), WEEKDAYS, "", 9, now=T0)
    assert alarms.update(b.id, LATER, days=ONCE).anchor == LATER


def test_label_or_snooze_change_keeps_the_schedule(alarms: AlarmStore) -> None:
    a = alarms.add(time(7, 30), WEEKDAYS, "", 9, now=T0)
    updated = alarms.update(a.id, LATER, label="Standup", snooze_minutes=5)
    assert (updated.label, updated.snooze_minutes, updated.anchor) == ("Standup", 5, T0)


def test_setting_the_same_time_is_not_a_change(alarms: AlarmStore) -> None:
    a = alarms.add(time(7, 30), WEEKDAYS, "", 9, now=T0)
    assert alarms.update(a.id, LATER, at=time(7, 30)).anchor == T0


def test_disable_then_enable_re_arms_from_now(alarms: AlarmStore) -> None:
    a = alarms.add(time(7, 30), WEEKDAYS, "", 9, now=T0)
    assert alarms.set_enabled(a.id, False, LATER).enabled is False
    later_still = LATER + timedelta(days=2)
    enabled = alarms.set_enabled(a.id, True, later_still)
    assert (enabled.enabled, enabled.anchor) == (True, later_still)


def test_enabling_an_enabled_recurring_alarm_is_a_no_op(alarms: AlarmStore) -> None:
    a = alarms.add(time(7, 30), WEEKDAYS, "", 9, now=T0)
    before = alarms.signature()
    assert alarms.set_enabled(a.id, True, LATER).anchor == T0
    assert alarms.signature() == before  # nothing was written


def test_enabling_a_one_shot_re_arms_it(alarms: AlarmStore) -> None:
    a = alarms.add(time(7, 30), ONCE, "", 9, now=T0)
    assert alarms.set_enabled(a.id, True, LATER).anchor == LATER


def _doc(**overrides: object) -> str:
    entry = {
        "id": 1,
        "at": "07:30",
        "days": [],
        "label": "",
        "enabled": True,
        "snooze_minutes": 9,
        "anchor": "2026-10-23T12:00:00+00:00",
    }
    entry.update(overrides)
    return json.dumps({"schema_version": 1, "next_id": 2, "alarms": [entry]})


@pytest.mark.parametrize(
    ("content", "reason"),
    [
        ("{not json", "not valid JSON"),
        ("[]", "must contain a JSON object"),
        ('{"schema_version": 99, "alarms": []}', "schema_version 99"),
        ('{"alarms": []}', "schema_version None"),
        ('{"schema_version": 1, "next_id": 2, "alarms": [{"id": 1}]}', "malformed entry"),
        (_doc(enabled="false"), "enabled must be bool"),
        (_doc(snooze_minutes=0), "snooze_minutes must be 1-60"),
        (_doc(snooze_minutes=9.5), "snooze_minutes must be int"),
        (_doc(id=True), "id must be int"),
        (_doc(days=["mon", "funday"]), "malformed entry"),
        (
            '{"schema_version": 1, "next_id": 2, "alarms": [{"id": 1, "at": "25:00",'
            ' "days": [], "label": "", "enabled": true, "snooze_minutes": 9,'
            ' "anchor": "2026-10-23T12:00:00+00:00"}]}',
            "malformed entry",
        ),
    ],
)
def test_unusable_file_is_reported_and_left_untouched(
    alarms: AlarmStore, content: str, reason: str
) -> None:
    alarms.path.write_text(content)
    with pytest.raises(StoreError, match=reason) as excinfo:
        alarms.load()
    assert str(alarms.path) in str(excinfo.value)
    assert alarms.path.read_text() == content


def test_naive_anchor_is_malformed(alarms: AlarmStore) -> None:
    alarms.add(time(7, 30), ONCE, "", 9, now=T0)
    doc = json.loads(alarms.path.read_text())
    doc["alarms"][0]["anchor"] = "2026-10-23T12:00:00"
    alarms.path.write_text(json.dumps(doc))
    with pytest.raises(StoreError, match="without a UTC offset"):
        alarms.load()


def test_failed_write_keeps_the_old_file_and_leaves_no_temp_files(
    alarms: AlarmStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    alarms.add(time(7, 30), ONCE, "Original", 9, now=T0)
    before = alarms.path.read_bytes()

    def boom(src: str, dst: str) -> None:
        raise OSError("disk full")

    monkeypatch.setattr(os, "replace", boom)
    with pytest.raises(OSError, match="disk full"):
        alarms.add(time(8, 0), ONCE, "Lost", 9, now=T0)
    assert alarms.path.read_bytes() == before
    assert not [p for p in alarms.path.parent.iterdir() if p.suffix == ".tmp"]


def test_unserialisable_data_leaves_no_temp_files(tmp_path: Path) -> None:
    target = tmp_path / "x.json"
    with pytest.raises(TypeError):
        atomic_write_json(target, {"bad": object()})
    assert list(tmp_path.iterdir()) == []


def test_state_round_trip(tmp_path: Path) -> None:
    store = StateStore(tmp_path / "state.json")
    assert store.load() == {}
    states = {
        1: AlarmState(last_handled=T0),
        4: AlarmState(last_handled=T0, snoozed_occurrence=T0, snoozed_until=LATER),
        7: AlarmState(),
    }
    store.save(states)
    assert StateStore(store.path).load() == states


def test_state_malformed_is_reported(tmp_path: Path) -> None:
    path = tmp_path / "state.json"
    path.write_text('{"schema_version": 1, "alarms": {"1": {"last_handled": "yesterday"}}}')
    with pytest.raises(StoreError, match="malformed entry"):
        StateStore(path).load()


def test_signature_changes_on_every_write(alarms: AlarmStore) -> None:
    alarms.add(time(7, 30), ONCE, "", 9, now=T0)
    first = alarms.signature()
    alarms.update(1, T0, label="same size?")
    assert alarms.signature() != first


ADD_FIVE = """
import sys
from datetime import UTC, datetime, time
from pathlib import Path
from alarm_clock.store import AlarmStore
store = AlarmStore(Path(sys.argv[1]))
for _ in range(5):
    store.add(time(7, 0), frozenset(), "", 9, now=datetime.now(UTC))
"""


def test_concurrent_cli_processes_lose_nothing(alarms: AlarmStore) -> None:
    """Eight real processes adding five alarms each, all at once."""
    procs = [subprocess.Popen([sys.executable, "-c", ADD_FIVE, str(alarms.path)]) for _ in range(8)]
    assert [p.wait() for p in procs] == [0] * 8
    ids = [a.id for a in alarms.load()]
    assert sorted(ids) == list(range(1, 41))
