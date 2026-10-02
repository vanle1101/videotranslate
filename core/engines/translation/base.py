from abc import abstractmethod
from typing import List, Dict, Any, Optional
from core.engines.base import BaseEngine

class TranslationEngine(BaseEngine):
    """Base class for Semantic & Context-aware Video Translation."""

    @abstractmethod
    def translate(
        self,
        segments: List[Dict[str, Any]],
        target_lang: str = "vi",
        progress_callback: Optional[callable] = None
    ) -> List[Dict[str, Any]]:
        """
        Translates Chinese transcript segments into Vietnamese using multi-stage reflection.
        Each segment in output must contain:
        - text_zh: original Chinese
        - literal_vi: first direct translation
        - natural_vi: adapted natural Vietnamese
        - final_vi: duration-constrained final translation
        """
        pass
