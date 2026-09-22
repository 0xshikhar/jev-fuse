"""Unit tests for asynchronous SQLite WAL DecisionLogWriter and DuckDB DecisionLogReader."""

import asyncio
from pathlib import Path
import pytest

from arbiter.log import DecisionLogReader, DecisionLogWriter, DecisionRecord
from arbiter.schema import Action


def make_record(
    trace_id: str,
    task: str = "bash-risk",
    confidence: float = 0.92,
    action: Action = Action.ALLOW,
    value: str = "safe",
    human_label: str | None = None,
) -> DecisionRecord:
    return DecisionRecord(
        trace_id=trace_id,
        task=task,
        client_id="claude-code",
        provider="jev",
        input_hash=f"hash_{trace_id}",
        input_preview="git push origin main",
        decision_value=value,
        raw_score=confidence + 0.02,
        confidence=confidence,
        action=action,
        latency_ms=14.2,
        cached=False,
        human_label=human_label,
        context={"branch": "main"},
    )


@pytest.mark.asyncio
async def test_writer_append_and_read(tmp_path: Path):
    db_file = tmp_path / "decisions.db"
    writer = DecisionLogWriter(db_path=db_file, flush_interval_ms=10.0)

    async with writer:
        rec1 = make_record("tr_001", action=Action.ALLOW, value="safe", confidence=0.95)
        rec2 = make_record("tr_002", action=Action.DENY, value="dangerous", confidence=0.88)
        writer.log(rec1)
        writer.log(rec2)
        await writer.flush()

    reader = DecisionLogReader(db_path=db_file)
    r1 = reader.get_record("tr_001")
    assert r1 is not None
    assert r1.trace_id == "tr_001"
    assert r1.action == Action.ALLOW
    assert r1.confidence == 0.95
    assert r1.decision_value == "safe"
    assert r1.context["branch"] == "main"

    records = reader.query_records(task="bash-risk", limit=10)
    assert len(records) == 2
    reader.close()


@pytest.mark.asyncio
async def test_writer_attach_human_label(tmp_path: Path):
    db_file = tmp_path / "decisions.db"
    writer = DecisionLogWriter(db_path=db_file, flush_interval_ms=10.0)

    async with writer:
        rec = make_record("tr_unlabeled", human_label=None)
        writer.log(rec)
        await writer.flush()

        # Attach ground truth
        success = await writer.attach_label("tr_unlabeled", "dangerous")
        assert success

    reader = DecisionLogReader(db_path=db_file)
    updated = reader.get_record("tr_unlabeled")
    assert updated is not None
    assert updated.human_label == "dangerous"
    assert updated.human_labeled_at is not None
    reader.close()


@pytest.mark.asyncio
async def test_writer_concurrent_stress(tmp_path: Path):
    db_file = tmp_path / "stress.db"
    writer = DecisionLogWriter(db_path=db_file, flush_interval_ms=20.0, batch_size=25)

    async with writer:
        async def log_worker(worker_id: int):
            for i in range(10):
                rec = make_record(f"tr_w{worker_id}_{i}", confidence=0.5 + (i * 0.04))
                writer.log(rec)

        # 10 workers logging 10 records each = 100 concurrent logs
        await asyncio.gather(*(log_worker(w) for w in range(10)))
        await writer.flush()

    reader = DecisionLogReader(db_path=db_file)
    all_recs = reader.query_records(limit=200)
    assert len(all_recs) == 100
    reader.close()


@pytest.mark.asyncio
async def test_duckdb_action_distribution_and_analytics(tmp_path: Path):
    db_file = tmp_path / "analytics.db"
    writer = DecisionLogWriter(db_path=db_file, flush_interval_ms=10.0)

    async with writer:
        writer.log(make_record("t1", action=Action.ALLOW))
        writer.log(make_record("t2", action=Action.ALLOW))
        writer.log(make_record("t3", action=Action.DENY))
        writer.log(make_record("t4", action=Action.ASK))
        await writer.flush()

    reader = DecisionLogReader(db_path=db_file)
    dist = reader.get_action_distribution(task="bash-risk")
    assert dist["allow"] == 2
    assert dist["deny"] == 1
    assert dist["ask"] == 1
    reader.close()


@pytest.mark.asyncio
async def test_duckdb_uncertainty_sampling(tmp_path: Path):
    db_file = tmp_path / "uncertainty.db"
    writer = DecisionLogWriter(db_path=db_file, flush_interval_ms=10.0)

    async with writer:
        # High confidence (should not be sampled)
        writer.log(make_record("t_high", confidence=0.98))
        # Low confidence (should not be sampled)
        writer.log(make_record("t_low", confidence=0.55))
        # Uncertain decisions within [0.70, 0.85]
        writer.log(make_record("t_unc1", confidence=0.78))
        writer.log(make_record("t_unc2", confidence=0.82))
        await writer.flush()

    reader = DecisionLogReader(db_path=db_file)
    uncertain = reader.get_uncertain_decisions("bash-risk", lower_conf=0.70, upper_conf=0.85)
    assert len(uncertain) == 2
    trace_ids = {u.trace_id for u in uncertain}
    assert trace_ids == {"t_unc1", "t_unc2"}
    reader.close()


@pytest.mark.asyncio
async def test_duckdb_calibration_buckets(tmp_path: Path):
    db_file = tmp_path / "calibration.db"
    writer = DecisionLogWriter(db_path=db_file, flush_interval_ms=10.0)

    async with writer:
        # 0.90 - 1.00 bucket
        writer.log(make_record("c1", confidence=0.92, value="safe", human_label="safe"))
        writer.log(make_record("c2", confidence=0.94, value="safe", human_label="safe"))
        # 0.70 - 0.80 bucket (1 correct, 1 wrong)
        writer.log(make_record("c3", confidence=0.75, value="safe", human_label="safe"))
        writer.log(make_record("c4", confidence=0.78, value="safe", human_label="dangerous"))
        await writer.flush()

    reader = DecisionLogReader(db_path=db_file)
    buckets = reader.get_calibration_buckets("bash-risk", num_buckets=10)
    assert len(buckets) >= 2

    # Check the 0.9 bucket
    b90 = next(b for b in buckets if b["bucket_range"][0] == 0.9)
    assert b90["count"] == 2
    assert b90["empirical_accuracy"] == 1.0

    # Check the 0.7 bucket
    b70 = next(b for b in buckets if b["bucket_range"][0] == 0.7)
    assert b70["count"] == 2
    assert b70["empirical_accuracy"] == 0.5  # 1 out of 2 matched
    reader.close()
