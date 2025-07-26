# FILE: services/app_logic.py

import threading
from gi.repository import GLib

# Import project modules
from models.package import Package
from services.pip_service import PipService
from services.cache_service import load_size_cache, save_size_cache

class AppLogic:
    """
    Handles all the business logic and non-UI operations for PipManager.
    This class communicates with the UI through a set of callbacks.
    """
    def __init__(self, callbacks: dict):
        self.callbacks = callbacks
        self.pip_service = PipService()
        self.callbacks['set_cache_button_label'] = lambda x: None # Add default
        self.callbacks['update_button_sensitivity'] = lambda: None
        
        # --- State Management (Single Source of Truth) ---
        self.is_busy = False
        self.packages_data: dict[str, Package] = {}
        self.size_cache = load_size_cache()
        
        # --- Threading Locks ---
        self.active_size_calculations = 0
        self._main_lock = threading.Lock()
        self._size_calculation_lock = threading.Lock()

    # --- Private UI Callback Wrappers ---
    def _ui_log(self, message: str, is_header: bool = False):
        GLib.idle_add(self.callbacks['log_output'], message, is_header)

    def _ui_set_busy_on_main_thread(self, busy: bool):
        GLib.idle_add(self.callbacks['set_busy'], busy)

    def _ui_update_package_list(self):
        GLib.idle_add(self.callbacks['update_package_list'], list(self.packages_data.keys()))

    def _ui_update_package_view(self, pkg_name: str):
        GLib.idle_add(self.callbacks['update_package_view'], pkg_name)

    def _ui_set_total_size_label(self, text: str):
        GLib.idle_add(self.callbacks['set_total_size_label'], text)

    def _ui_set_cache_button_tooltip(self, text: str):
        GLib.idle_add(self.callbacks['set_cache_button_tooltip'], text)

    def _begin_operation(self, operation_name: str, is_header: bool = True) -> bool:
        with self._main_lock:
            if self.is_busy:
                self._ui_log("Operation already in progress. Please wait.")
                return False
            self.is_busy = True
        
        self._ui_set_busy_on_main_thread(True)
        if operation_name:
            self._ui_log(operation_name, is_header=is_header)
        return True

    def _end_operation(self):
        with self._main_lock:
            self.is_busy = False
        self._ui_set_busy_on_main_thread(False)

    def _calculate_and_display_total_size(self):
        total_bytes = sum(pkg.size_bytes for pkg in self.packages_data.values())
        
        if total_bytes < 1024: display_text = f"Total Size: {total_bytes} B"
        elif total_bytes < 1024**2: display_text = f"Total Size: {total_bytes / 1024:.2f} KB"
        elif total_bytes < 1024**3: display_text = f"Total Size: {total_bytes / (1024**2):.2f} MB"
        else: display_text = f"Total Size: {total_bytes / (1024**3):.2f} GB"
        
        self._ui_set_total_size_label(display_text)

    # <<< FIX #1: Added a helper to parse the cached size string into bytes.
    def _parse_size_str_to_bytes(self, size_str: str) -> int:
        """Parses a size string like '10.5 MB' into bytes."""
        if not size_str or "..." in size_str or "Error" in size_str:
            return 0
        
        multipliers = {"B": 1, "KB": 1024, "MB": 1024**2, "GB": 1024**3}
        total_bytes = 0
        try:
            value_str = "".join(filter(lambda c: c.isdigit() or c == '.', size_str))
            unit_str = "".join(filter(str.isalpha, size_str)).upper()
            if value_str and unit_str in multipliers:
                value = float(value_str)
                total_bytes = value * multipliers[unit_str]
        except (ValueError, IndexError):
            self._ui_log(f"Warning: Could not parse size string '{size_str}'.")
            return 0
            
        return int(total_bytes)

    # --- Public Methods (Called by the UI) ---
    def load_packages(self):
        if not self._begin_operation("Refreshing package list"):
            return
        
        self._ui_set_total_size_label("Total Size: ...")
        threading.Thread(target=self._initial_load_worker, daemon=True).start()

    def install_package(self, package_name: str):
        if not package_name:
            self._ui_log("Please enter a package name.")
            return
        
        cmd = ['pip', 'install', '--user', package_name]
        self._run_pip_command_threaded(
            cmd, 
            f"Install '{package_name}'", 
            package_name, 
            self._on_install_success
        )

    def update_package(self, pkg_name: str):
        # <--- ADD THIS LOGIC ---
        # Invalidate the cache for this package to force a size recalculation after update.
        if pkg_name in self.size_cache:
            del self.size_cache[pkg_name]
        # ---> END OF ADDITION

        cmd = ['pip', 'install', '--user', '--upgrade', pkg_name]
        self._run_pip_command_threaded(cmd, f"Update '{pkg_name}'", pkg_name, lambda n: self.load_packages())

    def uninstall_package(self, pkg_name: str):
        if pkg_name in self.size_cache:
            del self.size_cache[pkg_name]
            save_size_cache(self.size_cache)
        
        cmd = ['pip', 'uninstall', '-y', pkg_name]
        self._run_pip_command_threaded(cmd, f"Uninstall '{pkg_name}'", pkg_name, lambda n: self.load_packages())

    def clear_pip_cache(self):
        cmd = ['pip', 'cache', 'purge']
        self._run_pip_command_threaded(cmd, "Clear pip cache")

    def check_dependencies(self):
        if not self._begin_operation("Checking package dependencies"):
            return
        threading.Thread(target=self._check_dependencies_worker, daemon=True).start()

    def show_package_details(self, pkg_name: str):
        if not self._begin_operation(f"Fetching details for '{pkg_name}'...", is_header=False):
            return

        def worker():
            try:
                details = self.pip_service.get_package_details(pkg_name)
                if details:
                    GLib.idle_add(self.callbacks['show_details_dialog'], details)
                else:
                    self._ui_log(f"Could not retrieve details for '{pkg_name}'.")
            finally:
                self._end_operation()

        threading.Thread(target=worker, daemon=True).start()

    # --- Internal Worker Threads ---
    def _initial_load_worker(self):
        """Phase 1: Loads local packages and sizes for immediate UI display."""
        try:
            packages_json = self.pip_service.get_local_packages()
            
            new_packages_data = {}
            for pkg_dict in sorted(packages_json, key=lambda p: p['name'].lower()):
                # Initially, assume all packages are up-to-date.
                pkg = Package(name=pkg_dict['name'], version=pkg_dict['version'])
                new_packages_data[pkg.name] = pkg

            with self._main_lock:
                self.packages_data = new_packages_data
            
            # Update the UI with the local packages immediately.
            self._ui_update_package_list()
            self._ui_log(f"Found {len(self.packages_data)} local packages. Checking for updates in the background...")
            
            # Start size calculation logic as before
            num_to_calculate = 0
            for name, pkg in self.packages_data.items():
                if name in self.size_cache:
                    pkg.size_str = self.size_cache[name]
                    pkg.size_bytes = self._parse_size_str_to_bytes(pkg.size_str)
                    self._ui_update_package_view(name)
                else:
                    pkg.size_str = "..."
                    num_to_calculate += 1
                    self.calculate_size_for_package(name)

            if num_to_calculate == 0:
                self._ui_log("All package sizes loaded from cache.")
                self._calculate_and_display_total_size()
                # We can end the primary "busy" state here if no sizes need calculating.
                # The update check will happen silently.
                self._end_operation()

            # --- KEY CHANGE: Start Phase 2 in a separate thread ---
            threading.Thread(target=self._check_for_updates_worker, daemon=True).start()

        except Exception as e:
            self._ui_log(f"A critical error occurred while loading local packages: {e}")
            self._end_operation()

    def _check_for_updates_worker(self):
        """Phase 2: Checks for outdated packages in the background and updates the UI."""
        self._ui_log("Checking for package updates...")
        outdated_info, status_message = self.pip_service.get_outdated_packages()
        
        if status_message:
            self._ui_log(status_message)
        
        if not outdated_info:
            if not status_message:
                self._ui_log("All packages are up to date.")
            return

        # Use a lock to safely modify the shared package data
        with self._main_lock:
            for name, latest_version in outdated_info.items():
                if name in self.packages_data:
                    self.packages_data[name].latest_version = latest_version
                    # Tell the UI to redraw just this one row
                    self._ui_update_package_view(name)
        
        # Tell the main thread to re-evaluate button sensitivity, as some packages
        # may now be updatable.
        GLib.idle_add(self.callbacks['update_button_sensitivity'])
        
        self._ui_log(f"Update check complete. Found {len(outdated_info)} outdated package(s).")

    def _run_pip_command_threaded(self, command_list, operation_name, pkg_name_for_callback=None, callback_on_finish=None):
        if not self._begin_operation(f"Starting: {operation_name}"):
            return
        
        def worker():
            success = False
            try:
                return_code, _ = self.pip_service.run_command(command_list, self._ui_log)
                if return_code == 0:
                    self._ui_log(f"Success: {operation_name} completed.")
                    if callback_on_finish:
                        callback_on_finish(pkg_name_for_callback)
                    success = True
                else:
                    self._ui_log(f"Error: '{operation_name}' failed (code {return_code}). See log for details.")
            finally:
                if not success or not callback_on_finish:
                    self._end_operation()
                
                if success and "cache" in command_list:
                    threading.Thread(target=self._update_cache_size_display_worker, daemon=True).start()
        
        threading.Thread(target=worker, daemon=True).start()

    def _check_dependencies_worker(self):
        try:
            return_code, output = self.pip_service.check_dependencies()
            if return_code == 0:
                self._ui_log("Success: No broken requirements found.")
            else:
                self._ui_log(f"""Found dependency issues:
{output}""")
        finally:
            self._end_operation()

    def _update_cache_size_display_worker(self):
        size_str = self.pip_service.get_cache_size()
        if size_str:
            self._ui_set_cache_button_tooltip(f"Purge pip cache ({size_str})")
        else:
            pass

    def _on_install_success(self, pkg_name: str):
        self.load_packages()

    def calculate_size_for_package(self, pkg_name: str):
        if not pkg_name: return
        self._adjust_ui_for_ongoing_calculations(calculation_starting=True)

        def worker():
            # This check is a failsafe, but get_package_size should be robust.
            if not self.pip_service: return 

            self._ui_log(f"Calculating size for '{pkg_name}'...")
            size_bytes, size_str = self.pip_service.get_package_size(pkg_name)

            with self._main_lock:
                if pkg_name not in self.packages_data:
                    self._ui_log(f"Calculation for '{pkg_name}' cancelled (package removed).")
                    self._adjust_ui_for_ongoing_calculations(calculation_starting=False)
                    return
                
                self.packages_data[pkg_name].size_bytes = size_bytes
                self.packages_data[pkg_name].size_str = size_str
                self.size_cache[pkg_name] = size_str
                save_size_cache(self.size_cache)

            self._ui_update_package_view(pkg_name)
            self._ui_log(f"Size for '{pkg_name}': {size_str}")
            self._adjust_ui_for_ongoing_calculations(calculation_starting=False)
        
        threading.Thread(target=worker, daemon=True).start()


    def _adjust_ui_for_ongoing_calculations(self, calculation_starting: bool):
        with self._size_calculation_lock:
            if calculation_starting:
                self.active_size_calculations += 1
                if self.active_size_calculations == 1:
                    self._ui_set_total_size_label("Total Size: Calculating...")
            else:
                self.active_size_calculations -= 1
                if self.active_size_calculations == 0:
                    self._calculate_and_display_total_size()
                    self._ui_log("All package sizes calculated and updated.")
                    # The main 'load_packages' task is now complete.
                    self._end_operation()