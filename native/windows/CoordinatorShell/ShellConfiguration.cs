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
    string TotalSegmentatorHome,
    string DicomNormalizerPath,
    string Dcm2niixPath,
    string DentalSegmentatorModelRoot,
    string ToothSegModelRoot)
{
    internal static ShellConfiguration Load(string? engineeringConfigPath)
    {
        var baseDirectory = AppContext.BaseDirectory;
        var nativeRuntime = Path.Combine(baseDirectory, "runtime", "native");
        if (engineeringConfigPath is null)
        {
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
                Path.Combine(baseDirectory, "models", "totalseg-home"),
                Path.Combine(
                    nativeRuntime,
                    "totalsegmentator-wrapper-dicom-normalizer.exe"),
                Path.Combine(nativeRuntime, "dcm2niix.exe"),
                Path.Combine(baseDirectory, "models", "dentalseg"),
                Path.Combine(baseDirectory, "models", "toothseg"));
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
            RequireAbsolute(payload.TotalSegmentatorHome, "totalseg_home"),
            OptionalAbsolute(
                payload.DicomNormalizerPath,
                Path.Combine(
                    nativeRuntime,
                    "totalsegmentator-wrapper-dicom-normalizer.exe"),
                "dicom_normalizer_path"),
            OptionalAbsolute(
                payload.Dcm2niixPath,
                Path.Combine(nativeRuntime, "dcm2niix.exe"),
                "dcm2niix_path"),
            OptionalAbsolute(
                payload.DentalSegmentatorModelRoot,
                Path.Combine(baseDirectory, "models", "dentalseg"),
                "dentalseg_model_root"),
            OptionalAbsolute(
                payload.ToothSegModelRoot,
                Path.Combine(baseDirectory, "models", "toothseg"),
                "toothseg_model_root"));
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

    internal RuntimeCheckResult CheckDicomRuntime()
    {
        var failures = new List<string>();
        CheckFile(
            DicomNormalizerPath,
            "DICOM読み込み機能",
            failures);
        CheckFile(Dcm2niixPath, "DICOM変換機能", failures);
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
                "DICOM読み込み環境を確認しました。",
                null,
                null)
            : new RuntimeCheckResult(
                false,
                string.Join(
                    " ",
                    failures.Distinct(StringComparer.Ordinal)),
                "dicom_runtime_unavailable",
                "アプリに同梱されたDICOM読み込み機能を確認してください。");
    }

    internal RuntimeCheckResult CheckDentalSegmentatorRuntime()
    {
        const string datasetName =
            "Dataset112_DentalSegmentator_v100";
        var failures = new List<string>();
        CheckDirectory(
            DentalSegmentatorModelRoot,
            "DentalSegmentator の追加モデル",
            failures);
        try
        {
            var datasetRoot = Path.Combine(
                DentalSegmentatorModelRoot,
                "nnUNet_results",
                datasetName);
            using var marker = JsonDocument.Parse(
                File.ReadAllText(
                    Path.Combine(
                        datasetRoot,
                        ".dentalsegmentator_model_ready.json")));
            var root = marker.RootElement;
            if (!root.TryGetProperty("schema", out var schema)
                || schema.GetString()
                    != "totalsegmentator_wrapper_mac.dentalsegmentator_model_status.v1"
                || !root.TryGetProperty("model_state", out var state)
                || state.GetString() != "ready"
                || !root.TryGetProperty("expected_md5", out var md5)
                || md5.GetString()
                    != "b71cd5230168d28a4f71b078265b76be"
                || !root.TryGetProperty("dataset_id", out var datasetId)
                || datasetId.GetString() != "112"
                || !root.TryGetProperty("dataset_name", out var name)
                || name.GetString() != datasetName
                || !Directory.EnumerateFiles(
                        datasetRoot,
                        "dataset.json",
                        SearchOption.AllDirectories)
                    .Any()
                || !Directory.EnumerateFiles(
                        datasetRoot,
                        "checkpoint_final.pth",
                        SearchOption.AllDirectories)
                    .Any(path => new FileInfo(path).Length > 0))
            {
                failures.Add(
                    "DentalSegmentator の追加モデルを確認できません。");
            }
        }
        catch (Exception exception) when (
            exception is IOException
                or UnauthorizedAccessException
                or JsonException)
        {
            failures.Add(
                "DentalSegmentator の追加モデルを確認できません。");
        }
        return failures.Count == 0
            ? new RuntimeCheckResult(
                true,
                "DentalSegmentator の追加モデルを確認しました。",
                null,
                null)
            : new RuntimeCheckResult(
                false,
                string.Join(
                    " ",
                    failures.Distinct(StringComparer.Ordinal)),
                "dentalseg_prepare_required",
                "検証済みのapp-private DentalSegmentatorモデルを準備してから、もう一度選んでください。");
    }

    internal RuntimeCheckResult CheckIndividualTeethRuntime()
    {
        var datasetRoot = Path.Combine(
            TotalSegmentatorHome,
            "nnunet",
            "results",
            "Dataset113_ToothFairy3");
        var failures = new List<string>();
        try
        {
            if (!Directory.Exists(datasetRoot)
                || !Directory.EnumerateFiles(
                        datasetRoot,
                        "dataset.json",
                        SearchOption.AllDirectories)
                    .Any()
                || !Directory.EnumerateFiles(
                        datasetRoot,
                        "checkpoint_final.pth",
                        SearchOption.AllDirectories)
                    .Any(path => new FileInfo(path).Length > 0))
            {
                failures.Add(
                    "個別歯ベータの追加モデルを確認できません。");
            }
        }
        catch (Exception exception) when (
            exception is IOException
                or UnauthorizedAccessException)
        {
            failures.Add(
                "個別歯ベータの追加モデルを確認できません。");
        }
        return failures.Count == 0
            ? new RuntimeCheckResult(
                true,
                "個別歯ベータの追加モデルを確認しました。",
                null,
                null)
            : new RuntimeCheckResult(
                false,
                string.Join(
                    " ",
                    failures.Distinct(StringComparer.Ordinal)),
                "individual_teeth_prepare_required",
                "検証済みのapp-private Dataset113モデルを準備してから、もう一度選んでください。");
    }

    internal RuntimeCheckResult CheckToothSegRuntime()
    {
        var results = Path.Combine(ToothSegModelRoot, "nnUNet_results");
        var failures = new List<string>();
        try
        {
            using var marker = JsonDocument.Parse(
                File.ReadAllText(
                    Path.Combine(
                        results,
                        ".toothseg_model_ready.json")));
            var root = marker.RootElement;
            var datasets = new[]
            {
                "Dataset121_ToothFairy2_Teeth",
                "Dataset123_ToothFairy2fixed_teeth_spacing02_brd3px",
            };
            if (!root.TryGetProperty("schema", out var schema)
                || schema.GetString()
                    != "totalsegmentator_wrapper_mac.toothseg_model_status.v1"
                || !root.TryGetProperty("model_state", out var state)
                || state.GetString() != "ready"
                || !root.TryGetProperty("expected_md5", out var md5)
                || md5.GetString()
                    != "5d8dd061cce9529943567aeba3271143"
                || !root.TryGetProperty(
                    "pair_distributions_sha256",
                    out var pairHash)
                || pairHash.GetString()
                    != "82ab04892277d36013be5ba9763ac334ea073fca7ebe8679086f1e33ed64ff29"
                || !File.Exists(
                    Path.Combine(
                        ToothSegModelRoot,
                        "fdi_pair_distrs.json"))
                || datasets.Any(
                    dataset =>
                    {
                        var datasetRoot = Path.Combine(results, dataset);
                        return !Directory.Exists(datasetRoot)
                            || !Directory.EnumerateFiles(
                                    datasetRoot,
                                    "dataset.json",
                                    SearchOption.AllDirectories)
                                .Any()
                            || !Directory.EnumerateFiles(
                                    datasetRoot,
                                    "checkpoint_final.pth",
                                    SearchOption.AllDirectories)
                                .Any(path => new FileInfo(path).Length > 0);
                    }))
            {
                failures.Add("ToothSeg の追加モデルを確認できません。");
            }
        }
        catch (Exception exception) when (
            exception is IOException
                or UnauthorizedAccessException
                or JsonException)
        {
            failures.Add("ToothSeg の追加モデルを確認できません。");
        }
        return failures.Count == 0
            ? new RuntimeCheckResult(
                true,
                "ToothSeg の追加モデルを確認しました。",
                null,
                null)
            : new RuntimeCheckResult(
                false,
                string.Join(
                    " ",
                    failures.Distinct(StringComparer.Ordinal)),
                "toothseg_prepare_required",
                "検証済みのapp-private ToothSegモデルを準備してから、もう一度選んでください。");
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

    private static string OptionalAbsolute(
        string? value,
        string defaultValue,
        string name)
    {
        return string.IsNullOrWhiteSpace(value)
            ? Path.GetFullPath(defaultValue)
            : RequireAbsolute(value, name);
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

        [JsonPropertyName("dicom_normalizer_path")]
        public string? DicomNormalizerPath { get; init; }

        [JsonPropertyName("dcm2niix_path")]
        public string? Dcm2niixPath { get; init; }

        [JsonPropertyName("dentalseg_model_root")]
        public string? DentalSegmentatorModelRoot { get; init; }

        [JsonPropertyName("toothseg_model_root")]
        public string? ToothSegModelRoot { get; init; }
    }
}

internal sealed record RuntimeCheckResult(
    bool Passed,
    string Message,
    string? ErrorCode,
    string? RecoveryMessage);
