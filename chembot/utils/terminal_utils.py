import os
import subprocess
import sys


def launch_terminals(folder_path: str):
    """
    Launch all Python scripts in a directory tree, each in its own terminal.

    Purpose
    -------
    - Traverse a folder recursively.
    - Identify all `.py` files.
    - Launch each script in a separate terminal window.
    - Adapt behavior based on the operating system.

    Supported Platforms
    -------------------
    - Windows (cmd)
    - macOS (Terminal via AppleScript)
    - Linux (gnome-terminal)

    Parameters
    ----------
    folder_path : str
        Root directory to scan for Python scripts.

    Raises
    ------
    NotImplementedError
        If the current operating system is unsupported.
    """
    for root, dirs, files in os.walk(folder_path):
        for file in files:
            # Only target Python source files
            if file.endswith(".py"):
                file_path = os.path.join(root, file)

                # Windows platform
                if sys.platform == "win32":
                    subprocess.Popen(
                        ["start", "cmd", "/K", "python", file_path],
                        shell=True
                    )

                # macOS platform
                elif sys.platform == "darwin":
                    subprocess.Popen([
                        "osascript",
                        "-e",
                        'tell application "Terminal" to do script "python3 ' + file_path + '"'
                    ])

                # Linux platform (GNOME terminal)
                elif sys.platform == "linux":
                    subprocess.Popen(
                        ["gnome-terminal", "--", "python3", file_path]
                    )

                # Unsupported OS
                else:
                    raise NotImplementedError(
                        "Unsupported platform: " + sys.platform
                    )