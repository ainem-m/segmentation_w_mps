namespace TotalSegmentatorWrapper.Windows.CoordinatorShell;

internal enum SegmentationProfile
{
    TotalSegmentator,
    DentalSegmentator,
    IndividualTeeth,
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
            SegmentationProfile.IndividualTeeth =>
                "run_nifti_individual_teeth",
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
            SegmentationProfile.IndividualTeeth =>
                "個別歯ベータ",
            _ => throw new ArgumentOutOfRangeException(nameof(profile)),
        };
    }
}
