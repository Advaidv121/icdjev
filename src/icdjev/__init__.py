"""icdjev - a modifier-aware WHO ICD-10 catalogue you load from your own WHO download.

>>> from icdjev import Catalogue
>>> cat = Catalogue.load("icd102016en.xml")          # file from icdcdn.who.int/icd10
>>> [n.code for n in cat.children("E11")][:3]        # modifier-derived children
['E11.0', 'E11.1', 'E11.2']
"""

from .catalogue import Catalogue
from .errors import IcdJevError, UnknownCodeError, UnsupportedFormatError
from .lookup import ICDCandidate, ICDCode, ICDLookup, ICDReviewResult
from .models import CodeRef, Edition, Node, Rubric

__version__ = "0.1.0"
__all__ = [
    "Catalogue",
    "CodeRef",
    "Edition",
    "ICDCandidate",
    "ICDCode",
    "ICDLookup",
    "ICDReviewResult",
    "IcdJevError",
    "Node",
    "Rubric",
    "UnknownCodeError",
    "UnsupportedFormatError",
    "__version__",
]
