namespace TraceGate.MesLab;

public sealed record WorkOrderInput(string? WorkOrderNo, string? ProductCode, int PlannedQuantity, string? Operator, string? RequestId);
public sealed record RegisterInput(string? WorkOrderNo, string? SerialNumber, string? Operator, string? RequestId);
public sealed record StationInput(string? Station, string? Result, string? Operator, string? Reason, string? RequestId);
public sealed record ReleaseInput(string? Operator, string? Reason, string? RequestId);

public sealed record RouteStep(string Code, string Name);
public sealed record WorkOrderView(string WorkOrderNo, string ProductCode, int PlannedQuantity, int RegisteredQuantity, int CompletedQuantity, int HeldQuantity, string Status, string CreatedAtUtc);
public sealed record UnitView(string SerialNumber, string WorkOrderNo, string ProductCode, string Status, string? NextStation, string[] CompletedStations, string CreatedAtUtc);
public sealed record EventView(long EventId, string WorkOrderNo, string SerialNumber, string EventType, string? Station, string? Result, string Operator, string? Reason, string CreatedAtUtc, string RequestId);
public sealed record MutationResult(int StatusCode, string Json, bool Replayed = false);

public sealed class MesException(int statusCode, string code, string message) : Exception(message)
{
    public int StatusCode { get; } = statusCode;
    public string Code { get; } = code;
}
