using System.IO;
using System.Text;
using System.Text.Json;
using System.Text.Json.Serialization;

namespace TotalSegmentatorWrapper.Windows.CoordinatorShell;

internal sealed class ShellPreferences
{
    private const string SettingsSchema =
        "totalsegmentator_wrapper.windows_shell_settings.v1";
    private const string SettingsDirectoryName =
        "TotalSegmentatorWrapperWindows";
    private const string SettingsFileName = "shell-settings.json";

    private readonly string? _settingsDirectory = ResolveSettingsDirectory();

    internal string? LoadOutputRoot()
    {
        if (_settingsDirectory is null)
        {
            return null;
        }

        try
        {
            var settingsPath = Path.Combine(
                _settingsDirectory,
                SettingsFileName);
            if (!File.Exists(settingsPath))
            {
                return null;
            }

            var payload = JsonSerializer.Deserialize<SettingsPayload>(
                File.ReadAllText(settingsPath, Encoding.UTF8));
            if (payload?.Schema != SettingsSchema)
            {
                return null;
            }

            return NormalizeExistingDirectory(payload.OutputRoot);
        }
        catch (Exception)
        {
            return null;
        }
    }

    internal bool SaveOutputRoot(string outputRoot)
    {
        var normalizedOutputRoot = NormalizeExistingDirectory(outputRoot);
        if (_settingsDirectory is null || normalizedOutputRoot is null)
        {
            return false;
        }

        string? temporaryPath = null;
        try
        {
            Directory.CreateDirectory(_settingsDirectory);
            var settingsPath = Path.Combine(
                _settingsDirectory,
                SettingsFileName);
            temporaryPath = Path.Combine(
                _settingsDirectory,
                $".shell-settings-{Guid.NewGuid():N}.tmp");
            var json = JsonSerializer.Serialize(
                new SettingsPayload
                {
                    Schema = SettingsSchema,
                    OutputRoot = normalizedOutputRoot,
                },
                new JsonSerializerOptions { WriteIndented = true });
            File.WriteAllText(
                temporaryPath,
                json,
                new UTF8Encoding(encoderShouldEmitUTF8Identifier: false));
            File.Move(temporaryPath, settingsPath, overwrite: true);
            temporaryPath = null;
            return true;
        }
        catch (Exception)
        {
            return false;
        }
        finally
        {
            if (temporaryPath is not null)
            {
                try
                {
                    File.Delete(temporaryPath);
                }
                catch (Exception)
                {
                    // The next save uses a unique temporary name.
                }
            }
        }
    }

    private static string? ResolveSettingsDirectory()
    {
        try
        {
            var localApplicationData = Environment.GetFolderPath(
                Environment.SpecialFolder.LocalApplicationData);
            if (!Path.IsPathFullyQualified(localApplicationData))
            {
                return null;
            }

            return Path.GetFullPath(
                Path.Combine(
                    localApplicationData,
                    SettingsDirectoryName));
        }
        catch (Exception)
        {
            return null;
        }
    }

    private static string? NormalizeExistingDirectory(string? directory)
    {
        if (string.IsNullOrWhiteSpace(directory)
            || !Path.IsPathFullyQualified(directory))
        {
            return null;
        }

        try
        {
            var normalized = Path.GetFullPath(directory);
            return Directory.Exists(normalized) ? normalized : null;
        }
        catch (Exception)
        {
            return null;
        }
    }

    private sealed class SettingsPayload
    {
        [JsonPropertyName("schema")]
        public string? Schema { get; init; }

        [JsonPropertyName("output_root")]
        public string? OutputRoot { get; init; }
    }
}
