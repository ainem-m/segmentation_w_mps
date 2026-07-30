namespace TotalSegmentatorWrapper.Windows.CoordinatorShell;

internal enum SegmentationProfile
{
    TotalSegmentator,
    DentalSegmentator,
}

internal static class SegmentationProfileExtensions
{
    internal static string OperationName(this SegmentationProfile profile)
    {
        return profile switch
        {
            SegmentationProfile.TotalSegmentator =>
                "run_nifti_totalsegmentator",
            SegmentationProfile.DentalSegmentator =>
                "run_nifti_dentalsegmentator",
            _ => throw new ArgumentOutOfRangeException(nameof(profile)),
        };
    }

    internal static string DisplayName(this SegmentationProfile profile)
    {
        return profile switch
        {
            SegmentationProfile.TotalSegmentator => "TotalSegmentator",
            SegmentationProfile.DentalSegmentator =>
                "DentalSegmentator（実験的）",
            _ => throw new ArgumentOutOfRangeException(nameof(profile)),
        };
    }
}
