# actions/open_app.py
# MARK XXV — Cross-Platform App Launcher (GNOME/Wayland/Flatpak aware)

import os
import time
import subprocess
import platform
import shutil
from pathlib import Path

try:
    import psutil
    _PSUTIL = True
except ImportError:
    _PSUTIL = False

# ── Linux aliases map user-friendly names to the actual binary / desktop-file
#    name on this GNOME/Fedora system.  Flatpak apps that have no native binary
#    are handled by the _FLATPAK_IDS map further below.
_APP_ALIASES = {
    "whatsapp":           {"Windows": "WhatsApp",               "Darwin": "WhatsApp",            "Linux": "whatsapp-for-linux"},
    "chrome":             {"Windows": "chrome",                 "Darwin": "Google Chrome",       "Linux": "google-chrome-stable"},
    "google chrome":      {"Windows": "chrome",                 "Darwin": "Google Chrome",       "Linux": "google-chrome-stable"},
    "firefox":            {"Windows": "firefox",                "Darwin": "Firefox",             "Linux": "firefox"},
    "spotify":            {"Windows": "Spotify",                "Darwin": "Spotify",             "Linux": "com.spotify.Client"},
    "vscode":             {"Windows": "code",                   "Darwin": "Visual Studio Code",  "Linux": "code"},
    "visual studio code": {"Windows": "code",                   "Darwin": "Visual Studio Code",  "Linux": "code"},
    "vs code":            {"Windows": "code",                   "Darwin": "Visual Studio Code",  "Linux": "code"},
    "discord":            {"Windows": "Discord",                "Darwin": "Discord",             "Linux": "discord"},
    "telegram":           {"Windows": "Telegram",               "Darwin": "Telegram",            "Linux": "org.telegram.desktop"},
    "instagram":          {"Windows": "Instagram",              "Darwin": "Instagram",           "Linux": "instagram"},
    "tiktok":             {"Windows": "TikTok",                 "Darwin": "TikTok",              "Linux": "tiktok"},
    "notepad":            {"Windows": "notepad.exe",            "Darwin": "TextEdit",            "Linux": "gnome-text-editor"},
    "text editor":        {"Windows": "notepad.exe",            "Darwin": "TextEdit",            "Linux": "gnome-text-editor"},
    "editor":             {"Windows": "notepad.exe",            "Darwin": "TextEdit",            "Linux": "gnome-text-editor"},
    "calculator":         {"Windows": "calc.exe",               "Darwin": "Calculator",          "Linux": "gnome-calculator"},
    "terminal":           {"Windows": "cmd.exe",                "Darwin": "Terminal",            "Linux": "ptyxis"},
    "cmd":                {"Windows": "cmd.exe",                "Darwin": "Terminal",            "Linux": "ptyxis"},
    "konsole":            {"Windows": "cmd.exe",                "Darwin": "Terminal",            "Linux": "konsole"},
    "explorer":           {"Windows": "explorer.exe",           "Darwin": "Finder",              "Linux": "nautilus"},
    "file explorer":      {"Windows": "explorer.exe",           "Darwin": "Finder",              "Linux": "nautilus"},
    "files":              {"Windows": "explorer.exe",           "Darwin": "Finder",              "Linux": "nautilus"},
    "file manager":       {"Windows": "explorer.exe",           "Darwin": "Finder",              "Linux": "nautilus"},
    "nautilus":           {"Windows": "explorer.exe",           "Darwin": "Finder",              "Linux": "nautilus"},
    "dolphin":            {"Windows": "explorer.exe",           "Darwin": "Finder",              "Linux": "dolphin"},
    "paint":              {"Windows": "mspaint.exe",            "Darwin": "Preview",             "Linux": "org.gnome.Drawing"},
    "word":               {"Windows": "winword",                "Darwin": "Microsoft Word",      "Linux": "libreoffice --writer"},
    "excel":              {"Windows": "excel",                  "Darwin": "Microsoft Excel",     "Linux": "libreoffice --calc"},
    "powerpoint":         {"Windows": "powerpnt",               "Darwin": "Microsoft PowerPoint","Linux": "libreoffice --impress"},
    "libreoffice":        {"Windows": "libreoffice",            "Darwin": "LibreOffice",         "Linux": "libreoffice"},
    "vlc":                {"Windows": "vlc",                    "Darwin": "VLC",                 "Linux": "vlc"},
    "mpv":                {"Windows": "mpv",                    "Darwin": "mpv",                 "Linux": "mpv"},
    "zoom":               {"Windows": "Zoom",                   "Darwin": "zoom.us",             "Linux": "zoom"},
    "slack":              {"Windows": "Slack",                  "Darwin": "Slack",               "Linux": "slack"},
    "steam":              {"Windows": "steam",                  "Darwin": "Steam",               "Linux": "steam"},
    "task manager":       {"Windows": "taskmgr.exe",            "Darwin": "Activity Monitor",    "Linux": "gnome-system-monitor"},
    "system monitor":     {"Windows": "taskmgr.exe",            "Darwin": "Activity Monitor",    "Linux": "gnome-system-monitor"},
    "settings":           {"Windows": "ms-settings:",           "Darwin": "System Preferences",  "Linux": "gnome-control-center"},
    "system settings":    {"Windows": "ms-settings:",           "Darwin": "System Preferences",  "Linux": "gnome-control-center"},
    "powershell":         {"Windows": "powershell.exe",         "Darwin": "Terminal",            "Linux": "ptyxis"},
    "edge":               {"Windows": "msedge",                 "Darwin": "Microsoft Edge",      "Linux": "microsoft-edge"},
    "brave":              {"Windows": "brave",                  "Darwin": "Brave Browser",       "Linux": "brave-browser"},
    "brave browser":      {"Windows": "brave",                  "Darwin": "Brave Browser",       "Linux": "brave-browser"},
    "obsidian":           {"Windows": "Obsidian",               "Darwin": "Obsidian",            "Linux": "obsidian"},
    "notion":             {"Windows": "Notion",                 "Darwin": "Notion",              "Linux": "notion-app"},
    "blender":            {"Windows": "blender",                "Darwin": "Blender",             "Linux": "blender"},
    "capcut":             {"Windows": "CapCut",                 "Darwin": "CapCut",              "Linux": "capcut"},
    "postman":            {"Windows": "Postman",                "Darwin": "Postman",             "Linux": "postman"},
    "figma":              {"Windows": "Figma",                  "Darwin": "Figma",               "Linux": "figma-linux"},
    "image viewer":       {"Windows": "mspaint.exe",            "Darwin": "Preview",             "Linux": "loupe"},
    "photos":             {"Windows": "mspaint.exe",            "Darwin": "Preview",             "Linux": "loupe"},
    "android studio":     {"Windows": "studio64",               "Darwin": "Android Studio",      "Linux": "android-studio"},
}

# ── Known Flatpak application IDs (for apps that don't have a native binary)
_FLATPAK_IDS = {
    "spotify":            "com.spotify.Client",
    "com.spotify.client":  "com.spotify.Client",
    "telegram":           "org.telegram.desktop",
    "org.telegram.desktop":"org.telegram.desktop",
    "extension manager":  "com.mattjakeman.ExtensionManager",
    "trivia":             "io.github.nokse22.trivia-quiz",
}


def _normalize(raw: str) -> str:
    system = platform.system()
    key    = raw.lower().strip()
    if key in _APP_ALIASES:
        return _APP_ALIASES[key].get(system, raw)
    for alias_key, os_map in _APP_ALIASES.items():
        if alias_key in key or key in alias_key:
            return os_map.get(system, raw)
    return raw


def _is_running(app_name: str) -> bool:
    if not _PSUTIL:
        return True
    app_lower = app_name.lower().replace(" ", "").replace(".exe", "")
    try:
        for proc in psutil.process_iter(["name"]):
            try:
                proc_name = proc.info["name"].lower().replace(" ", "").replace(".exe", "")
                if app_lower in proc_name or proc_name in app_lower:
                    return True
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                continue
    except Exception:
        pass
    return False


def _launch_windows(app_name: str) -> bool:
    try:
        import pyautogui
        pyautogui.PAUSE = 0.1
        pyautogui.press("win")
        time.sleep(0.6)
        pyautogui.write(app_name, interval=0.05)
        time.sleep(0.8)
        pyautogui.press("enter")
        time.sleep(3.0)
        return True
    except Exception as e:
        print(f"[open_app] ⚠️ Windows launch failed: {e}")
        return False

def _launch_macos(app_name: str) -> bool:
    try:
        result = subprocess.run(["open", "-a", app_name], capture_output=True, timeout=8)
        if result.returncode == 0:
            time.sleep(1.0)
            return True
    except Exception:
        pass

    try:
        result = subprocess.run(["open", "-a", f"{app_name}.app"], capture_output=True, timeout=8)
        if result.returncode == 0:
            time.sleep(1.0)
            return True
    except Exception:
        pass

    try:
        import pyautogui
        pyautogui.hotkey("command", "space")
        time.sleep(0.6)
        pyautogui.write(app_name, interval=0.05)
        time.sleep(0.8)
        pyautogui.press("enter")
        time.sleep(1.5)
        return True
    except Exception as e:
        print(f"[open_app] ⚠️ macOS Spotlight failed: {e}")
        return False


# ── All XDG desktop-entry directories to scan for .desktop files
def _desktop_dirs():
    dirs = []
    data_home = os.environ.get("XDG_DATA_HOME", os.path.expanduser("~/.local/share"))
    dirs.append(Path(data_home) / "applications")
    for d in os.environ.get("XDG_DATA_DIRS", "/usr/local/share:/usr/share").split(":"):
        dirs.append(Path(d) / "applications")
    # Flatpak exports
    dirs.append(Path("/var/lib/flatpak/exports/share/applications"))
    dirs.append(Path(data_home).parent / "flatpak" / "exports" / "share" / "applications")
    return dirs


def _find_desktop_file(app_name: str):
    """Find the .desktop file for an app by trying exact match then fuzzy."""
    key = app_name.lower().replace(" ", "-").replace("_", "-")
    candidates = [
        f"{key}.desktop",
        f"{app_name}.desktop",
        f"org.gnome.{key.title()}.desktop",
    ]

    for d in _desktop_dirs():
        if not d.is_dir():
            continue
        # Exact match first
        for cand in candidates:
            p = d / cand
            if p.exists():
                return p
        # Fuzzy match: scan files in directory
        try:
            for f in d.iterdir():
                if not f.name.endswith(".desktop"):
                    continue
                fname_lower = f.name.lower()
                if key in fname_lower:
                    return f
        except PermissionError:
            continue
    return None


def _launch_linux(app_name: str) -> bool:
    """Multi-stage Linux app launcher:
    1. Direct binary via shutil.which
    2. gtk-launch with .desktop file discovery
    3. flatpak run for known flatpak IDs
    4. gio launch as last resort
    """
    name_lower = app_name.lower().strip()

    # ── Stage 1: Is it a flatpak app ID? (e.g. "com.spotify.Client")
    if "." in app_name and app_name.count(".") >= 2:
        try:
            result = subprocess.run(
                ["flatpak", "run", app_name],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                start_new_session=True, timeout=3,
            )
            # flatpak run returns immediately for GUI apps (they fork)
            time.sleep(1.0)
            return True
        except subprocess.TimeoutExpired:
            # App launched but didn't exit — that's fine for GUI apps
            return True
        except Exception:
            pass

    # ── Stage 2: Direct binary on PATH
    # Handle multi-word commands like "libreoffice --writer"
    parts = app_name.split()
    binary_name = parts[0]
    extra_args = parts[1:] if len(parts) > 1 else []

    binary = (
        shutil.which(binary_name) or
        shutil.which(binary_name.lower()) or
        shutil.which(binary_name.lower().replace(" ", "-"))
    )
    if binary:
        try:
            subprocess.Popen(
                [binary] + extra_args,
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                start_new_session=True,
            )
            time.sleep(1.0)
            print(f"[open_app] ✅ Launched via binary: {binary} {' '.join(extra_args)}")
            return True
        except Exception as e:
            print(f"[open_app] ⚠️ Binary launch failed for {binary}: {e}")

    # ── Stage 3: Find and use .desktop file via gtk-launch
    desktop_file = _find_desktop_file(app_name)
    if desktop_file:
        desktop_basename = desktop_file.name.replace(".desktop", "")
        try:
            result = subprocess.run(
                ["gtk-launch", desktop_basename],
                capture_output=True, timeout=5,
            )
            if result.returncode == 0:
                time.sleep(1.0)
                print(f"[open_app] ✅ Launched via gtk-launch: {desktop_basename}")
                return True
        except subprocess.TimeoutExpired:
            return True
        except Exception as e:
            print(f"[open_app] ⚠️ gtk-launch failed for {desktop_basename}: {e}")

        # Fallback: gio launch with the full path
        try:
            result = subprocess.run(
                ["gio", "launch", str(desktop_file)],
                capture_output=True, timeout=5,
            )
            if result.returncode == 0:
                time.sleep(1.0)
                print(f"[open_app] ✅ Launched via gio: {desktop_file}")
                return True
        except subprocess.TimeoutExpired:
            return True
        except Exception as e:
            print(f"[open_app] ⚠️ gio launch failed: {e}")

    # ── Stage 4: Try flatpak run from the _FLATPAK_IDS map
    flatpak_id = _FLATPAK_IDS.get(name_lower)
    if not flatpak_id:
        # Also try the raw app_name as a flatpak ID key
        flatpak_id = _FLATPAK_IDS.get(app_name.lower())
    if flatpak_id:
        try:
            subprocess.Popen(
                ["flatpak", "run", flatpak_id],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                start_new_session=True,
            )
            time.sleep(1.5)
            print(f"[open_app] ✅ Launched via flatpak run: {flatpak_id}")
            return True
        except Exception as e:
            print(f"[open_app] ⚠️ flatpak run failed for {flatpak_id}: {e}")

    # ── Stage 5: Last resort — try gtk-launch with various guesses
    guesses = [
        name_lower,
        name_lower.replace(" ", "-"),
        name_lower.replace(" ", ""),
        f"org.gnome.{name_lower.title()}",
    ]
    for guess in guesses:
        try:
            result = subprocess.run(
                ["gtk-launch", guess],
                capture_output=True, timeout=5,
            )
            if result.returncode == 0:
                time.sleep(1.0)
                print(f"[open_app] ✅ Launched via gtk-launch guess: {guess}")
                return True
        except (subprocess.TimeoutExpired, Exception):
            continue

    print(f"[open_app] ❌ All Linux launch methods exhausted for: {app_name}")
    return False


_OS_LAUNCHERS = {
    "Windows": _launch_windows,
    "Darwin":  _launch_macos,
    "Linux":   _launch_linux,
}


def open_app(
    parameters=None,
    response=None,
    player=None,
    session_memory=None,
) -> str:
    app_name = (parameters or {}).get("app_name", "").strip()

    if not app_name:
        return "Please specify which application to open, sir."

    system   = platform.system()
    launcher = _OS_LAUNCHERS.get(system)

    if launcher is None:
        return f"Unsupported OS: {system}"

    normalized = _normalize(app_name)
    print(f"[open_app] 🚀 Launching: {app_name} → {normalized} ({system})")

    if player:
        player.write_log(f"[open_app] {app_name}")

    try:
        success = launcher(normalized)

        if success:
            return f"Opened {app_name} successfully, sir."

        if normalized != app_name:
            success = launcher(app_name)
            if success:
                return f"Opened {app_name} successfully, sir."

        return (
            f"I tried to open {app_name}, sir, but couldn't confirm it launched. "
            f"It may still be loading or might not be installed."
        )

    except Exception as e:
        print(f"[open_app] ❌ {e}")
        return f"Failed to open {app_name}, sir: {e}"