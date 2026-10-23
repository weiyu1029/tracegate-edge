using System.Globalization;
using System.Security.Cryptography;
using System.Text;
using System.Text.Json;
using System.Text.RegularExpressions;
using Microsoft.Data.Sqlite;

namespace TraceGate.MesLab;

/// <summary>A deliberately small transactional MES learning model. All data is synthetic.</summary>
public sealed class MesStore
{
    public static readonly RouteStep[] Route = [new("ASSEMBLY", "Assembly"), new("TEST", "Functional test"), new("PACK", "Packing")];
    private static readonly JsonSerializerOptions JsonOptions = new(JsonSerializerDefaults.Web);
    private readonly string _connectionString;
    private readonly bool _seedDemo;
    private readonly object _writeLock = new();

    public MesStore(IWebHostEnvironment environment)
    {
        var configuredPath = Environment.GetEnvironmentVariable("MES_DB_PATH");
        var path = Path.GetFullPath(string.IsNullOrWhiteSpace(configuredPath)
            ? Path.Combine(environment.ContentRootPath, "App_Data", "tracegate-mes-lab.db") : configuredPath);
        Directory.CreateDirectory(Path.GetDirectoryName(path)!);
        _connectionString = new SqliteConnectionStringBuilder { DataSource = path, Mode = SqliteOpenMode.ReadWriteCreate, ForeignKeys = true, DefaultTimeout = 10 }.ToString();
        _seedDemo = !string.Equals(Environment.GetEnvironmentVariable("MES_SEED_DEMO"), "false", StringComparison.OrdinalIgnoreCase);
    }

    public void Initialize()
    {
        lock (_writeLock)
        {
            using var connection = Open();
            Execute(connection, null, """
                PRAGMA journal_mode=WAL;
                CREATE TABLE IF NOT EXISTS work_orders (
                    work_order_no TEXT PRIMARY KEY,
                    product_code TEXT NOT NULL,
                    planned_quantity INTEGER NOT NULL CHECK(planned_quantity BETWEEN 1 AND 1000),
                    created_at_utc TEXT NOT NULL,
                    created_by TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS units (
                    serial_number TEXT PRIMARY KEY,
                    work_order_no TEXT NOT NULL REFERENCES work_orders(work_order_no),
                    current_step INTEGER NOT NULL DEFAULT 0 CHECK(current_step BETWEEN 0 AND 3),
                    status TEXT NOT NULL CHECK(status IN ('Ready','Held','Completed')),
                    created_at_utc TEXT NOT NULL,
                    updated_at_utc TEXT NOT NULL,
                    CHECK((status = 'Completed' AND current_step = 3) OR (status <> 'Completed' AND current_step < 3))
                );
                CREATE TABLE IF NOT EXISTS station_events (
                    event_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    work_order_no TEXT NOT NULL REFERENCES work_orders(work_order_no),
                    serial_number TEXT NOT NULL REFERENCES units(serial_number),
                    event_type TEXT NOT NULL CHECK(event_type IN ('Registered','StationPassed','StationFailed','HoldReleased')),
                    station TEXT,
                    result TEXT,
                    operator TEXT NOT NULL,
                    reason TEXT,
                    created_at_utc TEXT NOT NULL,
                    request_id TEXT NOT NULL UNIQUE
                );
                CREATE TABLE IF NOT EXISTS idempotency (
                    request_id TEXT PRIMARY KEY,
                    payload_hash TEXT NOT NULL,
                    status_code INTEGER NOT NULL,
                    response_json TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS ix_units_work_order ON units(work_order_no);
                CREATE INDEX IF NOT EXISTS ix_events_serial ON station_events(serial_number,event_id);
                CREATE INDEX IF NOT EXISTS ix_events_work_order ON station_events(work_order_no,event_id);
                CREATE TRIGGER IF NOT EXISTS station_events_no_update BEFORE UPDATE ON station_events
                    BEGIN SELECT RAISE(ABORT, 'Station events are append-only'); END;
                CREATE TRIGGER IF NOT EXISTS station_events_no_delete BEFORE DELETE ON station_events
                    BEGIN SELECT RAISE(ABORT, 'Station events are append-only'); END;
                """);
            if (_seedDemo && ScalarLong(connection, null, "SELECT COUNT(*) FROM work_orders") == 0)
                Seed(connection);
        }
    }

    public void CheckHealth()
    {
        using var connection = Open();
        ScalarLong(connection, null, "SELECT 1");
    }

    public object Dashboard()
    {
        using var connection = Open();
        using var command = Command(connection, null, """
            SELECT (SELECT COUNT(*) FROM work_orders), (SELECT COUNT(*) FROM units),
              (SELECT COUNT(*) FROM units WHERE status='Completed'),
              (SELECT COUNT(*) FROM units WHERE status='Held'), (SELECT COUNT(*) FROM station_events)
            """);
        using var reader = command.ExecuteReader();
        reader.Read();
        return new { workOrders = reader.GetInt32(0), units = reader.GetInt32(1), completedUnits = reader.GetInt32(2), heldUnits = reader.GetInt32(3), events = reader.GetInt32(4) };
    }

    public IReadOnlyList<WorkOrderView> WorkOrders()
    {
        using var connection = Open();
        using var command = Command(connection, null, WorkOrderSql + " ORDER BY w.created_at_utc DESC,w.work_order_no");
        using var reader = command.ExecuteReader();
        var items = new List<WorkOrderView>();
        while (reader.Read()) items.Add(ReadWorkOrder(reader));
        return items;
    }

    public IReadOnlyList<UnitView> Units(string workOrderNo)
    {
        var number = Identifier(workOrderNo, "workOrderNo", 40);
        using var connection = Open();
        GetWorkOrder(connection, null, number);
        using var command = Command(connection, null, UnitSql + " WHERE u.work_order_no=$number ORDER BY u.serial_number", ("$number", number));
        using var reader = command.ExecuteReader();
        var items = new List<UnitView>();
        while (reader.Read()) items.Add(ReadUnit(reader));
        return items;
    }

    public object Trace(string serialNumber)
    {
        var serial = Identifier(serialNumber, "serialNumber", 64);
        using var connection = Open();
        using var transaction = connection.BeginTransaction(deferred: true);
        var unit = GetUnit(connection, transaction, serial);
        var workOrder = GetWorkOrder(connection, transaction, unit.WorkOrderNo);
        var events = ReadEvents(connection, transaction, "WHERE serial_number=$serial ORDER BY event_id", ("$serial", serial));
        transaction.Commit();
        return new { unit, workOrder, route = Route, events };
    }

    public IReadOnlyList<EventView> Events(int limit, string? workOrderNo)
    {
        if (limit is < 1 or > 1000) throw Invalid("limit", "Use an event limit between 1 and 1000.");
        var number = OptionalWorkOrder(workOrderNo);
        using var connection = Open();
        if (number is not null) GetWorkOrder(connection, null, number);
        return ReadEvents(connection, null,
            (number is null ? "" : "WHERE work_order_no=$number ") + "ORDER BY event_id DESC LIMIT $limit",
            ("$number", number), ("$limit", limit));
    }

    public MutationResult CreateWorkOrder(WorkOrderInput input)
    {
        var number = Identifier(input.WorkOrderNo, "workOrderNo", 40);
        var product = Identifier(input.ProductCode, "productCode", 64);
        if (input.PlannedQuantity is < 1 or > 1000) throw Invalid("plannedQuantity", "Planned quantity must be between 1 and 1000.");
        var actor = Actor(input.Operator);
        var requestId = RequestId(input.RequestId);
        return Mutate(requestId, new { action = "createWorkOrder", number, product, input.PlannedQuantity, actor }, 201, (connection, transaction) =>
        {
            if (ScalarLong(connection, transaction, "SELECT COUNT(*) FROM work_orders WHERE work_order_no=$number", ("$number", number)) > 0)
                throw Conflict("work_order_exists", "This work order already exists.");
            Execute(connection, transaction, "INSERT INTO work_orders(work_order_no,product_code,planned_quantity,created_at_utc,created_by) VALUES($number,$product,$quantity,$time,$actor)",
                ("$number", number), ("$product", product), ("$quantity", input.PlannedQuantity), ("$time", Now()), ("$actor", actor));
            return GetWorkOrder(connection, transaction, number);
        });
    }

    public MutationResult Register(RegisterInput input)
    {
        var number = Identifier(input.WorkOrderNo, "workOrderNo", 40);
        var serial = Identifier(input.SerialNumber, "serialNumber", 64);
        var actor = Actor(input.Operator);
        var requestId = RequestId(input.RequestId);
        return Mutate(requestId, new { action = "register", number, serial, actor }, 201, (connection, transaction) =>
        {
            var order = GetWorkOrder(connection, transaction, number);
            if (ScalarLong(connection, transaction, "SELECT COUNT(*) FROM units WHERE serial_number=$serial", ("$serial", serial)) > 0)
                throw Conflict("serial_exists", "This serial number is already registered.");
            if (order.RegisteredQuantity >= order.PlannedQuantity)
                throw Conflict("planned_quantity_reached", "The work order has reached its planned quantity.");
            var time = Now();
            Execute(connection, transaction, "INSERT INTO units(serial_number,work_order_no,current_step,status,created_at_utc,updated_at_utc) VALUES($serial,$number,0,'Ready',$time,$time)",
                ("$serial", serial), ("$number", number), ("$time", time));
            AddEvent(connection, transaction, number, serial, "Registered", null, null, actor, null, time, requestId);
            return GetUnit(connection, transaction, serial);
        });
    }

    public MutationResult Report(string serialNumber, StationInput input)
    {
        var serial = Identifier(serialNumber, "serialNumber", 64);
        var station = (input.Station ?? "").Trim().ToUpperInvariant();
        if (!Route.Any(step => step.Code == station)) throw Invalid("station", "Choose ASSEMBLY, TEST, or PACK.");
        var result = (input.Result ?? "").Trim().ToUpperInvariant();
        if (result is not ("PASS" or "FAIL")) throw Invalid("result", "Result must be PASS or FAIL.");
        var actor = Actor(input.Operator);
        var reason = Reason(input.Reason, required: result == "FAIL");
        var requestId = RequestId(input.RequestId);
        return Mutate(requestId, new { action = "station", serial, station, result, actor, reason }, 200, (connection, transaction) =>
        {
            var unit = GetUnit(connection, transaction, serial);
            if (unit.Status == "Completed") throw Conflict("unit_completed", "This unit has completed its route.");
            if (unit.Status == "Held") throw Conflict("unit_held", "Release the hold with a reason before reporting this unit again.");
            if (unit.NextStation != station) throw Conflict("station_out_of_order", $"The next required station is {unit.NextStation}.");
            var passedSteps = unit.CompletedStations.Length + (result == "PASS" ? 1 : 0);
            var status = result == "FAIL" ? "Held" : passedSteps == Route.Length ? "Completed" : "Ready";
            var time = Now();
            Execute(connection, transaction, "UPDATE units SET current_step=$step,status=$status,updated_at_utc=$time WHERE serial_number=$serial",
                ("$step", passedSteps), ("$status", status), ("$time", time), ("$serial", serial));
            var entry = AddEvent(connection, transaction, unit.WorkOrderNo, serial, result == "PASS" ? "StationPassed" : "StationFailed", station, result, actor, reason, time, requestId);
            return new { unit = GetUnit(connection, transaction, serial), @event = entry };
        });
    }

    public MutationResult Release(string serialNumber, ReleaseInput input)
    {
        var serial = Identifier(serialNumber, "serialNumber", 64);
        var actor = Actor(input.Operator);
        var reason = Reason(input.Reason, required: true);
        var requestId = RequestId(input.RequestId);
        return Mutate(requestId, new { action = "release", serial, actor, reason }, 200, (connection, transaction) =>
        {
            var unit = GetUnit(connection, transaction, serial);
            if (unit.Status != "Held") throw Conflict("unit_not_held", "Only a held unit can be released.");
            var time = Now();
            Execute(connection, transaction, "UPDATE units SET status='Ready',updated_at_utc=$time WHERE serial_number=$serial", ("$time", time), ("$serial", serial));
            var entry = AddEvent(connection, transaction, unit.WorkOrderNo, serial, "HoldReleased", unit.NextStation, null, actor, reason, time, requestId);
            return new { unit = GetUnit(connection, transaction, serial), @event = entry };
        });
    }

    public string ExportCsv(string? workOrderNo)
    {
        var number = OptionalWorkOrder(workOrderNo);
        using var connection = Open();
        if (number is not null) GetWorkOrder(connection, null, number);
        var events = ReadEvents(connection, null, (number is null ? "" : "WHERE work_order_no=$number ") + "ORDER BY event_id", ("$number", number));
        var csv = new StringBuilder("event_id,work_order_no,serial_number,event_type,station,result,operator,reason,created_at_utc,request_id\r\n");
        foreach (var entry in events)
        {
            string?[] cells = [entry.EventId.ToString(CultureInfo.InvariantCulture), entry.WorkOrderNo, entry.SerialNumber, entry.EventType, entry.Station, entry.Result, entry.Operator, entry.Reason, entry.CreatedAtUtc, entry.RequestId];
            csv.AppendJoin(',', cells.Select(CsvCell)).Append("\r\n");
        }
        return csv.ToString();
    }

    private MutationResult Mutate(string requestId, object canonicalPayload, int statusCode, Func<SqliteConnection, SqliteTransaction, object> mutation)
    {
        var hash = Convert.ToHexString(SHA256.HashData(Encoding.UTF8.GetBytes(JsonSerializer.Serialize(canonicalPayload, JsonOptions))));
        lock (_writeLock)
        {
            using var connection = Open();
            // Immediate transaction obtains the write lock before reading quantity/state.
            using var transaction = connection.BeginTransaction(deferred: false);
            using (var existing = Command(connection, transaction, "SELECT payload_hash,status_code,response_json FROM idempotency WHERE request_id=$id", ("$id", requestId)))
            using (var reader = existing.ExecuteReader())
            {
                if (reader.Read())
                {
                    if (reader.GetString(0) != hash) throw Conflict("idempotency_conflict", "This request ID was already used with different content.");
                    var replay = new MutationResult(reader.GetInt32(1), reader.GetString(2), true);
                    reader.Close();
                    transaction.Commit();
                    return replay;
                }
            }
            var value = mutation(connection, transaction);
            var json = JsonSerializer.Serialize(value, JsonOptions);
            Execute(connection, transaction, "INSERT INTO idempotency(request_id,payload_hash,status_code,response_json) VALUES($id,$hash,$status,$json)",
                ("$id", requestId), ("$hash", hash), ("$status", statusCode), ("$json", json));
            transaction.Commit();
            return new MutationResult(statusCode, json);
        }
    }

    private SqliteConnection Open()
    {
        var connection = new SqliteConnection(_connectionString);
        connection.Open();
        return connection;
    }

    private const string WorkOrderSql = """
        SELECT w.work_order_no,w.product_code,w.planned_quantity,w.created_at_utc,
          (SELECT COUNT(*) FROM units u WHERE u.work_order_no=w.work_order_no),
          (SELECT COUNT(*) FROM units u WHERE u.work_order_no=w.work_order_no AND u.status='Completed'),
          (SELECT COUNT(*) FROM units u WHERE u.work_order_no=w.work_order_no AND u.status='Held')
        FROM work_orders w
        """;
    private const string UnitSql = """
        SELECT u.serial_number,u.work_order_no,w.product_code,u.status,u.current_step,u.created_at_utc
        FROM units u JOIN work_orders w ON w.work_order_no=u.work_order_no
        """;

    private static WorkOrderView GetWorkOrder(SqliteConnection connection, SqliteTransaction? transaction, string number)
    {
        using var command = Command(connection, transaction, WorkOrderSql + " WHERE w.work_order_no=$number", ("$number", number));
        using var reader = command.ExecuteReader();
        if (!reader.Read()) throw new MesException(404, "work_order_not_found", "Work order not found.");
        return ReadWorkOrder(reader);
    }

    private static WorkOrderView ReadWorkOrder(SqliteDataReader reader)
    {
        var planned = reader.GetInt32(2);
        var registered = reader.GetInt32(4);
        var completed = reader.GetInt32(5);
        return new(reader.GetString(0), reader.GetString(1), planned, registered, completed, reader.GetInt32(6),
            completed == planned ? "Completed" : registered > 0 ? "InProgress" : "Planned", reader.GetString(3));
    }

    private static UnitView GetUnit(SqliteConnection connection, SqliteTransaction? transaction, string serial)
    {
        using var command = Command(connection, transaction, UnitSql + " WHERE u.serial_number=$serial", ("$serial", serial));
        using var reader = command.ExecuteReader();
        if (!reader.Read()) throw new MesException(404, "unit_not_found", "Serial number not found.");
        return ReadUnit(reader);
    }

    private static UnitView ReadUnit(SqliteDataReader reader)
    {
        var step = reader.GetInt32(4);
        return new(reader.GetString(0), reader.GetString(1), reader.GetString(2), reader.GetString(3),
            step < Route.Length ? Route[step].Code : null, Route.Take(step).Select(item => item.Code).ToArray(), reader.GetString(5));
    }

    private static List<EventView> ReadEvents(SqliteConnection connection, SqliteTransaction? transaction, string suffix, params (string, object?)[] parameters)
    {
        using var command = Command(connection, transaction, "SELECT event_id,work_order_no,serial_number,event_type,station,result,operator,reason,created_at_utc,request_id FROM station_events " + suffix, parameters);
        using var reader = command.ExecuteReader();
        var items = new List<EventView>();
        while (reader.Read()) items.Add(new(reader.GetInt64(0), reader.GetString(1), reader.GetString(2), reader.GetString(3),
            NullableString(reader, 4), NullableString(reader, 5), reader.GetString(6), NullableString(reader, 7), reader.GetString(8), reader.GetString(9)));
        return items;
    }

    private static EventView AddEvent(SqliteConnection connection, SqliteTransaction transaction, string number, string serial, string type, string? station, string? result, string actor, string? reason, string time, string requestId)
    {
        Execute(connection, transaction, """
            INSERT INTO station_events(work_order_no,serial_number,event_type,station,result,operator,reason,created_at_utc,request_id)
            VALUES($number,$serial,$type,$station,$result,$actor,$reason,$time,$id)
            """, ("$number", number), ("$serial", serial), ("$type", type), ("$station", station), ("$result", result), ("$actor", actor), ("$reason", reason), ("$time", time), ("$id", requestId));
        var eventId = ScalarLong(connection, transaction, "SELECT last_insert_rowid()");
        return new(eventId, number, serial, type, station, result, actor, reason, time, requestId);
    }

    private static SqliteCommand Command(SqliteConnection connection, SqliteTransaction? transaction, string sql, params (string, object?)[] parameters)
    {
        var command = connection.CreateCommand();
        command.Transaction = transaction;
        command.CommandText = sql;
        foreach (var (name, value) in parameters) command.Parameters.AddWithValue(name, value ?? DBNull.Value);
        return command;
    }

    private static void Execute(SqliteConnection connection, SqliteTransaction? transaction, string sql, params (string, object?)[] parameters)
    {
        using var command = Command(connection, transaction, sql, parameters);
        command.ExecuteNonQuery();
    }

    private static long ScalarLong(SqliteConnection connection, SqliteTransaction? transaction, string sql, params (string, object?)[] parameters)
    {
        using var command = Command(connection, transaction, sql, parameters);
        return Convert.ToInt64(command.ExecuteScalar(), CultureInfo.InvariantCulture);
    }

    private static string? NullableString(SqliteDataReader reader, int ordinal) => reader.IsDBNull(ordinal) ? null : reader.GetString(ordinal);
    private static string Now() => DateTimeOffset.UtcNow.ToString("O", CultureInfo.InvariantCulture);
    private static MesException Invalid(string field, string message) => new(400, "invalid_" + field, message);
    private static MesException Conflict(string code, string message) => new(409, code, message);
    private static string Identifier(string? value, string field, int maxLength)
    {
        var normalized = (value ?? "").Trim().ToUpperInvariant();
        if (normalized.Length < 3 || normalized.Length > maxLength || !Regex.IsMatch(normalized, "^[A-Z0-9][A-Z0-9._-]*$", RegexOptions.CultureInvariant))
            throw Invalid(field, $"{field} must be 3–{maxLength} letters, digits, dots, underscores, or hyphens, starting with a letter or digit.");
        return normalized;
    }
    private static string Actor(string? value)
    {
        var actor = (value ?? "").Trim();
        if (actor.Length is < 1 or > 40 || actor.Any(char.IsControl)) throw Invalid("operator", "Operator is required and must be at most 40 characters without control characters.");
        return actor;
    }
    private static string? Reason(string? value, bool required)
    {
        var reason = (value ?? "").Trim();
        if (reason.Length > 200 || (required && reason.Length < 3) || reason.Any(char.IsControl))
            throw Invalid("reason", "A failure or hold release needs a reason of 3–200 characters without control characters.");
        return reason.Length == 0 ? null : reason;
    }
    private static string RequestId(string? value)
    {
        if (!Guid.TryParse(value, out var id) || id == Guid.Empty) throw Invalid("requestId", "Provide a non-empty UUID request ID.");
        return id.ToString("D");
    }
    private static string? OptionalWorkOrder(string? number) => string.IsNullOrWhiteSpace(number) ? null : Identifier(number, "workOrderNo", 40);
    private static string CsvCell(string? value)
    {
        var text = value ?? "";
        // Prevent spreadsheet formulas when user-entered actor/reason cells are opened in Excel.
        if (text.Length > 0 && "=+-@\t\r".Contains(text[0])) text = "'" + text;
        return "\"" + text.Replace("\"", "\"\"") + "\"";
    }

    private static void Seed(SqliteConnection connection)
    {
        using var transaction = connection.BeginTransaction(deferred: false);
        var baseTime = DateTimeOffset.UtcNow.AddMinutes(-30);
        var tick = 0;
        string Stamp() => baseTime.AddSeconds(tick++).ToString("O", CultureInfo.InvariantCulture);
        var created = Stamp();
        Execute(connection, transaction, "INSERT INTO work_orders VALUES('WO-DEMO-1001','DEMO-CONTROLLER',3,$time,'Demo operator')", ("$time", created));
        for (var index = 1; index <= 3; index++)
        {
            var serial = $"SN-DEMO-{index:0000}";
            var time = Stamp();
            var step = index == 3 ? 3 : 1;
            var status = index == 2 ? "Held" : index == 3 ? "Completed" : "Ready";
            Execute(connection, transaction, "INSERT INTO units VALUES($serial,'WO-DEMO-1001',$step,$status,$time,$time)", ("$serial", serial), ("$step", step), ("$status", status), ("$time", time));
            AddEvent(connection, transaction, "WO-DEMO-1001", serial, "Registered", null, null, "Demo operator", null, time, Guid.NewGuid().ToString("D"));
            AddEvent(connection, transaction, "WO-DEMO-1001", serial, "StationPassed", "ASSEMBLY", "PASS", "Demo operator", null, Stamp(), Guid.NewGuid().ToString("D"));
            if (index == 2) AddEvent(connection, transaction, "WO-DEMO-1001", serial, "StationFailed", "TEST", "FAIL", "Demo operator", "Synthetic test fixture mismatch", Stamp(), Guid.NewGuid().ToString("D"));
            if (index == 3)
            {
                AddEvent(connection, transaction, "WO-DEMO-1001", serial, "StationPassed", "TEST", "PASS", "Demo operator", null, Stamp(), Guid.NewGuid().ToString("D"));
                AddEvent(connection, transaction, "WO-DEMO-1001", serial, "StationPassed", "PACK", "PASS", "Demo operator", null, Stamp(), Guid.NewGuid().ToString("D"));
            }
        }
        transaction.Commit();
    }
}
