"""
JSON-serializable dataclasses for the ClashOfCodeDescription service's getClashDescription
Codingame API method.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from ....common.dataclass_wizard_x import CatchAll, JSONWizardX
from .contribution import CgHtml


@dataclass
class CgClashDescription(JSONWizardX):
    """Localized help/explainer content for Clash of Code, as returned by getClashDescription."""

    description: dict[str, CgHtml]
    """HTML statement content, keyed by locale ID as a string (e.g. "1" observed containing
       French content, "2" observed containing English content--the exact ID-to-locale mapping
       is unconfirmed). Typed as a plain dict rather than the recursive JsonDict alias, since
       dataclass_wizard 1.0.0 corrupts nested dict values loaded through that self-referential
       Union."""

    extra_data: CatchAll = field(default_factory=dict)


__all__ = ["CgClashDescription", "CgHtml"]
