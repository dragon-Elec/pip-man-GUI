# FILE: models/package.py

from dataclasses import dataclass

@dataclass
class Package:
    """A simple data class to represent an installed Python package."""
    name: str
    version: str
    latest_version: str = ""
    size_str: str = "..."
    size_bytes: int = 0

    @property
    def is_outdated(self) -> bool:
        """Returns True if a newer version is available."""
        return bool(self.latest_version)

    @property
    def display_version(self) -> str:
        """Returns the version string formatted for the GTK view."""
        if self.is_outdated:
            return f"{self.version} <span color='#d98417' weight='bold'>(→ {self.latest_version})</span>"
        return self.version
