#!/usr/bin/env python3

import sys
import gi
gi.require_version('Gtk', '4.0')
from gi.repository import Gtk, GLib, Gio, Pango

import subprocess
import json
import threading
from pathlib import Path

# --- Data Caching ---
CACHE_DIR = Path(GLib.get_user_cache_dir()) / 'pipman'
CACHE_FILE = CACHE_DIR / 'sizes.json'
CACHE_DIR.mkdir(parents=True, exist_ok=True)

def load_size_cache():
    if not CACHE_FILE.exists(): return {}
    try:
        with open(CACHE_FILE, 'r') as f: return json.load(f)
    except (json.JSONDecodeError, IOError): return {}

def save_size_cache(cache):
    with open(CACHE_FILE, 'w') as f: json.dump(cache, f, indent=2)


class PipService:
    """A service class to handle all subprocess calls to pip."""
    def run_command(self, command_list, log_callback):
        try:
            process = subprocess.Popen(command_list, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, encoding='utf-8')
            for line in iter(process.stdout.readline, ''):
                log_callback(line.strip())
            
            process.wait()
            _, stderr = process.communicate()
            return process.returncode, stderr
        except FileNotFoundError:
            return -1, f"FATAL ERROR: '{command_list[0]}' command not found."
        except Exception as e:
            return -1, f"An unexpected error occurred: {e}"

    def get_packages(self):
        # Always succeed at listing locally-installed packages
        proc_all = subprocess.run(
            ['pip', 'list', '--user', '--format=json'],
            capture_output=True, text=True, check=False, encoding='utf-8'
        )
        packages = json.loads(proc_all.stdout or '[]')
        if proc_all.returncode != 0:
            # pip list failed; return empty list so UI still comes up
            packages = []

        # Outdated list is optional; swallow network errors
        outdated_cmd = ['pip', 'list', '--user', '--outdated', '--format=json']
        proc_outdated = subprocess.run(outdated_cmd, capture_output=True, text=True, check=False, encoding='utf-8')
        outdated_info = {p['name']: p['latest_version'] for p in json.loads(proc_outdated.stdout or '[]')} if proc_outdated.returncode == 0 and proc_outdated.stdout else {}
        
        return packages, outdated_info

    def get_package_size(self, package_name):
        try:
            cmd = ['pip', 'show', '--files', package_name]
            proc = subprocess.run(cmd, capture_output=True, text=True, check=True, encoding='utf-8')
            
            files_section = False; total_size = 0; base_path = ""
            for line in proc.stdout.splitlines():
                if line.startswith('Location:'): base_path = Path(line.split(':', 1)[1].strip())
                if files_section and base_path:
                    file_path = base_path / line.strip()
                    if file_path.is_file(): total_size += file_path.stat().st_size
                if line.startswith('Files:'): files_section = True
            
            if total_size == 0: return "0 KB"
            size_mb = total_size / (1024 * 1024)
            return f"{size_mb:.2f} MB" if size_mb >= 1 else f"{total_size / 1024:.1f} KB"
        except Exception as e:
            print(f"Error calculating size for {package_name}: {e}")
            return "Error"

    # NEW: Method to specifically run `pip check`
    def check_dependencies(self):
        """Runs `pip check` and captures its output."""
        try:
            # `pip check` prints its report to stdout and returns a non-zero exit code
            # if issues are found. `check=False` prevents this from raising an exception.
            process = subprocess.run(['pip', 'check'], capture_output=True, text=True, check=False, encoding='utf-8')
            # The useful output can be in stdout (for errors) or stderr (for other issues)
            output = process.stdout + process.stderr
            return process.returncode, output.strip()
        except FileNotFoundError:
            return -1, "FATAL ERROR: 'pip' command not found."
        except Exception as e:
            return -1, f"An unexpected error occurred: {e}"


@Gtk.Template(filename='pipman.ui')
class PipManagerWindow(Gtk.ApplicationWindow):
    __gtype_name__ = 'PipManagerWindow'

    package_entry = Gtk.Template.Child()
    install_button = Gtk.Template.Child()
    column_view = Gtk.Template.Child()
    refresh_button = Gtk.Template.Child()
    check_button = Gtk.Template.Child() # NEW: Link to the check button
    update_button = Gtk.Template.Child()
    uninstall_button = Gtk.Template.Child()
    output_textview = Gtk.Template.Child()

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

        self.pip_service = PipService()
        self.is_busy = False
        self.size_cache = load_size_cache()
        self.packages_data = {}

        self.setup_list_view()
        self.log_output("Initializing Pip Manager...")
        self.load_packages_threaded()

    def setup_list_view(self):
        self.list_store = Gio.ListStore(item_type=Gtk.StringObject)
        self.selection_model = Gtk.SingleSelection(model=self.list_store)
        self.selection_model.connect("selection-changed", self.on_selection_changed)
        self.column_view.set_model(self.selection_model)

        col_name = Gtk.ColumnViewColumn(title="Package Name", factory=self._create_label_factory(self._bind_name))
        self.column_view.append_column(col_name)
        col_version = Gtk.ColumnViewColumn(title="Version", factory=self._create_label_factory(self._bind_version))
        self.column_view.append_column(col_version)
        col_size = Gtk.ColumnViewColumn(title="Size", factory=self._create_label_factory(self._bind_size))
        self.column_view.append_column(col_size)

    def _create_label_factory(self, bind_callback):
        factory = Gtk.SignalListItemFactory()
        factory.connect("setup", lambda fac, item: item.set_child(Gtk.Label(xalign=0)))
        factory.connect("bind", bind_callback)
        return factory

    def _get_pkg_data_from_item(self, list_item):
        item_str = list_item.get_item().get_string()
        return self.packages_data.get(item_str)

    def _bind_name(self, factory, list_item):
        label = list_item.get_child()
        pkg_data = self._get_pkg_data_from_item(list_item)
        if pkg_data: label.set_text(pkg_data['name'])

    def _bind_version(self, factory, list_item):
        label = list_item.get_child()
        pkg_data = self._get_pkg_data_from_item(list_item)
        if pkg_data: label.set_markup(pkg_data['display_version'])
    
    def _bind_size(self, factory, list_item):
        label = list_item.get_child()
        pkg_data = self._get_pkg_data_from_item(list_item)
        if pkg_data: label.set_text(pkg_data['display_size'])

    def set_ui_busy(self, busy):
        self.is_busy = busy
        # NEW: Add check_button to the list of widgets to disable
        for widget in [self.package_entry, self.install_button, self.refresh_button, self.check_button]:
            widget.set_sensitive(not busy)
        self._update_button_sensitivity()

    def log_output(self, message, is_header=False):
        def append_log():
            buffer = self.output_textview.get_buffer()
            tag = buffer.create_tag("header", weight=Pango.Weight.BOLD)
            end_iter = buffer.get_end_iter()
            
            if is_header:
                buffer.insert_with_tags_by_name(end_iter, f"\n--- {message} ---\n", "header")
            else:
                buffer.insert(end_iter, f"{message}\n")
            
            GLib.idle_add(lambda: self.output_textview.get_parent().get_vadjustment().set_value(self.output_textview.get_parent().get_vadjustment().get_upper()) or False)
            return False
        GLib.idle_add(append_log)

    def run_pip_command_threaded(self, command_list, operation_name, package_name_for_callback=None, callback_on_finish=None):
        if self.is_busy: return
        self.set_ui_busy(True)
        self.log_output(f"Starting: {operation_name}", is_header=True)
        
        def worker():
            return_code, stderr = self.pip_service.run_command(command_list, self.log_output)
            
            def finish_on_main_thread():
                if return_code == 0:
                    self.log_output(f"Success: {operation_name} completed.")
                    if callback_on_finish:
                        callback_on_finish(package_name_for_callback)
                else:
                    error_msg = f"Error during '{operation_name}' (code {return_code}).\n"
                    if stderr: error_msg += f"Details:\n{stderr}"
                    self.log_output(error_msg)
                
                self.set_ui_busy(False)
            
            GLib.idle_add(finish_on_main_thread)

        threading.Thread(target=worker, daemon=True).start()

    @Gtk.Template.Callback()
    def load_packages_threaded(self, widget=None):
        if self.is_busy: return
        self.set_ui_busy(True)
        self.log_output("Refreshing package list", is_header=True)
        
        def worker():
            try:
                packages, outdated_info = self.pip_service.get_packages()
                GLib.idle_add(self.update_package_list_store, packages, outdated_info)
            except Exception as e:          # keep broad catch for other errors
                GLib.idle_add(self.log_output, f"Error loading packages. Are you connected to the internet?\nDetails: {e}")
            finally:
                GLib.idle_add(self.set_ui_busy, False)

        threading.Thread(target=worker, daemon=True).start()

    def update_package_list_store(self, packages, outdated_info):
        self.list_store.remove_all()
        self.packages_data.clear()

        for pkg in sorted(packages, key=lambda p: p['name'].lower()):
            name, version = pkg['name'], pkg['version']
            is_outdated = name in outdated_info
            
            self.packages_data[name] = {
                'name': name, 'version': version, 'is_outdated': is_outdated,
                'display_version': f"{version} <span color='#d98417' weight='bold'>(→ {outdated_info[name]})</span>" if is_outdated else version,
                'display_size': self.size_cache.get(name, "...")
            }
            self.list_store.append(Gtk.StringObject.new(name))
        
        self.log_output(f"Found {len(packages)} packages ({len(outdated_info)} outdated).")

    def calculate_size_for_package_threaded(self, name):
        if not name: return
        self.log_output(f"Calculating size for '{name}'...")
        
        def worker():
            size = self.pip_service.get_package_size(name)
            self.size_cache[name] = size
            save_size_cache(self.size_cache)
            GLib.idle_add(self.update_package_size_in_view, name, size)
            GLib.idle_add(self.log_output, f"Size for '{name}': {size}")

        threading.Thread(target=worker, daemon=True).start()

    def update_package_size_in_view(self, name, display_size):
        if name in self.packages_data:
            self.packages_data[name]['display_size'] = display_size
            for i in range(self.list_store.get_n_items()):
                if self.list_store.get_item(i).get_string() == name:
                    self.list_store.items_changed(i, 1, 1); break
    
    def _update_button_sensitivity(self):
        pos = self.selection_model.get_selected()
        can_interact = (pos != Gtk.INVALID_LIST_POSITION) and not self.is_busy
        self.uninstall_button.set_sensitive(can_interact)
        
        is_outdated = False
        if can_interact:
            name = self.list_store.get_item(pos).get_string()
            if self.packages_data.get(name, {}).get('is_outdated'):
                is_outdated = True
        self.update_button.set_sensitive(is_outdated and can_interact)

    def on_selection_changed(self, selection, position, n_items):
        self._update_button_sensitivity()

    @Gtk.Template.Callback()
    def on_install_clicked(self, widget):
        package_name = self.package_entry.get_text().strip()
        if not package_name:
            self.log_output("Please enter a package name.")
            return
        
        cmd = ['pip', 'install', '--user', package_name]
        def on_install_success(pkg_name):
            self.load_packages_threaded()
            self.calculate_size_for_package_threaded(pkg_name)

        self.run_pip_command_threaded(cmd, f"Install '{package_name}'", package_name, on_install_success)
        self.package_entry.set_text("")

    @Gtk.Template.Callback()
    def on_update_clicked(self, widget):
        pos = self.selection_model.get_selected()
        if pos != Gtk.INVALID_LIST_POSITION:
            name = self.list_store.get_item(pos).get_string()
            cmd = ['pip', 'install', '--user', '--upgrade', name]
            self.run_pip_command_threaded(cmd, f"Update '{name}'", name, lambda pkg_name: self.load_packages_threaded())

    @Gtk.Template.Callback()
    def on_uninstall_clicked(self, widget):
        pos = self.selection_model.get_selected()
        if pos != Gtk.INVALID_LIST_POSITION:
            name = self.list_store.get_item(pos).get_string()
            dialog = Gtk.MessageDialog(transient_for=self, modal=True, message_type=Gtk.MessageType.QUESTION,
                                       buttons=Gtk.ButtonsType.YES_NO, text=f"Uninstall '{name}'?")
            dialog.connect("response", lambda d, r: self._on_uninstall_dialog_response(d, r, name))
            dialog.show()
            
    # NEW: Handler for the "Check Dependencies" button
    @Gtk.Template.Callback()
    def on_check_dependencies_clicked(self, widget):
        if self.is_busy: return
        self.set_ui_busy(True)
        self.log_output("Checking package dependencies", is_header=True)
        
        def worker():
            return_code, output = self.pip_service.check_dependencies()
            
            def finish_on_main_thread():
                if return_code == 0:
                    self.log_output("Success: No broken requirements found.")
                else:
                    self.log_output(f"Found dependency issues:\n{output}")
                self.set_ui_busy(False)
            
            GLib.idle_add(finish_on_main_thread)

        threading.Thread(target=worker, daemon=True).start()

    def _on_uninstall_dialog_response(self, dialog, response_id, name):
        if response_id == Gtk.ResponseType.YES:
            if name in self.size_cache:
                del self.size_cache[name]
                save_size_cache(self.size_cache)
            cmd = ['pip', 'uninstall', '-y', name]
            self.run_pip_command_threaded(cmd, f"Uninstall '{name}'", name, lambda pkg_name: self.load_packages_threaded())
        dialog.destroy()


class PipManagerApp(Gtk.Application):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, application_id="com.yash.pipmanager.v3.1", flags=Gio.ApplicationFlags.FLAGS_NONE, **kwargs)
        self.window = None

    def do_activate(self):
        if not self.window:
            self.window = PipManagerWindow(application=self)
        self.window.present()

if __name__ == "__main__":
    app = PipManagerApp()
    sys.exit(app.run(sys.argv))