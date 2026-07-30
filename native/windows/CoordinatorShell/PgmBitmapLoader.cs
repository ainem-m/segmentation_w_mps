using System.IO;
using System.Text;
using System.Windows.Media;
using System.Windows.Media.Imaging;

namespace TotalSegmentatorWrapper.Windows.CoordinatorShell;

internal static class PgmBitmapLoader
{
    private const int MaximumBytes = 64 * 1024 * 1024;

    internal static BitmapSource Load(string path)
    {
        var info = new FileInfo(path);
        if (!info.Exists
            || info.Length <= 0
            || info.Length > MaximumBytes)
        {
            throw new InvalidDataException(
                "The PGM preview size is invalid.");
        }
        var data = File.ReadAllBytes(path);
        var offset = 0;
        if (ReadToken(data, ref offset) != "P5")
        {
            throw new InvalidDataException(
                "Only binary PGM previews are supported.");
        }
        if (!int.TryParse(
                ReadToken(data, ref offset),
                System.Globalization.NumberStyles.None,
                System.Globalization.CultureInfo.InvariantCulture,
                out var width)
            || !int.TryParse(
                ReadToken(data, ref offset),
                System.Globalization.NumberStyles.None,
                System.Globalization.CultureInfo.InvariantCulture,
                out var height)
            || !int.TryParse(
                ReadToken(data, ref offset),
                System.Globalization.NumberStyles.None,
                System.Globalization.CultureInfo.InvariantCulture,
                out var maximum)
            || width <= 0
            || height <= 0
            || maximum != 255)
        {
            throw new InvalidDataException(
                "The PGM preview header is invalid.");
        }
        if (offset >= data.Length || !IsWhitespace(data[offset]))
        {
            throw new InvalidDataException(
                "The PGM preview header is incomplete.");
        }
        if (data[offset++] == '\r'
            && offset < data.Length
            && data[offset] == '\n')
        {
            offset++;
        }
        var pixelCount = checked(width * height);
        if (data.Length - offset != pixelCount)
        {
            throw new InvalidDataException(
                "The PGM preview payload is invalid.");
        }
        var pixels = new byte[pixelCount];
        Buffer.BlockCopy(data, offset, pixels, 0, pixelCount);
        var bitmap = BitmapSource.Create(
            width,
            height,
            96,
            96,
            PixelFormats.Gray8,
            null,
            pixels,
            width);
        bitmap.Freeze();
        return bitmap;
    }

    private static string ReadToken(
        byte[] data,
        ref int offset)
    {
        while (offset < data.Length)
        {
            if (data[offset] == '#')
            {
                while (offset < data.Length
                       && data[offset] != (byte)'\r'
                       && data[offset] != (byte)'\n')
                {
                    offset++;
                }
                continue;
            }
            if (!IsWhitespace(data[offset]))
            {
                break;
            }
            offset++;
        }
        var start = offset;
        while (offset < data.Length
               && !IsWhitespace(data[offset])
               && data[offset] != '#')
        {
            offset++;
        }
        if (start == offset)
        {
            throw new InvalidDataException(
                "The PGM preview header is incomplete.");
        }
        return Encoding.ASCII.GetString(
            data,
            start,
            offset - start);
    }

    private static bool IsWhitespace(byte value)
    {
        return value is (byte)' '
            or (byte)'\t'
            or (byte)'\r'
            or (byte)'\n';
    }
}
