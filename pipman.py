#!/usr/bin/env python3

# FILE: pipman.py

import sys
import gi
gi.require_version('Gtk', '4.0')
from gi.repository import Gtk, Gio, GLib

# Import our refactored window class
from ui.window import PipManagerWindow

class PipManagerApp(Gtk.Application):
    """The main GTK Application class."""
    def __init__(self, *args, **kwargs):
        # --- FIX FOR BUG #3 ---
        # The application ID must be a valid format (no dots in segments).
        # Version numbers do not belong in the ID.
        super().__init__(*args, application_id="com.yash.pipmanager", flags=Gio.ApplicationFlags.FLAGS_NONE, **kwargs)
        # --- END FIX ---
        self.window = None

    def do_activate(self):
        """Called when the application is activated."""
        if not self.window:
            self.window = PipManagerWindow(application=self)
        self.window.present()

def _gtk_log_handler(domain, level, message):
    if "GtkText - did not receive a focus-out event." in message:
        return # Suppress this specific warning
    # Otherwise, let GLib handle it normally
    GLib.log_default_handler(domain, level, message)

if __name__ == "__main__":
    # Set the custom log handler before the application starts
    GLib.log_set_handler("Gtk", GLib.LogLevelFlags.LEVEL_WARNING, _gtk_log_handler)
    
    app = PipManagerApp()
    sys.exit(app.run(sys.argv))