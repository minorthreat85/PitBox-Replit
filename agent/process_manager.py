"""
Process management for acs.exe (Assetto Corsa).
"""
import logging
import shutil
import subprocess
import threading
import time
import ctypes
from pathlib import Path
from typing import Optional, Tuple
import psutil

from agent.config import get_config


logger = logging.getLogger(__name__)


# Win32 API constants
SW_RESTORE = 9
SW_SHOW = 5
HWND_TOP = 0
SWP_SHOWWINDOW = 0x0040
SWP_NOMOVE = 0x0002
SWP_NOSIZE = 0x0001

# Win32 API functions
user32 = ctypes.windll.user32


# Global process state
_process: Optional[psutil.Process] = None
_start_time: Optional[float] = None


def _bring_window_to_foreground(pid: int, max_attempts: int = 25, retry_delay: float = 0.2) -> bool:
    """
    Bring the AC window to foreground using Win32 APIs.
    
    This is critical for sim-lounge/kiosk environments where AC must
    always be the focused window after launch.
    
    Args:
        pid: Process ID of acs.exe
        max_attempts: Maximum number of polling attempts (default: 25 = ~5 seconds)
        retry_delay: Delay between attempts in seconds
    
    Returns:
        True if window was successfully brought to foreground, False otherwise
    """
    logger.info(f"Attempting to bring AC window (PID {pid}) to foreground")
    
    def enum_windows_callback(hwnd, lparam):
        """Callback to find window by PID"""
        window_pid = ctypes.c_ulong()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(window_pid))
        
        if window_pid.value == pid:
            # Check if this is a visible window with a title
            if user32.IsWindowVisible(hwnd):
                length = user32.GetWindowTextLengthW(hwnd)
                if length > 0:
                    # Store the hwnd in lparam (we'll use a list to pass by reference)
                    ctypes.cast(lparam, ctypes.POINTER(ctypes.c_void_p)).contents.value = hwnd
                    return False  # Stop enumeration
        return True  # Continue enumeration
    
    # Define callback type
    EnumWindowsProc = ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_void_p, ctypes.c_void_p)
    enum_callback = EnumWindowsProc(enum_windows_callback)
    
    for attempt in range(max_attempts):
        # Find window by PID
        hwnd_holder = ctypes.c_void_p()
        user32.EnumWindows(enum_callback, ctypes.byref(hwnd_holder))
        hwnd = hwnd_holder.value
        
        if hwnd:
            try:
                # Get current foreground window
                current_fg = user32.GetForegroundWindow()
                
                # Get our thread ID and foreground thread ID
                our_thread = ctypes.windll.kernel32.GetCurrentThreadId()
                fg_thread = user32.GetWindowThreadProcessId(current_fg, None)
                
                # Attach to foreground thread input (allows us to steal focus)
                if fg_thread != our_thread:
                    user32.AttachThreadInput(fg_thread, our_thread, True)
                
                # Restore window if minimized
                user32.ShowWindow(hwnd, SW_RESTORE)
                time.sleep(0.05)  # Brief pause for window state change
                
                # Bring to top
                user32.BringWindowToTop(hwnd)
                
                # Set window position to top-most temporarily, then remove top-most
                user32.SetWindowPos(
                    hwnd,
                    HWND_TOP,
                    0, 0, 0, 0,
                    SWP_NOMOVE | SWP_NOSIZE | SWP_SHOWWINDOW
                )
                
                # Set as foreground window
                user32.SetForegroundWindow(hwnd)
                
                # Show window (ensure it's visible)
                user32.ShowWindow(hwnd, SW_SHOW)
                
                # Detach thread input
                if fg_thread != our_thread:
                    user32.AttachThreadInput(fg_thread, our_thread, False)
                
                # Verify it worked
                time.sleep(0.1)
                new_fg = user32.GetForegroundWindow()
                
                if new_fg == hwnd:
                    logger.info(f"Successfully brought AC window to foreground (attempt {attempt + 1}/{max_attempts})")
                    return True
                else:
                    logger.debug(f"SetForegroundWindow called but window not in foreground yet (attempt {attempt + 1}/{max_attempts})")
            
            except Exception as e:
                logger.warning(f"Error during window focus attempt {attempt + 1}: {e}")
        
        # Wait before retry
        if attempt < max_attempts - 1:
            time.sleep(retry_delay)
    
    logger.warning(f"Failed to bring AC window to foreground after {max_attempts} attempts")
    return False


def get_status() -> Tuple[bool, Optional[int], Optional[float]]:
    """
    Get current process status.
    
    Returns:
        Tuple of (running, pid, uptime_seconds)
    """
    global _process, _start_time
    
    # Check if we have a tracked process
    if _process is None:
        return False, None, None
    
    # Verify process still exists
    try:
        if _process.is_running():
            uptime = time.time() - _start_time if _start_time else None
            return True, _process.pid, uptime
        else:
            # Process died, clear state
            logger.info(f"Process {_process.pid} is no longer running")
            _process = None
            _start_time = None
            return False, None, None
    except (psutil.NoSuchProcess, psutil.AccessDenied):
        # Process gone or inaccessible
        logger.info("Process no longer accessible, clearing state")
        _process = None
        _start_time = None
        return False, None, None


def get_process_status() -> dict:
    """
    Get current process status as a dict for API responses.
    
    Returns:
        Dict with ac_running, pid, uptime_sec (compatible with StatusResponse).
    """
    running, pid, uptime = get_status()
    return {"ac_running": running, "pid": pid, "uptime_sec": uptime}


def _ensure_ini_filename(name: str) -> str:
    """Return name with .ini appended if not already ending with .ini (case-insensitive)."""
    n = (name or "").strip()
    if not n:
        return n
    return n if n.lower().endswith(".ini") else n + ".ini"


def _apply_steering_preset(preset_name: str) -> None:
    """Apply steering preset (copy template to controls.ini). Raises ValueError if not configured or preset not found."""
    from agent.config import get_preset_dir, get_controls_ini_dir
    config = get_config()
    managed = get_preset_dir(config)
    ac_cfg_raw = get_controls_ini_dir(config)
    if not managed or not ac_cfg_raw:
        raise ValueError("Steering presets not configured (set savedsetups and ac_cfg_dir or ac_cfg)")
    src_dir = Path(managed)
    ac_cfg = Path(ac_cfg_raw)
    ini_filename = _ensure_ini_filename(preset_name)
    src_file = src_dir / ini_filename
    if not src_file.is_file():
        try:
            from agent.service.event_emitter import emit
            from agent.common.event_log import LogCategory, LogLevel
            from agent.config import get_config as get_agent_config
            cfg = get_agent_config()
            emit(LogLevel.ERROR, LogCategory.PRESET, f"Steering preset not found: {ini_filename}", rig_id=cfg.agent_id, event_code="PRESET_STEERING_MISSING", details={"path": str(src_file), "preset": preset_name})
        except Exception:
            pass
        raise ValueError(f"Preset not found: {ini_filename} (path: {src_file})")
    ac_cfg.mkdir(parents=True, exist_ok=True)
    dest_file = ac_cfg / "controls.ini"
    if dest_file.exists():
        shutil.copy2(dest_file, dest_file.with_suffix(".ini.bak"))
    shutil.copy2(src_file, dest_file)
    logger.info(f"Applied steering preset: {preset_name}")


def start_process(steering_preset: Optional[str] = None) -> Tuple[bool, Optional[int], str]:
    """
    Start acs.exe process.
    
    Args:
        steering_preset: Optional name of steering preset to apply before launch
    
    Returns:
        Tuple of (success, pid, message)
    """
    global _process, _start_time
    
    config = get_config()
    
    # Check if already running
    running, pid, _ = get_status()
    if running:
        logger.info(f"AC already running with PID {pid}")
        return True, pid, "Already running"
    
    # Apply steering preset before launch if requested
    if steering_preset and (steering_preset := steering_preset.strip()):
        try:
            _apply_steering_preset(steering_preset)
        except ValueError as e:
            logger.warning(f"Steering preset apply failed: {e}")
            return False, None, str(e)
    
    # Verify exe exists
    acs_exe = Path(config.paths.acs_exe)
    if not acs_exe.exists():
        logger.error(f"acs.exe not found: {acs_exe}")
        return False, None, f"acs.exe not found: {acs_exe}"
    
    # Launch process
    try:
        logger.info(f"Starting AC: {acs_exe}")
        
        # Use CREATE_NEW_PROCESS_GROUP on Windows for clean shutdown
        creation_flags = subprocess.CREATE_NEW_PROCESS_GROUP if hasattr(subprocess, 'CREATE_NEW_PROCESS_GROUP') else 0
        
        proc = subprocess.Popen(
            [str(acs_exe)],
            creationflags=creation_flags,
            cwd=acs_exe.parent  # Set working directory to AC install folder
        )
        
        # Wrap in psutil.Process for better control; set immediately so status shows "running" right away
        _process = psutil.Process(proc.pid)
        _start_time = time.time()
        
        logger.info(f"AC started successfully, PID: {proc.pid}")
        
        # Bring AC window to foreground in a daemon thread so we don't block (game may take 10+ s to show window)
        def _focus_later():
            _bring_window_to_foreground(proc.pid, max_attempts=30, retry_delay=0.25)
        _focus_thread = threading.Thread(target=_focus_later, daemon=True, name="ac-bring-to-front")
        _focus_thread.start()
        
        return True, proc.pid, f"Started with PID {proc.pid}"
        
    except Exception as e:
        logger.error(f"Failed to start AC: {e}", exc_info=True)
        return False, None, f"Failed to start: {str(e)}"


def stop_process() -> Tuple[bool, str]:
    """
    Stop acs.exe process gracefully.
    
    Returns:
        Tuple of (success, message)
    """
    global _process, _start_time
    
    # Check if running
    running, pid, _ = get_status()
    if not running:
        logger.info("AC not running, nothing to stop")
        return True, "Not running"
    
    try:
        logger.info(f"Stopping AC (PID {pid})")
        
        # Try graceful termination first
        _process.terminate()
        
        # Wait up to 2 seconds for graceful shutdown, then force kill (reduces perceived exit time)
        try:
            _process.wait(timeout=2)
            logger.info(f"AC stopped gracefully (PID {pid})")
        except psutil.TimeoutExpired:
            # Force kill if graceful shutdown failed
            logger.warning(f"AC did not stop gracefully, forcing kill (PID {pid})")
            _process.kill()
            _process.wait(timeout=2)
            logger.info(f"AC force killed (PID {pid})")
        
        # Clear state
        _process = None
        _start_time = None
        
        return True, "Stopped successfully"
        
    except Exception as e:
        logger.error(f"Failed to stop AC: {e}", exc_info=True)
        # Try to clear state anyway
        _process = None
        _start_time = None
        return False, f"Failed to stop: {str(e)}"
