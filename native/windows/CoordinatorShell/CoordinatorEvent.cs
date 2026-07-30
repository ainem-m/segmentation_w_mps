using System.IO;
using System.Text.Json;

namespace TotalSegmentatorWrapper.Windows.CoordinatorShell;

internal sealed record CoordinatorEvent(
    string EventName,
    int ProtocolVersion,
    string OperationId,
    int Sequence,
    string? StageId,
    string? Label,
    int? StageIndex,
    int? StageTotal,
    double? Percent,
    string? Stage,
    string? RequestedPolicy,
    int? RequestedDeviceIndex,
    string? ResolvedDevice,
    bool? FallbackAllowed,
    bool? FallbackOccurred,
    string? ErrorCode,
    string? SafeReason,
    string? ReasonCode)
{
    internal bool IsTerminal =>
        EventName is "operation_completed"
            or "operation_failed"
            or "operation_cancelled";

    internal static CoordinatorEvent Parse(string line)
    {
        using var document = JsonDocument.Parse(line);
        var root = document.RootElement;
        if (root.ValueKind != JsonValueKind.Object)
        {
            throw new InvalidDataException(
                "Coordinator stdout must contain JSON objects only.");
        }
        return new CoordinatorEvent(
            RequiredString(root, "event"),
            RequiredInt(root, "protocol_version"),
            RequiredString(root, "operation_id"),
            RequiredInt(root, "sequence"),
            OptionalString(root, "stage_id"),
            OptionalString(root, "label"),
            OptionalInt(root, "index"),
            OptionalInt(root, "total"),
            OptionalDouble(root, "percent"),
            OptionalString(root, "stage"),
            OptionalString(root, "requested_policy"),
            OptionalInt(root, "requested_device_index"),
            OptionalString(root, "resolved_device"),
            OptionalBool(root, "fallback_allowed"),
            OptionalBool(root, "fallback_occurred"),
            OptionalString(root, "error_code"),
            OptionalString(root, "safe_reason"),
            OptionalString(root, "reason_code"));
    }

    internal static bool ContractSelfTest()
    {
        var started = Parse(
            """
            {"event":"operation_started","operation_id":"00000000-0000-0000-0000-000000000001","protocol_version":1,"sequence":1}
            """);
        var device = Parse(
            """
            {"event":"device_resolved","fallback_allowed":false,"fallback_occurred":false,"operation_id":"00000000-0000-0000-0000-000000000001","protocol_version":1,"requested_device_index":0,"requested_policy":"cuda_required","resolved_device":"cuda:0","sequence":2}
            """);
        var progress = Parse(
            """
            {"event":"progress","operation_id":"00000000-0000-0000-0000-000000000001","percent":8,"protocol_version":1,"sequence":3,"stage_id":"segment"}
            """);
        var phaseOnlyProgress = Parse(
            """
            {"event":"progress","operation_id":"00000000-0000-0000-0000-000000000001","percent":null,"protocol_version":1,"sequence":4,"stage":"Predicting","stage_id":"segment","step":null,"total":null}
            """);
        var failure = Parse(
            """
            {"event":"operation_failed","error_code":"cuda_unavailable","operation_id":"00000000-0000-0000-0000-000000000001","protocol_version":1,"safe_reason":"The required CUDA device did not pass strict validation.","sequence":5}
            """);
        return started.EventName == "operation_started"
            && device.RequestedPolicy == "cuda_required"
            && device.RequestedDeviceIndex == 0
            && device.ResolvedDevice == "cuda:0"
            && device.FallbackAllowed == false
            && device.FallbackOccurred == false
            && progress.Percent == 8
            && phaseOnlyProgress.Percent is null
            && phaseOnlyProgress.Stage == "Predicting"
            && failure.IsTerminal
            && failure.ErrorCode == "cuda_unavailable";
    }

    private static string RequiredString(JsonElement root, string name)
    {
        return root.TryGetProperty(name, out var value)
                && value.ValueKind == JsonValueKind.String
            ? value.GetString()
                ?? throw new InvalidDataException(
                    $"Coordinator event field {name} is invalid.")
            : throw new InvalidDataException(
                $"Coordinator event field {name} is missing.");
    }

    private static int RequiredInt(JsonElement root, string name)
    {
        return root.TryGetProperty(name, out var value)
                && value.ValueKind == JsonValueKind.Number
                && value.TryGetInt32(out var result)
            ? result
            : throw new InvalidDataException(
                $"Coordinator event field {name} is missing.");
    }

    private static string? OptionalString(JsonElement root, string name)
    {
        return root.TryGetProperty(name, out var value)
                && value.ValueKind == JsonValueKind.String
            ? value.GetString()
            : null;
    }

    private static int? OptionalInt(JsonElement root, string name)
    {
        return root.TryGetProperty(name, out var value)
                && value.ValueKind == JsonValueKind.Number
                && value.TryGetInt32(out var result)
            ? result
            : null;
    }

    private static double? OptionalDouble(JsonElement root, string name)
    {
        return root.TryGetProperty(name, out var value)
                && value.ValueKind == JsonValueKind.Number
                && value.TryGetDouble(out var result)
            ? result
            : null;
    }

    private static bool? OptionalBool(JsonElement root, string name)
    {
        return root.TryGetProperty(name, out var value)
                && value.ValueKind is JsonValueKind.True or JsonValueKind.False
            ? value.GetBoolean()
            : null;
    }
}
