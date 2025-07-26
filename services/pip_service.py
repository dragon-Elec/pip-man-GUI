# FILE: services/pip_service.py

import subprocess
import json
from pathlib import Path
import socket
from importlib import metadata
from pathlib import Path

class PipService:
    """A service class to handle all subprocess calls to pip."""

    def run_command(self, command_list, log_callback):
        """Runs a generic command, streaming its output to the log_callback."""
        try:
            # Using Popen to capture output line-by-line for live logging
            process = subprocess.Popen(
                command_list,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT, # Combine stdout and stderr
                text=True,
                encoding='utf-8',
                bufsize=1 # Line-buffered
            )
            
            # Stream output to the logger
            for line in iter(process.stdout.readline, ''):
                log_callback(line.strip())
            
            process.wait()
            # Since we combined streams, stderr will be None.
            # The return code is the source of truth for success/failure.
            return process.returncode, ""

        except FileNotFoundError:
            err_msg = f"FATAL ERROR: '{command_list[0]}' command not found. Is pip installed and in your PATH?"
            log_callback(err_msg)
            return -1, err_msg
        except Exception as e:
            err_msg = f"An unexpected error occurred: {e}"
            log_callback(err_msg)
            return -1, err_msg

    def _has_internet_connection(self):
        """Checks for a live internet connection by connecting to a reliable host."""
        try:
            # Connect to a well-known, highly available DNS server with a short timeout.
            socket.create_connection(("8.8.8.8", 53), timeout=3)
            return True
        except (OSError, socket.timeout):
            return False

    def get_local_packages(self):
        """
        Fetches only the user-installed packages from the local environment.
        This is a fast, offline-safe operation.
        """
        proc_all = subprocess.run(
            ['pip', 'list', '--user', '--format=json'],
            capture_output=True, text=True, check=True, encoding='utf-8'
        )
        return json.loads(proc_all.stdout or '[]')

    def get_outdated_packages(self):
        """
        Checks for outdated packages. This is a network-intensive operation.
        Returns a tuple: (dict of outdated info, status message).
        """
        outdated_info = {}
        status_message = None
        
        if not self._has_internet_connection():
            status_message = "Offline: Skipping check for outdated packages."
        else:
            try:
                outdated_cmd = ['pip', 'list', '--user', '--outdated', '--format=json']
                proc_outdated = subprocess.run(
                    outdated_cmd, capture_output=True, text=True,
                    check=False, encoding='utf-8', timeout=15
                )
                if proc_outdated.returncode == 0 and proc_outdated.stdout:
                    outdated_info = {p['name']: p['latest_version'] for p in json.loads(proc_outdated.stdout or '[]')}
            except subprocess.TimeoutExpired:
                status_message = "Network Timeout: Could not check for updates."
            except Exception as e:
                status_message = f"Network Error: Could not fetch updates. Details: {e}"

        return outdated_info, status_message

    def get_package_size(self, package_name: str) -> tuple[int, str]:
        """
        Calculates the total size of a package's files using importlib.metadata.
        Returns a tuple of (size_in_bytes, formatted_size_string).
        """
        try:
            files = metadata.files(package_name)
            if not files:
                return 0, "0 B"

            total_size = 0
            for file_path in files:
                abs_path = file_path.locate()
                if abs_path.is_file():
                    total_size += abs_path.stat().st_size
            
            if total_size == 0: return 0, "0 B"
            
            # More precise formatting
            if total_size < 1024:
                size_str = f"{total_size} B"
            elif total_size < 1024**2:
                size_str = f"{total_size / 1024:.1f} KB"
            elif total_size < 1024**2 * 999: # Show MB up to 999
                 size_str = f"{total_size / (1024**2):.2f} MB"
            else: # Show GB for larger sizes
                size_str = f"{total_size / (1024**3):.2f} GB"
            
            return total_size, size_str
            
        except metadata.PackageNotFoundError:
            return 0, "Not Found"
        except Exception as e:
            print(f"Error calculating size for {package_name}: {e}")
            return 0, "Error"

    def get_cache_size(self) -> str:
        """
        Retrieves the pip cache size by running 'pip cache info', parsing all
        size lines, and returning a single total.
        Returns a human-readable size string (e.g., "123.4 MB").
        """
        import re
        try:
            process = subprocess.run(
                ['pip', 'cache', 'info'],
                capture_output=True, text=True, check=True, encoding='utf-8'
            )
            
            total_bytes = 0
            # Regex to find a number (int or float) and a unit (MB, kB, B)
            size_pattern = re.compile(r'(\d+\.?\d*)\s*(MB|kB|B)', re.IGNORECASE)

            for line in process.stdout.splitlines():
                if "size:" in line:
                    match = size_pattern.search(line)
                    if match:
                        value_str, unit = match.groups()
                        value = float(value_str)
                        if unit.upper() == 'MB':
                            total_bytes += value * 1024 * 1024
                        elif unit.upper() == 'KB':
                            total_bytes += value * 1024
                        elif unit.upper() == 'B':
                            total_bytes += value
            
            # Format the total bytes back into a human-readable string
            if total_bytes < 1024:
                return f"{total_bytes:.0f} B"
            elif total_bytes < 1024**2:
                return f"{total_bytes / 1024:.2f} KB"
            elif total_bytes < 1024**3:
                return f"{total_bytes / (1024**2):.2f} MB"
            else:
                return f"{total_bytes / (1024**3):.2f} GB"

        except (subprocess.CalledProcessError, FileNotFoundError, ImportError):
            return "" # Return empty string on error

    def get_package_details(self, package_name: str) -> dict | None:
        """
        Runs `pip show` for a given package and parses the output into a dictionary.
        Returns None if the command fails or the package is not found.
        """
        try:
            # The command we want to run
            cmd = ['pip', 'show', package_name]
            process = subprocess.run(
                cmd, capture_output=True, text=True, check=True, encoding='utf-8'
            )
            
            details = {}
            # The output is like "Key: Value", so we split each line
            for line in process.stdout.strip().split('\n'):
                if ': ' in line:
                    key, value = line.split(': ', 1)
                    # We store the key-value pair, cleaning up whitespace
                    details[key] = value.strip()
            
            return details
            
        except subprocess.CalledProcessError:
            # This happens if `pip show` returns a non-zero exit code (e.g., package not found)
            return None
        except Exception:
            # Catch any other unexpected errors
            return None

    def check_dependencies(self):
        """
        Runs `pip check` and captures its output.
        Returns a tuple: (return_code, output_string).
        """
        try:
            # `pip check` prints its report to stdout and returns non-zero on issues.
            process = subprocess.run(
                ['pip', 'check'], 
                capture_output=True, 
                text=True, 
                check=False, # Important: Don't raise exception on non-zero exit
                encoding='utf-8'
            )
            # The useful output can be in stdout (for errors) or stderr (for other issues)
            output = process.stdout + process.stderr
            return process.returncode, output.strip()
        except FileNotFoundError:
            return -1, "FATAL ERROR: 'pip' command not found."
        except Exception as e:
            return -1, f"An unexpected error occurred: {e}"