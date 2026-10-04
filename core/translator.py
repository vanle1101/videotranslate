"""Compatibility facade for the shared, provider-aware translation engine."""

from typing import Any, Dict, List, Optional

from config import settings
from core.engines.translation.semantic_translator import SemanticTranslator


class VideoTranslator:
    def __init__(self, provider: Optional[str] = None):
        self.provider = provider or settings.LLM_PROVIDER

    def translate_segments(self, segments: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """Translate with the selected provider and preserve segment metadata."""
        return SemanticTranslator(provider=self.provider).translate(segments)
