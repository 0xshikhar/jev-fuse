"""Analytical Decision Log reader powered by DuckDB over the SQLite WAL store."""

from pathlib import Path
from typing import Any
import duckdb

from arbiter.log.models import DecisionRecord


class DecisionLogReader:
    """Read-heavy analytical query engine for calibration, drift monitoring, and audit trails."""

    def __init__(self, db_path: str | Path):
        self._db_path = str(db_path)
        self._conn: duckdb.DuckDBPyConnection | None = None

    def _get_conn(self) -> duckdb.DuckDBPyConnection:
        if self._conn is not None:
            return self._conn

        conn = duckdb.connect()
        # Attach SQLite database read-only for high-performance OLAP analytics
        conn.execute(f"ATTACH '{self._db_path}' AS db (TYPE SQLITE);")
        self._conn = conn
        return self._conn

    def get_record(self, trace_id: str) -> DecisionRecord | None:
        """Fetch a single decision record by trace ID."""
        conn = self._get_conn()
        res = conn.execute("SELECT * FROM db.decisions WHERE trace_id = ?", [trace_id]).fetchall()
        if not res:
            return None
        col_names = [desc[0] for desc in conn.description]
        row_dict = dict(zip(col_names, res[0]))
        return DecisionRecord.from_row(row_dict)

    def query_records(
        self,
        task: str | None = None,
        action: str | None = None,
        client_id: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> list[DecisionRecord]:
        """Query recent decision records with optional filters."""
        conn = self._get_conn()
        clauses = []
        params = []

        if task:
            clauses.append("task = ?")
            params.append(task)
        if action:
            clauses.append("action = ?")
            params.append(action)
        if client_id:
            clauses.append("client_id = ?")
            params.append(client_id)

        where_sql = ("WHERE " + " AND ".join(clauses)) if clauses else ""
        query = f"""
        SELECT * FROM db.decisions
        {where_sql}
        ORDER BY timestamp_ns DESC
        LIMIT ? OFFSET ?;
        """
        params.extend([limit, offset])

        rows = conn.execute(query, params).fetchall()
        col_names = [desc[0] for desc in conn.description]
        return [DecisionRecord.from_row(dict(zip(col_names, r))) for r in rows]

    def get_action_distribution(
        self,
        task: str | None = None,
        since_ns: int | None = None,
    ) -> dict[str, int]:
        """Aggregate action distribution (ALLOW, ASK, DENY, etc.) for a task."""
        conn = self._get_conn()
        clauses = []
        params = []

        if task:
            clauses.append("task = ?")
            params.append(task)
        if since_ns:
            clauses.append("timestamp_ns >= ?")
            params.append(since_ns)

        where_sql = ("WHERE " + " AND ".join(clauses)) if clauses else ""
        query = f"""
        SELECT action, COUNT(*) as count
        FROM db.decisions
        {where_sql}
        GROUP BY action;
        """
        rows = conn.execute(query, params).fetchall()
        return {action: count for action, count in rows}

    def get_uncertain_decisions(
        self,
        task: str,
        lower_conf: float = 0.70,
        upper_conf: float = 0.85,
        limit: int = 50,
    ) -> list[DecisionRecord]:
        """Active learning sampling: fetch unreviewed decisions closest to decision thresholds."""
        conn = self._get_conn()
        query = """
        SELECT * FROM db.decisions
        WHERE task = ?
          AND confidence >= ?
          AND confidence <= ?
          AND human_label IS NULL
        ORDER BY ABS(confidence - 0.80) ASC, timestamp_ns DESC
        LIMIT ?;
        """
        rows = conn.execute(query, [task, lower_conf, upper_conf, limit]).fetchall()
        col_names = [desc[0] for desc in conn.description]
        return [DecisionRecord.from_row(dict(zip(col_names, r))) for r in rows]

    def get_labeled_dataset(self, task: str) -> list[DecisionRecord]:
        """Fetch all decisions containing ground-truth human labels for a task."""
        conn = self._get_conn()
        query = """
        SELECT * FROM db.decisions
        WHERE task = ? AND human_label IS NOT NULL
        ORDER BY timestamp_ns ASC;
        """
        rows = conn.execute(query, [task]).fetchall()
        col_names = [desc[0] for desc in conn.description]
        return [DecisionRecord.from_row(dict(zip(col_names, r))) for r in rows]

    def get_calibration_buckets(
        self,
        task: str,
        num_buckets: int = 10,
    ) -> list[dict[str, Any]]:
        """
        Compute predicted vs. empirical accuracy across confidence intervals.
        Requires human_label to compute observed accuracy.
        """
        conn = self._get_conn()
        bucket_size = 1.0 / num_buckets

        query = """
        SELECT
            CAST(FLOOR(confidence / ?) * ? AS REAL) as bucket_floor,
            COUNT(*) as total_count,
            AVG(confidence) as avg_predicted_conf,
            AVG(CASE WHEN human_label = decision_value THEN 1.0 ELSE 0.0 END) as empirical_acc
        FROM db.decisions
        WHERE task = ? AND human_label IS NOT NULL
        GROUP BY bucket_floor
        ORDER BY bucket_floor ASC;
        """
        rows = conn.execute(query, [bucket_size, bucket_size, task]).fetchall()

        buckets = []
        for b_floor, count, avg_conf, emp_acc in rows:
            buckets.append({
                "bucket_range": [round(b_floor, 2), round(b_floor + bucket_size, 2)],
                "count": count,
                "avg_predicted_confidence": round(avg_conf, 4) if avg_conf else 0.0,
                "empirical_accuracy": round(emp_acc, 4) if emp_acc is not None else 0.0,
            })
        return buckets

    def get_total_decisions(self, task: str | None = None) -> int:
        """Count total decisions recorded, optionally filtered by task."""
        conn = self._get_conn()
        if task:
            res = conn.execute("SELECT COUNT(*) FROM db.decisions WHERE task = ?", [task]).fetchone()
        else:
            res = conn.execute("SELECT COUNT(*) FROM db.decisions").fetchone()
        return res[0] if res else 0

    def get_recent_decisions(self, limit: int = 10) -> list[DecisionRecord]:
        """Fetch the most recent N decisions for dashboard display."""
        return self.query_records(limit=limit)

    def calculate_ece(self, task: str | None = None, num_bins: int = 10) -> float:
        """
        Compute Expected Calibration Error (ECE) across predictions with ground-truth labels.
        Returns 0.0 if insufficient labeled data.
        """
        conn = self._get_conn()
        where_sql = "WHERE human_label IS NOT NULL" + (" AND task = ?" if task else "")
        params = [task] if task else []

        query = f"""
        SELECT COUNT(*) as total_labeled
        FROM db.decisions
        {where_sql};
        """
        res = conn.execute(query, params).fetchone()
        total_labeled = res[0] if res else 0
        if total_labeled == 0:
            return 0.0

        bucket_size = 1.0 / num_bins
        bucket_query = f"""
        SELECT
            COUNT(*) as bin_count,
            AVG(confidence) as avg_conf,
            AVG(CASE WHEN human_label = decision_value THEN 1.0 ELSE 0.0 END) as empirical_acc
        FROM db.decisions
        {where_sql}
        GROUP BY CAST(FLOOR(confidence / {bucket_size}) * {bucket_size} AS REAL);
        """
        rows = conn.execute(bucket_query, params).fetchall()
        ece = 0.0
        for bin_count, avg_conf, emp_acc in rows:
            weight = bin_count / total_labeled
            ece += weight * abs((avg_conf or 0.0) - (emp_acc or 0.0))
        return round(ece, 4)

    def close(self) -> None:
        """Close DuckDB analytical connection."""
        if self._conn is not None:
            self._conn.close()
            self._conn = None
