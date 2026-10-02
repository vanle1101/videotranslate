from abc import ABC, abstractmethod
from typing import Dict, Any, Optional
from pathlib import Path

class BaseEngine(ABC):
    """Base class for all premium engines in the system."""
    
    @property
    @abstractmethod
    def name(self) -> str:
        """Name of the engine."""
        pass

    @property
    @abstractmethod
    def is_available(self) -> bool:
        """Checks if dependencies, models, and checkpoints are available to run."""
        pass

    @abstractmethod
    def get_info(self) -> Dict[str, Any]:
        """Returns metadata about the engine (status, version, model path, device)."""
        pass
