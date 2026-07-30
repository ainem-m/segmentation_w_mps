using System.IO;
using System.Text.Json;
using System.Text.Json.Serialization;

namespace TotalSegmentatorWrapper.Windows.CoordinatorShell;

internal sealed record ShellConfiguration(
    string SupervisorPath,
    string CoordinatorPath,
    string CoordinatorWorkingDirectory,
    string BundledSamplePath,
    string OutputRoot,
    string TotalSegmentatorHome)
{
    internal static ShellConfiguration Load(string? engineeringConfigPath)
    {
        if (engineeringConfigPath is null)
        {
            var baseDirectory = AppContext.BaseDirectory;
            var runtime = Path.Combine(baseDirectory, "runtime", "python");
            return new ShellConfiguration(
                Path.Combine(baseDirectory, "tswm-process-supervisor.exe"),
                Path.Combine(
                    runtime,
                    "Scripts",
                    "totalsegmentator-wrapper-coordinator.exe"),
                runtime,
                Path.Combine(
                    baseDirectory,
                    "sample1",
                    "input",
                    "owner_cbct_jawcrop_0p5mm.nii.gz"),
                Path.Combine(
                    Environment.GetFolderPath(
                        Environment.SpecialFolder.LocalApplicationData),
                    "TotalSegmentatorWrapperWindows",
                    "runs"),
                Path.Combine(baseDirectory, "models", "totalseg-home"));
        }

        var absoluteConfigPath = Path.GetFullPath(engineeringConfigPath);
        if (!Path.IsPathFullyQualified(engineeringConfigPath)
            || !File.Exists(absoluteConfigPath))
        {
            throw new InvalidDataException(
                "The engineering configuration path is unavailable.");
        }
        var payload = JsonSerializer.Deserialize<ConfigurationPayload>(
            File.ReadAllText(absoluteConfigPath),
            new JsonSerializerOptions { PropertyNameCaseInsensitive = true })
            ?? throw new InvalidDataException(
                "The engineering configuration is invalid.");
        return new ShellConfiguration(
            RequireAbsolute(payload.SupervisorPath, "supervisor_path"),
            RequireAbsolute(payload.CoordinatorPath, "coordinator_path"),
            RequireAbsolute(
                payload.CoordinatorWorkingDirectory,
                "coordinator_working_directory"),
            RequireAbsolute(payload.BundledSamplePath, "bundled_sample_path"),
            RequireAbsolute(payload.OutputRoot, "output_root"),
            RequireAbsolute(payload.TotalSegmentatorHome, "totalseg_home"));
    }

    internal RuntimeCheckResult CheckRuntime()
    {
        var failures = new List<string>();
        CheckFile(SupervisorPath, "Windowsの処理管理機能", failures);
        CheckFile(CoordinatorPath, "3Dプレビュー作成機能", failures);
        CheckDirectory(
            CoordinatorWorkingDirectory,
            "同梱済みの実行環境",
            failures);
        CheckFile(BundledSamplePath, "同梱Sample 1", failures);
        CheckDirectory(
            TotalSegmentatorHome,
            "同梱済みのモデル",
            failures);
        CheckTotalSegmentatorCache(failures);
        try
        {
            Directory.CreateDirectory(OutputRoot);
        }
        catch (Exception exception) when (
            exception is IOException
                or UnauthorizedAccessException
                or ArgumentException)
        {
            failures.Add("保存先を準備できません。");
        }
        return failures.Count == 0
            ? new RuntimeCheckResult(
                true,
                "実行環境を確認しました。通信や依存関係の再解決は行っていません。",
                null,
                null)
            : new RuntimeCheckResult(
                false,
                string.Join(
                    " ",
                    failures.Distinct(StringComparer.Ordinal)),
                "runtime_unavailable",
                "アプリの配置と保存先を確認してから、「準備を始める」をもう一度押してください。");
    }

    private static string RequireAbsolute(string? value, string name)
    {
        if (string.IsNullOrWhiteSpace(value)
            || !Path.IsPathFullyQualified(value))
        {
            throw new InvalidDataException(
                $"The {name} value must be an absolute path.");
        }
        return Path.GetFullPath(value);
    }

    private static void CheckFile(
        string path,
        string label,
        ICollection<string> failures)
    {
        if (!File.Exists(path))
        {
            failures.Add($"{label} が見つかりません。");
        }
    }

    private static void CheckDirectory(
        string path,
        string label,
        ICollection<string> failures)
    {
        if (!Directory.Exists(path))
        {
            failures.Add($"{label} が見つかりません。");
        }
    }

    private void CheckTotalSegmentatorCache(ICollection<string> failures)
    {
        try
        {
            using var config = JsonDocument.Parse(
                File.ReadAllText(
                    Path.Combine(TotalSegmentatorHome, "config.json")));
            if (!config.RootElement.TryGetProperty(
                    "send_usage_stats",
                    out var usage)
                || usage.ValueKind != JsonValueKind.False)
            {
                failures.Add(
                    "TotalSegmentator の外部送信無効設定を確認できません。");
            }
            foreach (var dataset in new[]
            {
                "Dataset115_mandible",
                "Dataset297_TotalSegmentator_total_3mm_1559subj",
            })
            {
                var datasetPath = Path.Combine(
                    TotalSegmentatorHome,
                    "nnunet",
                    "results",
                    dataset);
                if (!Directory.Exists(datasetPath)
                    || !Directory.EnumerateFiles(
                            datasetPath,
                            "checkpoint_final.pth",
                            SearchOption.AllDirectories)
                        .Any(path => new FileInfo(path).Length > 0))
                {
                    failures.Add(
                        "TotalSegmentator の同梱済みモデルを確認できません。");
                }
            }
        }
        catch (Exception exception) when (
            exception is IOException
                or UnauthorizedAccessException
                or JsonException)
        {
            failures.Add(
                "TotalSegmentator の同梱済み設定を確認できません。");
        }
    }

    private sealed class ConfigurationPayload
    {
        [JsonPropertyName("supervisor_path")]
        public string? SupervisorPath { get; init; }

        [JsonPropertyName("coordinator_path")]
        public string? CoordinatorPath { get; init; }

        [JsonPropertyName("coordinator_working_directory")]
        public string? CoordinatorWorkingDirectory { get; init; }

        [JsonPropertyName("bundled_sample_path")]
        public string? BundledSamplePath { get; init; }

        [JsonPropertyName("output_root")]
        public string? OutputRoot { get; init; }

        [JsonPropertyName("totalseg_home")]
        public string? TotalSegmentatorHome { get; init; }
    }
}

internal sealed record RuntimeCheckResult(
    bool Passed,
    string Message,
    string? ErrorCode,
    string? RecoveryMessage);
