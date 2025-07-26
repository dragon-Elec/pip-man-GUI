# FILE: models/package_gobject.py

import gi
gi.require_version('Gtk', '4.0')
from gi.repository import GObject

from models.package import Package # Our original data class

class PackageGObject(GObject.Object):
    __gtype_name__ = "PackageGObject"
    """
    A GObject wrapper for the Package dataclass.
    This makes package data accessible to GTK models, sorters, and filters.
    """
    # Define GObject properties that mirror the Package data
    # This is how you tell GTK about your data's structure
    name = GObject.Property(type=str, nick='Package Name')
    version = GObject.Property(type=str, nick='Version')
    is_outdated = GObject.Property(type=bool, nick='Is Outdated', default=False)
    size_bytes = GObject.Property(type=int, nick='Size in Bytes')
    size_str = GObject.Property(type=str, nick='Formatted Size')
    display_version = GObject.Property(type=str, nick='Display Version')

    def __init__(self, pkg: Package):
        super().__init__()
        self._pkg = pkg # Keep the original data object

    # When GTK asks for a property, we return the data from our internal object
    def do_get_property(self, prop):
        if prop.name == 'name':
            return self._pkg.name
        elif prop.name == 'version':
            return self._pkg.version
        elif prop.name == 'is_outdated':
            return self._pkg.is_outdated
        elif prop.name == 'size_bytes':
            return self._pkg.size_bytes
        elif prop.name == 'size_str':
            return self._pkg.size_str
        elif prop.name == 'display_version':
            return self._pkg.display_version
        else:
            raise AttributeError(f'unknown property {prop.name}')

    # Allow the UI to get the underlying package data if needed
    def get_package_data(self) -> Package:
        return self._pkg
