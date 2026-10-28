"""Where the installed programs live.

The development installer puts fwmanager under /usr/local. The .deb build rewrites PREFIX to
"/usr" and LOCALEDIR (and checks that it did), so every path below follows.
"""

PREFIX = "/usr/local"
LIBEXEC = f"{PREFIX}/libexec/fwmanager"
SETUP_HELPER = f"{LIBEXEC}/fwmanager-setup"
UNINSTALLER = f"{LIBEXEC}/uninstall"
UNINSTALL_COMMAND = f"sudo {UNINSTALLER}"  # shown when the password window cannot be used
# The .deb keeps its translations in its own folder: minimal Ubuntu images (servers, cloud,
# containers) tell dpkg to skip every file under /usr/share/locale.
LOCALEDIR = "/usr/share/locale"
