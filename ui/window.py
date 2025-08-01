import gi
gi.require_version('Gtk', '4.0')
from gi.repository import Gtk, GLib, Gio, Pango

# Import our new logic class
from services.app_logic import AppLogic
from models.package import Package

@Gtk.Template(filename='ui/pipman.ui')
class PipManagerWindow(Gtk.ApplicationWindow):
    __gtype_name__ = 'PipManagerWindow'

    # --- Template Children (UI Widgets) ---
    details_button = Gtk.Template.Child()
    search_entry = Gtk.Template.Child()
    package_entry = Gtk.Template.Child()
    install_button = Gtk.Template.Child()
    column_view = Gtk.Template.Child()
    refresh_button = Gtk.Template.Child()
    check_button = Gtk.Template.Child()
    clear_cache_button = Gtk.Template.Child()
    update_button = Gtk.Template.Child()
    uninstall_button = Gtk.Template.Child()
    output_textview = Gtk.Template.Child()
    total_size_label = Gtk.Template.Child()

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.scroll_position = 0

        # The ListStore holds the master list of all package names
        self.list_store = Gio.ListStore(item_type=Gtk.StringObject)

        # --- NEW: Create a filter and a model that uses it ---
        self.filter = Gtk.CustomFilter.new(self._filter_func)
        self.filter_model = Gtk.FilterListModel(model=self.list_store, filter=self.filter)
        # --- END NEW ---

        # --- Setup AppLogic ---
        # AppLogic now handles all state. The window is stateless.
        logic_callbacks = {
            'log_output': self.log_output,
            'set_busy': self.set_ui_busy,
            'update_package_list': self.update_package_list_store,
            'update_package_view': self.update_package_view,
            'set_total_size_label': self.total_size_label.set_text,
            'set_cache_button_tooltip': self.clear_cache_button.set_tooltip_text,
            'show_details_dialog': self.show_details_dialog,
            'update_button_sensitivity': self._update_button_sensitivity,
        }
        self.logic = AppLogic(callbacks=logic_callbacks)
        
        # --- UI Initialization ---
        self.log_buffer = self.output_textview.get_buffer()
        self.log_buffer.create_tag("header", weight=Pango.Weight.BOLD)
        
        # --- NEW: Connect search bar signal ---
        self.search_entry.connect("search-changed", self.on_search_changed)
        # --- END NEW ---
        
        self.setup_column_view()

        self.log_output("Initializing Pip Manager...")
        self.logic.load_packages()

    # --- UI Setup ---
    def setup_column_view(self):
        # 1.  Build the columns exactly as before …
        # -------------------------------------------
        # --- FIX: Use a custom sorter for clarity ---
        self.name_sorter    = Gtk.CustomSorter.new(self._name_sort_func)
        self.version_sorter = Gtk.CustomSorter.new(self._version_sort_func)
        self.size_sorter    = Gtk.CustomSorter.new(self._size_sort_func)

        self.column_view.append_column(
            self._create_column("Package Name", self._bind_name, self.name_sorter))
        self.column_view.append_column(
            self._create_column("Version",      self._bind_version, self.version_sorter))
        self.column_view.append_column(
            self._create_column("Size",         self._bind_size,    self.size_sorter))

        # 2.  **Get the special sorter from the view and give it to the sort model**
        # -------------------------------------------------------------------------
        view_sorter = self.column_view.get_sorter()
        view_sorter.connect("changed", self.on_sorter_changed)
        self.sort_model = Gtk.SortListModel(model=self.filter_model,
                                            sorter=view_sorter)

        # 3.  Wrap selection and finish as before
        # ---------------------------------------
        self.selection_model = Gtk.SingleSelection(model=self.sort_model)
        self.selection_model.connect("selection-changed", self.on_selection_changed)
        self.column_view.set_model(self.selection_model)

        # 4.  (Optional) choose an initial order
        # --------------------------------------
        columns = self.column_view.get_columns()
        if columns and columns.get_n_items() > 0:
            self.column_view.sort_by_column(columns.get_item(0),
                                            Gtk.SortType.ASCENDING)

    def _create_column(self, title: str, bind_callback, sorter: Gtk.Sorter):
        """Helper to create a ColumnViewColumn with a label factory and a sorter."""
        factory = Gtk.SignalListItemFactory()
        factory.connect("setup", lambda fac, item: item.set_child(Gtk.Label(xalign=0)))
        factory.connect("bind", bind_callback)
        
        column = Gtk.ColumnViewColumn(title=title, factory=factory)
        
        column.set_sorter(sorter)     # Set the sorter for this column
        return column

    # --- Data Binding Callbacks ---
    def _get_pkg_from_list_item(self, list_item) -> Package | None:
        """Gets the package object from AppLogic based on the list item."""
        string_object = list_item.get_item()
        if not string_object: return None
        pkg_name = string_object.get_string()
        return self.logic.packages_data.get(pkg_name)

    def _bind_name(self, factory, list_item):
        """Binds the package name."""
        label = list_item.get_child()
        pkg = self._get_pkg_from_list_item(list_item)
        if pkg:
            label.set_text(pkg.name)

    def _bind_version(self, factory, list_item):
        """Binds the package version."""
        label = list_item.get_child()
        pkg = self._get_pkg_from_list_item(list_item)
        if pkg:
            label.set_markup(pkg.display_version)

    def _bind_size(self, factory, list_item):
        """Binds the package size."""
        label = list_item.get_child()
        pkg = self._get_pkg_from_list_item(list_item)
        if pkg:
            label.set_text(pkg.size_str)

    # --- UI Update Callbacks (from AppLogic to UI) ---
    def update_package_list_store(self, package_names: list[str]):
        """Receives a list of package names from AppLogic and updates the store."""
        self.list_store.remove_all()
        for name in package_names:
            self.list_store.append(Gtk.StringObject.new(name))
        # The SortListModel will re-sort automatically.
        self._update_button_sensitivity()

    def update_package_view(self, pkg_name: str):
        """Tells the list view to redraw a specific row."""
        # --- FIX: Preserve scroll position and selection ---
        selected_item = self.selection_model.get_selected_item()
        selected_pkg_name = None
        if selected_item:
            selected_pkg_name = selected_item.get_string()

        for i, item in enumerate(self.list_store):
            if item.get_string() == pkg_name:
                self.list_store.items_changed(i, 1, 1)
                break

        if selected_pkg_name:
            # Find the Gtk.StringObject for the previously selected package
            for i, item in enumerate(self.sort_model): # Iterate through the *sorted* model
                if item.get_string() == selected_pkg_name:
                    # Reselect the item in the selection model
                    self.selection_model.select_item(i, True)
                    break

    def set_ui_busy(self, busy: bool):
        """Toggles the sensitivity of UI widgets."""
        self.column_view.set_sensitive(not busy)
        for widget in [self.package_entry, self.install_button, self.refresh_button, 
                       self.check_button, self.clear_cache_button]:
            widget.set_sensitive(not busy)
        # Selection-dependent buttons have their own logic
        self._update_button_sensitivity()

    def _update_button_sensitivity(self):
        """Updates button sensitivity based on selection and busy state."""
        is_busy = self.logic.is_busy
        selected_item = self.selection_model.get_selected_item()
        pkg = None
        if selected_item:
             pkg = self.logic.packages_data.get(selected_item.get_string())

        can_interact = (pkg is not None) and not is_busy
        
        self.details_button.set_sensitive(can_interact)
        self.uninstall_button.set_sensitive(can_interact)
        self.update_button.set_sensitive(can_interact and pkg.is_outdated if pkg else False)

    # --- UI Event Handlers (from User to AppLogic) ---
    @Gtk.Template.Callback()
    def on_install_clicked(self, widget):
        package_name = self.package_entry.get_text().strip()
        self.logic.install_package(package_name)
        self.package_entry.set_text("")

    @Gtk.Template.Callback()
    def on_update_clicked(self, widget):
        selected_item = self.selection_model.get_selected_item()
        if selected_item:
            self.logic.update_package(selected_item.get_string())

    @Gtk.Template.Callback()
    def on_uninstall_clicked(self, widget):
        selected_item = self.selection_model.get_selected_item()
        if selected_item:
            pkg_name = selected_item.get_string()
            dialog = Gtk.MessageDialog(transient_for=self, modal=True, message_type=Gtk.MessageType.QUESTION,
                                       buttons=Gtk.ButtonsType.YES_NO, text=f"Uninstall '{pkg_name}'?")
            dialog.connect("response", self._on_uninstall_dialog_response, pkg_name)
            dialog.present()

    def _on_uninstall_dialog_response(self, dialog, response_id, pkg_name):
        if response_id == Gtk.ResponseType.YES:
            self.logic.uninstall_package(pkg_name)
        dialog.destroy()

    @Gtk.Template.Callback()
    def on_refresh_clicked(self, widget):
        self.logic.load_packages()

    @Gtk.Template.Callback()
    def on_clear_cache_clicked(self, widget):
        self.logic.clear_pip_cache()

    @Gtk.Template.Callback()
    def on_check_dependencies_clicked(self, widget):
        self.logic.check_dependencies()
        
    def on_selection_changed(self, selection, position, n_items):
        self._update_button_sensitivity()

    # --- Logging ---
    def log_output(self, message: str, is_header: bool = False):
        end_iter = self.log_buffer.get_end_iter()
        if is_header:
            if self.log_buffer.get_char_count() > 0: self.log_buffer.insert(end_iter, "\n")
            self.log_buffer.insert_with_tags_by_name(self.log_buffer.get_end_iter(), f"--- {message} ---\n", "header")
        else:
            self.log_buffer.insert(self.log_buffer.get_end_iter(), f"{message}\n")
        
        GLib.idle_add(self._scroll_output_to_end)

    def _scroll_output_to_end(self):
        adj = self.output_textview.get_parent().get_vadjustment()
        adj.set_value(adj.get_upper())
        return False

    def _filter_func(self, string_object) -> bool:
        """
        This is the actual filter function. It returns True if a package
        should be shown, and False if it should be hidden.
        """
        # Get the current search text, remove whitespace, and make it lowercase
        search_text = self.search_entry.get_text().strip().lower()
        
        # If the search bar is empty, show everything
        if not search_text:
            return True
            
        # Get the package name from the Gtk.StringObject and make it lowercase
        pkg_name = string_object.get_string().lower()
        
        # Return True only if the search text is part of the package name
        return search_text in pkg_name

    @Gtk.Template.Callback()
    def on_search_changed(self, search_entry):
        """
        Called every time the user types in the search bar.
        This tells the filter that it needs to be re-evaluated.
        """
        # The Gtk.FilterChange.DIFFERENT flag tells the filter that it needs to
        # re-run the _filter_func on all items.
        self.filter.changed(Gtk.FilterChange.DIFFERENT)

    @Gtk.Template.Callback()
    def on_details_clicked(self, widget):
        """Called when the 'Details' button is clicked."""
        selected_item = self.selection_model.get_selected_item()
        if selected_item:
            self.logic.show_package_details(selected_item.get_string())

    def show_details_dialog(self, details: dict):
        """Creates and displays a dialog with the package details."""
        dialog = Gtk.MessageDialog(transient_for=self, modal=True,
                                   message_type=Gtk.MessageType.INFO,
                                   buttons=Gtk.ButtonsType.CLOSE)
        
        # --- FIX START ---
        # Combine primary and secondary text into a single markup string
        # because Gtk.MessageDialog in GTK4 does not have format_secondary_markup.
        
        # Primary title part (e.g., "PackageName (Version)")
        title_markup = f"<b>{details.get('Name', 'N/A')}</b> <small>({details.get('Version', 'N/A')})</small>"
        
        # Create a more detailed secondary text, making some fields bold
        lines = []
        # Define which keys we want to display and in what order
        display_keys = ['Summary', 'Home-page', 'Author', 'License', 'Requires', 'Required-by']
        for key in display_keys:
            if key in details and details[key]: # Only show if it exists and is not empty
                # IMPORTANT: Escape the value to prevent any potential Pango markup
                # in the package details from breaking the display or causing crashes.
                escaped_value = GLib.markup_escape_text(details[key])
                lines.append(f"<b>{key}:</b> {escaped_value}")
        
        # Join the secondary lines with newlines
        secondary_text_markup = "\n".join(lines)

        # Join the title and secondary text with some spacing between them
        full_dialog_markup = f"{title_markup}\n\n{secondary_text_markup}"

        # Set the entire combined content as the main dialog message using markup
        dialog.set_markup(full_dialog_markup)
        # --- FIX END ---
        
        # Connect the close response and show the dialog
        dialog.connect("response", lambda d, r: d.destroy())
        dialog.present()

    def _version_sort_func(self, pkg1_str_obj, pkg2_str_obj, *args):
        pkg1 = self.logic.packages_data.get(pkg1_str_obj.get_string())
        pkg2 = self.logic.packages_data.get(pkg2_str_obj.get_string())

        if not pkg1 or not pkg2: return 0 # Should not happen

        # Prioritize outdated packages
        if pkg1.is_outdated and not pkg2.is_outdated: return -1
        if not pkg1.is_outdated and pkg2.is_outdated: return 1

        # Fallback to alphabetical sort if both are outdated or both are up-to-date
        return GLib.strcmp0(pkg1.name, pkg2.name)

    def _size_sort_func(self, pkg1_str_obj, pkg2_str_obj, *args):
        pkg1 = self.logic.packages_data.get(pkg1_str_obj.get_string())
        pkg2 = self.logic.packages_data.get(pkg2_str_obj.get_string())

        if not pkg1 or not pkg2: return 0 # Should not happen

        # Sort by size_bytes (numeric)
        return pkg1.size_bytes - pkg2.size_bytes

    def _name_sort_func(self, pkg1_str_obj, pkg2_str_obj, *args):
        pkg1 = self.logic.packages_data.get(pkg1_str_obj.get_string())
        pkg2 = self.logic.packages_data.get(pkg2_str_obj.get_string())

        if not pkg1 or not pkg2: return 0 # Should not happen

        # A negative value if a < b, 0 if a = b, a positive value if a > b
        return GLib.strcmp0(pkg1.name.lower(), pkg2.name.lower())

    def on_sorter_changed(self, sorter, *args):
        """
        When sorting changes, deselect any selected package to prevent
        scrollbar jumping issues. This is called whenever a column header is clicked.
        """
        # 1. Deselect any currently selected package
        self.selection_model.unselect_all()
        
        # 2. Save the current scroll position as a fallback
        scrolled_window = self.column_view.get_parent()
        if scrolled_window:
            adjustment = scrolled_window.get_vadjustment()
            self.scroll_position = adjustment.get_value()
            
            # Schedule scroll position restoration with bounds checking
            GLib.timeout_add(50, self.restore_scroll_position)
        
        # 3. Update button sensitivity to reflect deselection
        self._update_button_sensitivity()

    def restore_scroll_position(self):
        """Restores the scrollbar to its saved position with bounds checking."""
        scrolled_window = self.column_view.get_parent()
        if not scrolled_window:
            return False # Stop the timeout

        adjustment = scrolled_window.get_vadjustment()
        max_position = adjustment.get_upper() - adjustment.get_page_size()
        valid_position = min(max_position, self.scroll_position)
        valid_position = max(0, valid_position)  # Ensure it's not negative
        adjustment.set_value(valid_position)
        
        return False