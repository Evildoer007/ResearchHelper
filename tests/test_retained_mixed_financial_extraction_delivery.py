"""Keep the explicit delivered replay name for existing acceptance runners."""
if __package__:
    from .test_retained_mixed_financial_extraction import (
        RetainedMixedExtractionTests as DeliveredRetainedMixedExtractionTests,
    )
else:
    from test_retained_mixed_financial_extraction import (
        RetainedMixedExtractionTests as DeliveredRetainedMixedExtractionTests,
    )
