from .fallback_parser import OfficeFallbackParser
from .native_parser import OfficeNativeParser
from .parser import OfficeParser
from .primary_parser import OfficePrimaryParser

__all__ = [
    "OfficeParser",
    "OfficePrimaryParser",
    "OfficeFallbackParser",
    "OfficeNativeParser",
]
