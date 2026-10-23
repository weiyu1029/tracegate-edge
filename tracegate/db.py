"""SQLite schema, migrations, and wholly synthetic demonstration fixtures."""

from __future__ import annotations

import contextlib
import sqlite3
from collections.abc import Iterator, Sequence
from pathlib import Path
from typing import Any

from .util import utc_now


MIGRATIONS: tuple[tuple[int, str, str], ...] = (
    (
        1,
        "initial_domain_model",
        """
        CREATE TABLE IF NOT EXISTS routes (
            route_id TEXT NOT NULL,
            version INTEGER NOT NULL,
            name TEXT NOT NULL,
            status TEXT NOT NULL CHECK(status IN ('ACTIVE','RETIRED')),
            PRIMARY KEY (route_id, version)
        );

        CREATE TABLE IF NOT EXISTS route_steps (
            route_id TEXT NOT NULL,
            route_version INTEGER NOT NULL,
            sequence INTEGER NOT NULL,
            station_code TEXT NOT NULL,
            operation_code TEXT NOT NULL,
            name TEXT NOT NULL,
            on_pass_sequence INTEGER,
            on_fail_sequence INTEGER,
            requirements_json TEXT NOT NULL DEFAULT '[]',
            PRIMARY KEY (route_id, route_version, sequence),
            FOREIGN KEY (route_id, route_version)
                REFERENCES routes(route_id, version)
        );

        CREATE TABLE IF NOT EXISTS work_orders (
            work_order_id TEXT PRIMARY KEY,
            model_code TEXT NOT NULL,
            route_id TEXT NOT NULL,
            route_version INTEGER NOT NULL,
            planned_qty INTEGER NOT NULL CHECK(planned_qty > 0),
            status TEXT NOT NULL CHECK(status IN ('PLANNED','RELEASED','CLOSED')),
            created_at TEXT NOT NULL,
            FOREIGN KEY (route_id, route_version)
                REFERENCES routes(route_id, version)
        );

        CREATE TABLE IF NOT EXISTS products (
            acn TEXT PRIMARY KEY,
            work_order_id TEXT NOT NULL REFERENCES work_orders(work_order_id),
            status TEXT NOT NULL CHECK(status IN ('ACTIVE','HOLD','COMPLETE','SCRAPPED')),
            created_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS wip_state (
            acn TEXT PRIMARY KEY REFERENCES products(acn),
            current_sequence INTEGER,
            status TEXT NOT NULL CHECK(status IN
                ('READY','IN_PROGRESS','IN_REPAIR','HOLD','COMPLETE')),
            attempt_no INTEGER NOT NULL DEFAULT 1 CHECK(attempt_no > 0),
            last_event_id TEXT,
            version INTEGER NOT NULL DEFAULT 1,
            updated_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS network_interfaces (
            interface_id TEXT PRIMARY KEY,
            acn TEXT NOT NULL REFERENCES products(acn),
            interface_name TEXT NOT NULL,
            mac_address TEXT NOT NULL UNIQUE,
            status TEXT NOT NULL CHECK(status IN ('ALLOCATED','BOUND','RETIRED')),
            bound_at TEXT,
            UNIQUE(acn, interface_name)
        );

        CREATE TABLE IF NOT EXISTS materials (
            part_no TEXT PRIMARY KEY,
            description TEXT NOT NULL,
            active INTEGER NOT NULL DEFAULT 1 CHECK(active IN (0,1))
        );

        CREATE TABLE IF NOT EXISTS kp_groups (
            group_id TEXT PRIMARY KEY,
            description TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS kp_group_members (
            group_id TEXT NOT NULL REFERENCES kp_groups(group_id),
            part_no TEXT NOT NULL REFERENCES materials(part_no),
            substitute_rank INTEGER NOT NULL DEFAULT 0,
            PRIMARY KEY(group_id, part_no)
        );

        CREATE TABLE IF NOT EXISTS bom_requirements (
            work_order_id TEXT NOT NULL REFERENCES work_orders(work_order_id),
            step_sequence INTEGER NOT NULL,
            group_id TEXT NOT NULL REFERENCES kp_groups(group_id),
            required_qty INTEGER NOT NULL CHECK(required_qty > 0),
            PRIMARY KEY(work_order_id, step_sequence, group_id)
        );

        CREATE TABLE IF NOT EXISTS key_part_bindings (
            binding_id TEXT PRIMARY KEY,
            parent_acn TEXT NOT NULL REFERENCES products(acn),
            kpsn TEXT NOT NULL UNIQUE,
            part_no TEXT NOT NULL REFERENCES materials(part_no),
            group_id TEXT NOT NULL REFERENCES kp_groups(group_id),
            station_code TEXT NOT NULL,
            step_sequence INTEGER NOT NULL,
            attempt_no INTEGER NOT NULL,
            request_id TEXT NOT NULL UNIQUE,
            bound_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS verification_records (
            verification_id TEXT PRIMARY KEY,
            request_id TEXT NOT NULL UNIQUE,
            acn TEXT NOT NULL REFERENCES products(acn),
            verification_type TEXT NOT NULL,
            step_sequence INTEGER NOT NULL,
            attempt_no INTEGER NOT NULL,
            result TEXT NOT NULL CHECK(result IN ('PASS','FAIL')),
            details_json TEXT NOT NULL,
            created_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS station_events (
            event_id TEXT PRIMARY KEY,
            request_id TEXT NOT NULL UNIQUE,
            acn TEXT NOT NULL REFERENCES products(acn),
            route_id TEXT NOT NULL,
            route_version INTEGER NOT NULL,
            sequence INTEGER NOT NULL,
            station_code TEXT NOT NULL,
            operation_code TEXT NOT NULL,
            attempt_no INTEGER NOT NULL,
            result TEXT NOT NULL CHECK(result IN ('PASS','FAIL')),
            reason_code TEXT NOT NULL,
            message TEXT NOT NULL,
            evidence_json TEXT NOT NULL,
            occurred_at TEXT NOT NULL,
            delivery_status TEXT NOT NULL DEFAULT 'PENDING'
                CHECK(delivery_status IN ('PENDING','IN_FLIGHT','RETRY','DELIVERED','DEAD_LETTER')),
            delivered_at TEXT
        );

        CREATE TABLE IF NOT EXISTS scan_attempts (
            scan_id TEXT PRIMARY KEY,
            request_id TEXT NOT NULL,
            operation TEXT NOT NULL,
            input_type TEXT NOT NULL,
            raw_value TEXT,
            normalized_value TEXT,
            acn TEXT,
            station_code TEXT,
            result TEXT NOT NULL CHECK(result IN ('PASS','FAIL')),
            reason_code TEXT NOT NULL,
            occurred_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS delivery_outbox (
            event_id TEXT PRIMARY KEY REFERENCES station_events(event_id),
            payload TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'PENDING'
                CHECK(status IN ('PENDING','IN_FLIGHT','RETRY','DELIVERED','DEAD_LETTER')),
            attempts INTEGER NOT NULL DEFAULT 0,
            next_attempt_at REAL NOT NULL DEFAULT 0,
            locked_until REAL NOT NULL DEFAULT 0,
            last_error TEXT,
            created_at TEXT NOT NULL,
            delivered_at TEXT
        );

        CREATE TABLE IF NOT EXISTS defects (
            defect_id TEXT PRIMARY KEY,
            acn TEXT NOT NULL REFERENCES products(acn),
            event_id TEXT NOT NULL REFERENCES station_events(event_id),
            code TEXT NOT NULL,
            description TEXT NOT NULL,
            status TEXT NOT NULL CHECK(status IN ('OPEN','REPAIRED','CLOSED')),
            created_at TEXT NOT NULL,
            closed_at TEXT
        );

        CREATE TABLE IF NOT EXISTS repair_actions (
            action_id TEXT PRIMARY KEY,
            defect_id TEXT NOT NULL REFERENCES defects(defect_id),
            acn TEXT NOT NULL REFERENCES products(acn),
            request_id TEXT NOT NULL UNIQUE,
            action_code TEXT NOT NULL,
            notes TEXT NOT NULL,
            technician TEXT NOT NULL,
            status TEXT NOT NULL CHECK(status IN ('RECORDED','VERIFIED')),
            created_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS idempotency_records (
            request_id TEXT PRIMARY KEY,
            operation TEXT NOT NULL,
            request_hash TEXT NOT NULL,
            status_code INTEGER NOT NULL,
            response_json TEXT NOT NULL,
            created_at TEXT NOT NULL
        );

        CREATE INDEX IF NOT EXISTS ix_station_events_acn_time
            ON station_events(acn, occurred_at);
        CREATE INDEX IF NOT EXISTS ix_scan_attempts_acn_time
            ON scan_attempts(acn, occurred_at);
        CREATE INDEX IF NOT EXISTS ix_outbox_due
            ON delivery_outbox(status, next_attempt_at, locked_until);
        CREATE INDEX IF NOT EXISTS ix_bindings_parent_group
            ON key_part_bindings(parent_acn, step_sequence, attempt_no, group_id);
        CREATE INDEX IF NOT EXISTS ix_verifications_lookup
            ON verification_records(acn, verification_type, step_sequence, attempt_no, result);
        CREATE INDEX IF NOT EXISTS ix_defects_acn_status
            ON defects(acn, status);
        """,
    ),
    (
        2,
        "global_audit_sequence",
        """
        CREATE TABLE IF NOT EXISTS audit_log (
            audit_sequence INTEGER PRIMARY KEY AUTOINCREMENT,
            record_type TEXT NOT NULL,
            record_id TEXT NOT NULL UNIQUE,
            request_id TEXT,
            acn TEXT,
            occurred_at TEXT NOT NULL
        );

        CREATE INDEX IF NOT EXISTS ix_audit_log_acn_sequence
            ON audit_log(acn, audit_sequence);
        CREATE INDEX IF NOT EXISTS ix_audit_log_request
            ON audit_log(request_id);

        INSERT OR IGNORE INTO audit_log(
            record_type,record_id,request_id,acn,occurred_at
        )
        SELECT record_type,record_id,request_id,acn,occurred_at
        FROM (
            SELECT 'station_event' AS record_type,event_id AS record_id,
                   request_id,acn,occurred_at,10 AS kind_order,rowid AS local_order
            FROM station_events
            UNION ALL
            SELECT 'verification',verification_id,request_id,acn,created_at,
                   20,rowid FROM verification_records
            UNION ALL
            SELECT 'key_part',binding_id,request_id,parent_acn,bound_at,
                   30,rowid FROM key_part_bindings
            UNION ALL
            SELECT 'defect',d.defect_id,e.request_id,d.acn,d.created_at,
                   40,d.rowid FROM defects d
            JOIN station_events e ON e.event_id=d.event_id
            UNION ALL
            SELECT 'repair_action',action_id,request_id,acn,created_at,
                   50,rowid FROM repair_actions
            UNION ALL
            SELECT 'scan_attempt',scan_id,request_id,acn,occurred_at,
                   60,rowid FROM scan_attempts
        )
        ORDER BY occurred_at,kind_order,local_order;
        """,
    ),
)


class Database:
    """Owns database connections while keeping service methods stateless."""

    def __init__(self, path: str | Path):
        self.path = Path(path)

    def connect(self) -> sqlite3.Connection:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(self.path, timeout=10)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys=ON")
        connection.execute("PRAGMA busy_timeout=10000")
        return connection

    def migrate(self, *, seed: bool = True) -> None:
        with contextlib.closing(self.connect()) as connection, connection:
            connection.execute("PRAGMA journal_mode=WAL")
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS schema_migrations (
                    version INTEGER PRIMARY KEY,
                    name TEXT NOT NULL,
                    applied_at TEXT NOT NULL
                )
                """
            )
            # executescript() commits any pending transaction before running;
            # make that boundary explicit, then wrap each migration and its
            # version record in one transaction inside the script itself.
            connection.commit()
            applied = {
                row[0]
                for row in connection.execute(
                    "SELECT version FROM schema_migrations"
                ).fetchall()
            }
            for version, name, sql in MIGRATIONS:
                if version in applied:
                    continue
                safe_name = name.replace("'", "''")
                safe_time = utc_now().replace("'", "''")
                try:
                    connection.executescript(
                        "BEGIN IMMEDIATE;\n"
                        f"{sql}\n"
                        "INSERT INTO schema_migrations(version,name,applied_at) "
                        f"VALUES({int(version)},'{safe_name}','{safe_time}');\n"
                        "COMMIT;"
                    )
                except BaseException:
                    if connection.in_transaction:
                        connection.rollback()
                    raise
        if seed:
            self.seed_demo()

    @contextlib.contextmanager
    def transaction(self, *, immediate: bool = False) -> Iterator[sqlite3.Connection]:
        connection = self.connect()
        try:
            connection.execute("BEGIN IMMEDIATE" if immediate else "BEGIN")
            yield connection
            connection.commit()
        except BaseException:
            connection.rollback()
            raise
        finally:
            connection.close()

    def query_one(
        self, sql: str, parameters: Sequence[Any] = ()
    ) -> dict[str, Any] | None:
        with contextlib.closing(self.connect()) as connection:
            row = connection.execute(sql, parameters).fetchone()
        return dict(row) if row else None

    def query_all(
        self, sql: str, parameters: Sequence[Any] = ()
    ) -> list[dict[str, Any]]:
        with contextlib.closing(self.connect()) as connection:
            rows = connection.execute(sql, parameters).fetchall()
        return [dict(row) for row in rows]

    def seed_demo(self, *, connection: sqlite3.Connection | None = None) -> None:
        """Insert an isolated, fictional production route if it is absent."""

        now = utc_now()
        manager = (
            self.transaction(immediate=True)
            if connection is None
            else contextlib.nullcontext(connection)
        )
        with manager as connection:
            exists = connection.execute(
                "SELECT 1 FROM routes WHERE route_id='ROUTE-DEMO-A' AND version=1"
            ).fetchone()
            if exists:
                return

            connection.execute(
                "INSERT INTO routes(route_id,version,name,status) VALUES(?,?,?,?)",
                ("ROUTE-DEMO-A", 1, "Synthetic edge-controller route", "ACTIVE"),
            )
            steps = (
                (10, "ST-IN", "REGISTER", "Unit intake", 20, None, "[]"),
                (20, "ST-ID", "VERIFY_MAC", "Interface identity", 30, None, '["MAC"]'),
                (30, "ST-KP", "COLLECT_KP", "Key-part collection", 40, None, '["KEY_PARTS"]'),
                (40, "ST-FT", "FUNCTION_TEST", "Functional test", 60, 50, "[]"),
                (50, "ST-RP", "REPAIR", "Repair and disposition", 40, 50, '["REPAIR_ACTION"]'),
                (60, "ST-PK", "PACK", "Pack verification", 70, None, "[]"),
                (70, "ST-OUT", "COMPLETE", "Route completion", None, None, "[]"),
            )
            connection.executemany(
                """
                INSERT INTO route_steps(
                    route_id,route_version,sequence,station_code,operation_code,
                    name,on_pass_sequence,on_fail_sequence,requirements_json
                ) VALUES('ROUTE-DEMO-A',1,?,?,?,?,?,?,?)
                """,
                steps,
            )
            connection.execute(
                """
                INSERT INTO work_orders(
                    work_order_id,model_code,route_id,route_version,planned_qty,status,created_at
                ) VALUES(?,?,?,?,?,?,?)
                """,
                (
                    "WO-DEMO-1001",
                    "EDGE-CONTROLLER-A",
                    "ROUTE-DEMO-A",
                    1,
                    3,
                    "RELEASED",
                    now,
                ),
            )
            products = tuple(
                (f"ACN-DEMO-{index:04d}", "WO-DEMO-1001", "ACTIVE", now)
                for index in range(1, 4)
            )
            connection.executemany(
                "INSERT INTO products(acn,work_order_id,status,created_at) VALUES(?,?,?,?)",
                products,
            )
            connection.executemany(
                """
                INSERT INTO wip_state(
                    acn,current_sequence,status,attempt_no,last_event_id,version,updated_at
                ) VALUES(?,10,'READY',1,NULL,1,?)
                """,
                ((product[0], now) for product in products),
            )
            interfaces: list[tuple[str, str, str, str, str, str]] = []
            for index, product in enumerate(products, start=1):
                interfaces.extend(
                    (
                        (
                            f"IF-{index:04d}-WLAN",
                            product[0],
                            "wlan0",
                            f"02:54:47:00:{index:02X}:01",
                            "BOUND",
                            now,
                        ),
                        (
                            f"IF-{index:04d}-ETH",
                            product[0],
                            "eth0",
                            f"02:54:47:00:{index:02X}:02",
                            "BOUND",
                            now,
                        ),
                    )
                )
            connection.executemany(
                """
                INSERT INTO network_interfaces(
                    interface_id,acn,interface_name,mac_address,status,bound_at
                ) VALUES(?,?,?,?,?,?)
                """,
                interfaces,
            )
            connection.executemany(
                "INSERT INTO materials(part_no,description,active) VALUES(?,?,1)",
                (
                    ("CTRL-A100", "Synthetic controller module"),
                    ("CTRL-A101", "Approved controller substitute"),
                    ("SENSOR-T200", "Synthetic temperature sensor"),
                    ("SENSOR-T201", "Approved sensor substitute"),
                    ("UNLISTED-X999", "Known but not approved for this BOM"),
                ),
            )
            connection.executemany(
                "INSERT INTO kp_groups(group_id,description) VALUES(?,?)",
                (
                    ("KP-CONTROLLER", "Controller alternatives"),
                    ("KP-SENSOR", "Sensor alternatives"),
                ),
            )
            connection.executemany(
                """
                INSERT INTO kp_group_members(group_id,part_no,substitute_rank)
                VALUES(?,?,?)
                """,
                (
                    ("KP-CONTROLLER", "CTRL-A100", 0),
                    ("KP-CONTROLLER", "CTRL-A101", 1),
                    ("KP-SENSOR", "SENSOR-T200", 0),
                    ("KP-SENSOR", "SENSOR-T201", 1),
                ),
            )
            connection.executemany(
                """
                INSERT INTO bom_requirements(
                    work_order_id,step_sequence,group_id,required_qty
                ) VALUES('WO-DEMO-1001',30,?,?)
                """,
                (("KP-CONTROLLER", 1), ("KP-SENSOR", 2)),
            )

    def reset_demo(self) -> None:
        """Reset only the repository's synthetic fixtures and operational data."""

        with self.transaction(immediate=True) as connection:
            for table in (
                "audit_log",
                "idempotency_records",
                "repair_actions",
                "defects",
                "delivery_outbox",
                "scan_attempts",
                "station_events",
                "verification_records",
                "key_part_bindings",
                "bom_requirements",
                "kp_group_members",
                "kp_groups",
                "materials",
                "network_interfaces",
                "wip_state",
                "products",
                "work_orders",
                "route_steps",
                "routes",
            ):
                connection.execute(f"DELETE FROM {table}")
            self.seed_demo(connection=connection)
