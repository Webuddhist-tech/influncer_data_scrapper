"""Social Media Ranking — weekly creator scoring pipeline.

Stages: extract-links -> validate-links -> extract-social -> score -> publish.
A separate data repo stores snapshots; a Pages dashboard reads the published JSON.
"""
from .config import PLATFORMS, PLATFORM_WEIGHTS, Config
from .models import CreatorProfile, CreatorScore, PlatformScore, RawMetrics
from .scoring import combine_scores, score_all, score_platform
from .storage import DataRepo

__version__ = "0.1.0"

__all__ = [
    "Config",
    "CreatorProfile",
    "CreatorScore",
    "DataRepo",
    "PLATFORMS",
    "PLATFORM_WEIGHTS",
    "PlatformScore",
    "RawMetrics",
    "__version__",
    "combine_scores",
    "score_all",
    "score_platform",
]
