"""Exceptions raised by icdjev."""


class IcdJevError(Exception):
    """Base class for all icdjev errors."""


class UnsupportedFormatError(IcdJevError):
    """The file is not a WHO ICD-10 ClaML export this loader understands."""


class UnknownCodeError(IcdJevError, KeyError):
    """The code does not exist in the loaded edition."""
