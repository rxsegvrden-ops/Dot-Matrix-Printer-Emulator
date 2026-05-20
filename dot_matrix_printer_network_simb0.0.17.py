# testing the check agent!
import sys
import time
import os
import csv
import io
import socket
import random
import re
import tempfile
import winsound
import msvcrt
import subprocess
import threading
import queue
import wave
import array
from dataclasses import dataclass, field

os.environ.setdefault("PYGAME_HIDE_SUPPORT_PROMPT", "1")

try:
    import pygame
except Exception:
    pygame = None

try:
    from PIL import Image
except Exception:
    Image = None


PRINT_MODES = {
    "DRAFT": {
        "char_delay": 0.045,
        "line_delay": 0.14,
        "dropout_chance": 0.035,
        "faint_chance": 0.055,
        "jitter_chance": 0.025,
        "beep_freq": 1850,
        "beep_ms": 16,
    },
    "NLQ": {
        "char_delay": 0.085,
        "line_delay": 0.22,
        "dropout_chance": 0.012,
        "faint_chance": 0.025,
        "jitter_chance": 0.01,
        "beep_freq": 2250,
        "beep_ms": 12,
    },
}

SOUND_FILES = {
    "print": "dot_matrix_sound.wav",
    "carriage": "dot_matrix_carriage_return.wav",
    "paper": "dot_matrix_paper_feed.wav",
    "error": "dot_matrix_error.wav",
    "grind": "dot_matrix_grind_halt.wav",
}

DASH_WIDTH = 74
PAPER_WIDTH = 72
SETTINGS_FILE_NAME = "dot_matrix_printer_settings.sep"
SPOOL_DIR_NAME = "spool"
EXTRA_DIR_NAME = "extra"
ASSET_LIBRARY_DIR_NAME = "assets"
IBM_DIR_NAME = "ibm_9pin"
PAN_DIR_NAME = "panasonic_24pin"
STATE_WARMUP_DIR_NAME = "warmup"
STATE_STEADY_DIR_NAME = "steady_state"
TOP_OF_FORM_LINES = 6
STANDARD_PAGE_LINES = 66
MAX_SPOOL_FILES = 50
JAM_CHECK_BLOCK_LINES = 100
JAM_CHANCE_PER_BLOCK = 0.05
RIBBON_LIFE_CHARS = 12000
INCOMING_QUEUE_MAX = 256
MAX_INCOMING_JOB_BYTES = 2 * 1024 * 1024

THEME_STYLES = {
    "CLASSIC": {"color": "70", "perf": ".", "alt_perf": ":", "hole": "o"},
    "GREENBAR": {"color": "A0", "perf": "=", "alt_perf": "-", "hole": "o"},
    "AGED": {"color": "E0", "perf": ":", "alt_perf": ".", "hole": "*"},
}


@dataclass
class PrinterState:
    mode_name: str = "DRAFT"
    sound_enabled: bool = True
    sound_profile: str = "LOUD"
    head_profile: str = "IBM9"
    grunge_enabled: bool = True
    speed_scale: float = 0.70
    paused: bool = False
    abort_job: bool = False
    current_line: int = 0
    total_lines: int = 0
    page_count: int = 1
    page_line_count: int = 0
    printed_chars: int = 0
    job_number: int = 0
    byte_count: int = 0
    client_label: str = "IDLE"
    status: str = "ONLINE"
    feed_mode: str = "REALISTIC"
    paper_theme: str = "CLASSIC"
    fit_mode: str = "TRUNCATE"
    queue_depth: int = 0
    queue_preview: str = "empty"
    completed_jobs: int = 0
    spool_count: int = 0
    history_cursor: int = -1
    request_reprint: bool = False
    request_demo_job: bool = False
    request_line_feed: int = 0
    request_form_feed: bool = False
    request_exit: bool = False
    request_exit_pending: bool = False
    stop_all_jobs: bool = False
    hard_exit_worker: bool = False
    stop_signal_deadline: float = 0.0
    paper_stack_used: int = 0
    paper_stack_capacity: int = 36
    last_spool_file: str = "none"
    last_key_message: str = "Ready."
    active_workers: int = 0
    paper_jam_active: bool = False
    jam_reason: str = ""
    jam_trigger_line: int = 0
    command_buffer: str = ""
    command_mode: bool = False
    jam_alert_phase: bool = False
    ribbon_depleted_active: bool = False
    ribbon_health: float = 1.0
    ribbon_chars_used: int = 0
    active_paper_width: int = PAPER_WIDTH

    def mode(self):
        return PRINT_MODES.get(self.mode_name, PRINT_MODES["DRAFT"])

    def char_delay(self):
        if self.head_profile.upper() == "IBM9":
            # Historic IBM 9-pin baseline speeds (jitter is layered separately).
            return 0.005 if self.mode_name == "DRAFT" else 0.020
        if self.head_profile.upper() == "PAN24":
            # Historic Panasonic 24-pin baseline speeds (jitter is layered separately).
            return 0.0052 if self.mode_name == "DRAFT" else 0.0160
        return max(0.003, self.mode()["char_delay"] * self.speed_scale)

    def line_delay(self):
        return max(0.01, self.mode()["line_delay"] * self.speed_scale)


def clear_screen():
    os.system('cls' if os.name == 'nt' else 'clear')

def set_console_theme(state):
    if os.name != "nt":
        return
    style = THEME_STYLES.get(state.paper_theme, THEME_STYLES["CLASSIC"])
    os.system(f"color {style['color']}")



def bar(value, width=24):
    value = max(0, min(width, value))
    return "[" + "#" * value + "." * (width - value) + "]"


def ribbon_condition_label(state):
    pct = int(round(state.ribbon_health * 100))
    if pct >= 80:
        return f"FRESH {pct:>3d}%"
    if pct >= 55:
        return f"USED  {pct:>3d}%"
    if pct >= 30:
        return f"FADED {pct:>3d}%"
    if pct >= 10:
        return f"GHOST {pct:>3d}%"
    return f"END   {pct:>3d}%"


def replace_ribbon(state):
    state.ribbon_health = 1.0
    state.ribbon_chars_used = 0
    state.ribbon_depleted_active = False
    if state.status == "RIBBON_DEPLETED":
        state.status = "ONLINE"
    # Pristine reset: immediately restore crisp/default text output state.
    state.command_mode = False
    state.command_buffer = ""
    state.jam_alert_phase = False
    state.last_key_message = "Ribbon replaced. Ink density + text formatting reset to pristine defaults."


def enforce_clean_ribbon_override(state):
    if state.grunge_enabled:
        return False
    changed = False
    if state.ribbon_health != 1.0:
        changed = True
    if state.ribbon_chars_used != 0:
        changed = True
    if state.ribbon_depleted_active:
        changed = True
    if state.status == "RIBBON_DEPLETED":
        changed = True
    state.ribbon_health = 1.0
    state.ribbon_chars_used = 0
    state.ribbon_depleted_active = False
    if state.status == "RIBBON_DEPLETED":
        state.status = "ONLINE"
    return changed


def apply_ribbon_wear(state):
    if not state.grunge_enabled:
        enforce_clean_ribbon_override(state)
        return
    state.ribbon_chars_used += 1
    # IBM9 NLQ double-strike wears ribbon a bit faster than standard passes.
    if state.head_profile.upper() == "IBM9" and state.mode_name.upper() == "NLQ":
        if (state.printed_chars % 5) == 0:
            state.ribbon_chars_used += 1
    used_ratio = min(1.0, state.ribbon_chars_used / float(RIBBON_LIFE_CHARS))
    state.ribbon_health = max(0.0, 1.0 - used_ratio)


def advance_page_counter(state, lines=1):
    page_breaks = 0
    for _ in range(max(0, lines)):
        state.page_line_count += 1
        if state.page_line_count >= STANDARD_PAGE_LINES:
            state.page_count += 1
            state.page_line_count = 0
            page_breaks += 1
    return page_breaks


def force_form_feed_page_break(state):
    state.page_count += 1
    state.page_line_count = 0
    return advance_paper_stack_after_line(state)


def cycle_head_profile(current):
    order = ["IBM9", "PAN24"]
    if current not in order:
        return order[0]
    return order[(order.index(current) + 1) % len(order)]


def jam_alert_lines(state):
    label = "!!! PAPER JAM / RIBBON SNAG DETECTED !!!" if state.jam_alert_phase else "*** PAPER JAM / RIBBON SNAG DETECTED ***"
    line_info = f"Line {state.jam_trigger_line}" if state.jam_trigger_line else "Line unknown"
    reason = state.jam_reason or "mechanical fault"
    return [
        label,
        f"FAULT: {reason} @ {line_info}",
        "MENU WINDOW ONLY: : --clear-jam + ENTER TO RESUME",
    ]


def garble_ascii_burst(length):
    charset = r"!@#$%^&*()_+-=[]{}|;:,.<>/?~0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ"
    return "".join(random.choice(charset) for _ in range(max(8, length)))


def trigger_paper_jam(state, line_number):
    reasons = ["paper jam", "ribbon snag", "tractor reel bind"]
    state.paper_jam_active = True
    state.jam_reason = random.choice(reasons)
    state.jam_trigger_line = line_number
    state.status = "JAMMED"
    state.last_key_message = "JAMMED. Clear from MENU window: : --clear-jam + Enter."
    state.command_buffer = ""
    state.jam_alert_phase = True


def clear_paper_jam(state):
    state.paper_jam_active = False
    state.jam_reason = ""
    state.jam_trigger_line = 0
    state.status = "ONLINE"
    state.command_buffer = ""
    state.jam_alert_phase = False
    state.last_key_message = "Jam cleared. Tractor feed resumed."


def trigger_ribbon_depleted(state):
    state.ribbon_depleted_active = True
    state.status = "RIBBON_DEPLETED"
    state.command_mode = True
    state.command_buffer = ""
    state.last_key_message = "[!] ERROR: RIBBON DEPLETED. PLEASE REPLACE RIBBON TO RESUME."


def process_typed_command(state, key):
    if key in ("\r", "\n"):
        command = state.command_buffer.strip().lower()
        state.command_buffer = ""
        state.command_mode = False
        if command == "--clear-jam":
            clear_paper_jam(state)
            return True, True
        if command == "--replace-ribbon":
            replace_ribbon(state)
            return True, True
        if command:
            state.last_key_message = f"Unknown command: {command}"
        else:
            state.last_key_message = "Command mode: type --replace-ribbon then press Enter."
        return True, False

    if key == "\b":
        state.command_buffer = state.command_buffer[:-1]
        return True, False

    if key.isprintable():
        state.command_buffer = (state.command_buffer + key)[-64:]
        return True, False
    return False, False


def panel_row(content):
    inner_width = DASH_WIDTH - 2
    clipped = content[:inner_width]
    return f"| {clipped:<{inner_width}} |"


def parse_bool(value, default=False):
    normalized = value.strip().lower()
    if normalized in ("1", "true", "yes", "on"):
        return True
    if normalized in ("0", "false", "no", "off"):
        return False
    return default


def load_settings(state, settings_path):
    if not os.path.exists(settings_path):
        return

    try:
        with open(settings_path, "r", newline="", encoding="utf-8") as handle:
            reader = csv.reader(handle, delimiter=";")
            for row in reader:
                if len(row) < 2:
                    continue

                key = row[0].strip().lower()
                value = row[1].strip()

                if key == "mode_name":
                    candidate = value.upper()
                    if candidate in PRINT_MODES:
                        state.mode_name = candidate
                elif key == "sound_enabled":
                    state.sound_enabled = parse_bool(value, state.sound_enabled)
                elif key == "sound_profile":
                    candidate = value.upper()
                    if candidate in ("LOUD", "QUIET"):
                        state.sound_profile = candidate
                elif key == "head_profile":
                    candidate = value.upper()
                    if candidate in ("IBM9", "PAN24"):
                        state.head_profile = candidate
                elif key == "grunge_enabled":
                    state.grunge_enabled = parse_bool(value, state.grunge_enabled)
                elif key == "speed_scale":
                    try:
                        speed = float(value)
                        state.speed_scale = max(0.25, min(3.00, speed))
                    except ValueError:
                        pass
                elif key == "feed_mode":
                    candidate = value.upper()
                    if candidate in ("FAST", "REALISTIC"):
                        state.feed_mode = candidate
                elif key == "paper_theme":
                    candidate = value.upper()
                    if candidate in THEME_STYLES:
                        state.paper_theme = candidate
                elif key == "fit_mode":
                    candidate = value.upper()
                    if candidate in ("TRUNCATE", "WRAP"):
                        state.fit_mode = candidate
                elif key == "paused":
                    state.paused = parse_bool(value, state.paused)
                elif key == "stop_all_jobs":
                    state.stop_all_jobs = parse_bool(value, state.stop_all_jobs)
                elif key == "hard_exit_worker":
                    state.hard_exit_worker = parse_bool(value, state.hard_exit_worker)
                elif key == "paper_stack_used":
                    try:
                        used = int(value)
                        state.paper_stack_used = max(0, min(state.paper_stack_capacity - 1, used))
                    except ValueError:
                        pass
                elif key == "paper_jam_active":
                    state.paper_jam_active = parse_bool(value, state.paper_jam_active)
                elif key == "ribbon_depleted_active":
                    state.ribbon_depleted_active = parse_bool(value, state.ribbon_depleted_active)
                elif key == "jam_reason":
                    state.jam_reason = value
                elif key == "jam_trigger_line":
                    try:
                        state.jam_trigger_line = max(0, int(value))
                    except ValueError:
                        pass
                elif key == "page_count":
                    try:
                        state.page_count = max(1, int(value))
                    except ValueError:
                        pass
                elif key == "page_line_count":
                    try:
                        state.page_line_count = max(0, min(STANDARD_PAGE_LINES, int(value)))
                    except ValueError:
                        pass
                elif key == "current_line":
                    try:
                        state.current_line = max(0, int(value))
                    except ValueError:
                        pass
                elif key == "total_lines":
                    try:
                        state.total_lines = max(0, int(value))
                    except ValueError:
                        pass
                elif key == "ribbon_health":
                    try:
                        state.ribbon_health = max(0.0, min(1.0, float(value)))
                    except ValueError:
                        pass
                elif key == "ribbon_chars_used":
                    try:
                        state.ribbon_chars_used = max(0, int(value))
                    except ValueError:
                        pass
    except Exception as load_error:
        state.last_key_message = f"Settings load failed: {load_error}"


def save_settings(state, settings_path):
    rows = [
        ("mode_name", state.mode_name),
        ("sound_enabled", str(state.sound_enabled)),
        ("sound_profile", state.sound_profile),
        ("head_profile", state.head_profile),
        ("grunge_enabled", str(state.grunge_enabled)),
        ("speed_scale", f"{state.speed_scale:.2f}"),
        ("feed_mode", state.feed_mode),
        ("paper_theme", state.paper_theme),
        ("fit_mode", state.fit_mode),
        ("paused", str(state.paused)),
        ("stop_all_jobs", str(state.stop_all_jobs)),
        ("hard_exit_worker", str(state.hard_exit_worker)),
        ("paper_stack_used", str(state.paper_stack_used)),
        ("paper_jam_active", str(state.paper_jam_active)),
        ("ribbon_depleted_active", str(state.ribbon_depleted_active)),
        ("jam_reason", state.jam_reason),
        ("jam_trigger_line", str(state.jam_trigger_line)),
        ("page_count", str(state.page_count)),
        ("page_line_count", str(state.page_line_count)),
        ("current_line", str(state.current_line)),
        ("total_lines", str(state.total_lines)),
        ("ribbon_health", f"{state.ribbon_health:.8f}"),
        ("ribbon_chars_used", str(state.ribbon_chars_used)),
    ]

    with open(settings_path, "w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle, delimiter=";")
        for key, value in rows:
            writer.writerow([key, value])

def safe_save_settings(state, settings_path):
    try:
        save_settings(state, settings_path)
        return True
    except Exception as save_error:
        state.last_key_message = f"Settings save failed: {save_error}"
        return False


def ensure_directory(path):
    os.makedirs(path, exist_ok=True)


def sanitize_filename_fragment(value):
    cleaned = "".join(ch if ch.isalnum() or ch in ("-", "_") else "_" for ch in value)
    return cleaned[:48].strip("_") or "unknown"


def update_queue_metrics(state, job_queue, spool_history):
    state.queue_depth = len(job_queue)
    state.spool_count = len(spool_history)
    if job_queue:
        state.queue_preview = ", ".join(f"#{job['job_id']}" for job in job_queue[:3])
    else:
        state.queue_preview = "empty"
    if spool_history:
        state.last_spool_file = os.path.basename(spool_history[-1]["spool_path"])
    else:
        state.last_spool_file = "none"
    if spool_history and state.history_cursor < 0:
        state.history_cursor = len(spool_history) - 1


def spool_job_text(spool_dir, job_id, source_label, text):
    ensure_directory(spool_dir)
    timestamp = time.strftime("%Y%m%d_%H%M%S")
    source_slug = sanitize_filename_fragment(source_label)
    file_name = f"job_{job_id:04d}_{timestamp}_{source_slug}.txt"
    file_path = os.path.join(spool_dir, file_name)
    with open(file_path, "w", encoding="utf-8", errors="ignore") as handle:
        handle.write(text)
    trim_spool_dir(spool_dir, MAX_SPOOL_FILES)
    return file_path


def trim_spool_dir(spool_dir, max_files):
    if max_files <= 0:
        return
    entries = [
        entry
        for entry in os.scandir(spool_dir)
        if entry.is_file() and entry.name.lower().endswith(".txt")
    ]
    if len(entries) <= max_files:
        return
    entries.sort(key=lambda entry: entry.stat().st_mtime)
    for entry in entries[: len(entries) - max_files]:
        try:
            os.remove(entry.path)
        except OSError:
            pass


def create_job_record(job_id, source_label, text, is_csv, spool_dir):
    spool_path = spool_job_text(spool_dir, job_id, source_label, text)
    return {
        "job_id": job_id,
        "source": source_label,
        "bytes": len(text.encode("utf-8", errors="ignore")),
        "text": text,
        "is_csv": is_csv,
        "spool_path": spool_path,
    }


def build_demo_test_page():
    timestamp = time.strftime("%Y-%m-%d %H:%M:%S")
    lines = [
        "DOT MATRIX PRINTER - TEST PAGE",
        "===============================",
        f"Generated: {timestamp}",
        "",
        "ABCDEFGHIJKLMNOPQRSTUVWXYZ",
        "abcdefghijklmnopqrstuvwxyz",
        "0123456789 !\"#$%&'()*+,-./:;<=>?@[\\]^_`{|}~",
        "",
        "Alignment:",
        "....1....2....3....4....5....6....7....8....9....0....",
        "123456789012345678901234567890123456789012345678901234567890",
        "",
        "Density:",
        "@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@",
        "############################################################",
        "MMMMMMMMMMMMMMMMMMMMMMMMMMMMMMMMMMMMMMMMMMMMMMMMMMMMMMMMMMMM",
        "",
        "Feed + perforation check complete.",
        "END OF TEST PAGE",
    ]
    return "\n".join(lines)


def load_spool_history(spool_dir):
    ensure_directory(spool_dir)
    history = []
    for entry in sorted(os.scandir(spool_dir), key=lambda e: e.stat().st_mtime):
        if not entry.is_file() or not entry.name.lower().endswith(".txt"):
            continue
        try:
            with open(entry.path, "r", encoding="utf-8", errors="ignore") as handle:
                text = handle.read()
        except OSError:
            continue

        name = entry.name
        id_match = re.search(r"job_(\d{4})_", name)
        job_id = int(id_match.group(1)) if id_match else (len(history) + 1)
        history.append(
            {
                "job_id": job_id,
                "source": "spool_restore",
                "bytes": len(text.encode("utf-8", errors="ignore")),
                "text": text,
                "is_csv": "," in text,
                "spool_path": entry.path,
            }
        )
    return history


def enqueue_selected_history_job(state, job_queue, spool_history, spool_dir, next_job_id):
    if not spool_history:
        state.last_key_message = "No spool history available for reprint."
        return next_job_id

    if state.history_cursor < 0 or state.history_cursor >= len(spool_history):
        state.history_cursor = len(spool_history) - 1

    source_job = spool_history[state.history_cursor]
    reprint_label = f"reprint_{source_job['job_id']:04d}"
    new_job = create_job_record(
        next_job_id,
        reprint_label,
        source_job["text"],
        source_job["is_csv"],
        spool_dir,
    )
    job_queue.append(new_job)
    spool_history.append(new_job)
    state.last_key_message = f"Reprint queued as job #{next_job_id:04d} from history job #{source_job['job_id']:04d}."
    return next_job_id + 1

def draw_dashboard(state):
    progress = 0
    if state.total_lines:
        progress = int((state.current_line / state.total_lines) * 24)

    sound = "ON " if state.sound_enabled else "OFF"
    sound_profile = state.sound_profile
    grunge = "WORN" if state.grunge_enabled else "CLEAN"
    paused = "PAUSED" if state.paused else state.status

    print("+" + "=" * DASH_WIDTH + "+")
    print(panel_row(f"VIRTUAL DOT-MATRIX PRINTER                             {paused:<8}"))
    print("+" + "-" * DASH_WIDTH + "+")
    print(panel_row(f"JOB #{state.job_number:04d}  FROM {state.client_label:<26} BYTES {state.byte_count:<9}"))
    print(panel_row(f"MODE {state.mode_name:<6}  SPEED {state.speed_scale:>4.2f}x  FEED {state.feed_mode:<9}  JOB LINE {state.current_line:>3}/{state.total_lines:<3}"))
    print(panel_row(f"PAGE {state.page_count:>3}  LINE {state.page_line_count:>2}/{STANDARD_PAGE_LINES:<2}  STACK {state.paper_stack_used:>2}/{state.paper_stack_capacity:<2}"))
    print(panel_row(f"PAPER {bar(progress)}  CHARS PRINTED {state.printed_chars:<10} SOUND {sound:<3}/{sound_profile:<5} RIBBON {grunge:<5}"))
    print(panel_row(f"RIBBON LIFE {ribbon_condition_label(state):<12} CHARS {state.ribbon_chars_used:<7} MAINT: --replace-ribbon"))
    print(panel_row(f"HEAD PROFILE {state.head_profile:<6} (IBM9 metal click / PAN24 high whirr)"))
    print(panel_row(f"THEME {state.paper_theme:<10} FIT {state.fit_mode:<10}"))
    print(panel_row(f"QUEUE {state.queue_depth:<3} NEXT {state.queue_preview:<18} DONE {state.completed_jobs:<4} LAUN {state.active_workers:<3}"))
    print(panel_row(f"LAST SPOOL FILE: {state.last_spool_file}"))
    if state.paper_jam_active:
        print("+" + "-" * DASH_WIDTH + "+")
        for alert_line in jam_alert_lines(state):
            print(panel_row(alert_line))
        typed = state.command_buffer if state.command_buffer else "(waiting for --clear-jam)"
        print(panel_row(f"CMD> {typed}"))
    if state.ribbon_depleted_active:
        print("+" + "-" * DASH_WIDTH + "+")
        print(panel_row("[!] ERROR: RIBBON DEPLETED. PLEASE REPLACE RIBBON TO RESUME."))
        print(panel_row("TYPE --replace-ribbon THEN PRESS ENTER TO RESUME"))
        typed = state.command_buffer if state.command_buffer else "(waiting for --replace-ribbon)"
        print(panel_row(f"CMD> {typed}"))
    print("+" + "-" * DASH_WIDTH + "+")
    print(panel_row("MAIN CONTROLS"))
    print(panel_row(f" [M] Print Mode   -> {state.mode_name:<10} [+/-] Speed Delay -> {state.speed_scale:>4.2f}x"))
    print(panel_row(f" [S] Sound        -> {sound:<10} [K] Sound Profile -> {sound_profile:<7}"))
    print(panel_row(f" [V] Head Voice   -> {state.head_profile:<10}"))
    print(panel_row(f" [G] Ribbon Wear  -> {grunge:<10}"))
    print(panel_row(f" [F] Feed Style   -> {state.feed_mode:<10} [Y] Paper Theme -> {state.paper_theme:<10}"))
    print(panel_row(f" [A] Auto-Fit     -> {state.fit_mode:<10} [P] Pause/Resume -> {'YES' if state.paused else 'NO':<10}"))
    print(panel_row(f" [L] Line Feed    -> 1 line     [T] Top-Of-Form -> {TOP_OF_FORM_LINES} lines"))
    print(panel_row(" [X] Queue classic demo test page"))
    selected_history = f"{state.history_cursor + 1}" if state.history_cursor >= 0 else "none"
    print(panel_row(f" [H/J] History Sel-> {selected_history:<10} [R] Reprint Selected"))
    print(panel_row(" [W] Write settings file now"))
    print(panel_row(" [:] Command mode (type --replace-ribbon + Enter)"))
    typed = state.command_buffer if state.command_mode and state.command_buffer else "(type --replace-ribbon)"
    print(panel_row(f" CMD> {typed}"))
    print(panel_row(" [I] Load saved config now   [U] Refresh main menu from settings file"))
    print(panel_row(" [Q] Stop print jobs, then close main menu"))
    print(panel_row("Settings auto-saves on changes. Press W to force-save now."))
    print(panel_row("Settings file: extra/dot_matrix_printer_settings.sep"))
    print("+" + "-" * DASH_WIDTH + "+")
    print(panel_row("SHORTCUTS: M +/- S K V G F Y A L T X P H J R W : I U Q"))
    if state.paper_jam_active:
        print(panel_row("JAM CLEAR COMMAND: --clear-jam + Enter"))
    if state.ribbon_depleted_active:
        print(panel_row("RIBBON COMMAND: --replace-ribbon + Enter"))
    print(panel_row(f"STATUS: {state.last_key_message}"))
    print("+" + "=" * DASH_WIDTH + "+")
    print()

def redraw_print_view(state, show_dashboard=True):
    set_console_theme(state)
    clear_screen()
    if show_dashboard:
        draw_dashboard(state)


def theme_style(state):
    return THEME_STYLES.get(state.paper_theme, THEME_STYLES["CLASSIC"])


def perforation_char(state, line_number):
    style = theme_style(state)
    return style["perf"] if line_number % 2 else style["alt_perf"]


def paper_hole(state, line_number):
    hole = theme_style(state)["hole"]
    return hole if line_number % 2 else " "


def print_tractor_paper_header(state, width=None):
    if width is None:
        width = state.active_paper_width
    perf = perforation_char(state, 1)
    print("    ." + "-" * (width + 4) + ".")
    print("    | " + perf * width + " |")


def print_tractor_paper_line(state, line_number, content, width=None):
    if width is None:
        width = state.active_paper_width
    hole = paper_hole(state, line_number)
    visible = content[:width]
    padded = f"{visible:<{width}}"
    print(f" {hole}  | {padded} |  {hole}")
    if line_number % 3 == 0:
        print("    | " + perforation_char(state, line_number) * width + " |")


def worker_paper_console_width():
    header = "    ." + "-" * (PAPER_WIDTH + 4) + "."
    body = f" o  | {' ' * PAPER_WIDTH} |  o"
    return max(len(header), len(body))


def set_worker_console_size():
    if os.name != "nt":
        return
    os.system("mode con: cols=84 lines=71")


def sleep_mechanical_impact(state):
    base = state.char_delay()
    if state.head_profile.upper() == "PAN24":
        line_idx = max(1, int(getattr(state, "current_line", 1)))
        warm_end = 11
        if line_idx <= warm_end:
            # Keep original startup warmup physics before line 12 only.
            phase_line = line_idx
            taper = 1.0
            if phase_line >= 6:
                taper = max(0.0, (warm_end - phase_line) / max(1.0, float(warm_end - 6)))

            stutter = 0.0
            if phase_line < 9:
                speed_factor = round(random.uniform(0.92, 0.99), 2)
                if random.random() < (0.06 * taper):
                    stutter += random.uniform(0.28, 0.55) * taper
                if random.random() < (0.04 * taper):
                    stutter += random.uniform(0.003, 0.010) * max(0.35, taper)
            else:
                ramp = min(1.0, max(0.0, (phase_line - 9) / 2.0))
                speed_min = 0.88 + (0.10 * ramp)
                speed_max = 0.98 + (0.05 * ramp)
                speed_factor = round(random.uniform(speed_min, speed_max), 2)
                skip_chance = 0.16 * (1.0 - ramp) * taper
                hang_chance = 0.14 * (1.0 - ramp) * taper
                if random.random() < hang_chance:
                    stutter += 0.10 + random.uniform(0.08, 0.45) * (1.0 - ramp) * taper
                if random.random() < skip_chance:
                    stutter += 0.004 + random.uniform(0.004, 0.022) * (1.0 - ramp) * taper

            cadence = base / max(0.80, speed_factor)
            time.sleep(max(0.0018, cadence + stutter))
        else:
            # Steady grid after warmup: fixed PAN24 cadence.
            time.sleep(max(0.0018, base))
        return

    variance = 0.16 if state.mode_name == "DRAFT" else 0.10
    jitter = random.uniform(-base * variance, base * variance)
    stutter = 0.0
    if random.random() < (0.03 if state.mode_name == "DRAFT" else 0.015):
        stutter = random.uniform(base * 0.35, base * 0.95)
    micro_jitter = random.uniform(-0.0008, 0.0012)
    time.sleep(max(0.0018, base + jitter + stutter + micro_jitter))


def line_settle_delay(state):
    base = state.line_delay()
    variance = 0.14 if state.feed_mode == "REALISTIC" else 0.08
    jitter = random.uniform(-base * variance, base * variance)
    settle = 0.0
    if state.feed_mode == "REALISTIC" and random.random() < 0.22:
        settle = random.uniform(base * 0.06, base * 0.30)
    return max(0.006, base + jitter + settle)


def animate_paper_feed(line_number, feed_delay, state):
    # Two feed styles: FAST (snappy) and REALISTIC (heavier platen motion with slight cadence drift).
    if state.feed_mode == "FAST":
        feed_steps = 4
        base_delay = max(0.012, min(0.05, feed_delay / max(1, feed_steps)))
    else:
        feed_steps = 12
        base_delay = max(0.02, min(0.085, feed_delay / max(1, feed_steps - 2)))

    # Keep motion timing without emitting full blank lines.
    # This avoids one visible blank paper line per printed newline.
    for step in range(feed_steps):
        cadence = 1.0 + (0.06 if step in (0, feed_steps - 1) else -0.03)
        jitter = random.uniform(-0.004, 0.004) if state.feed_mode == "REALISTIC" else random.uniform(-0.002, 0.002)
        time.sleep(max(0.004, base_delay * cadence + jitter))
        if state.feed_mode == "REALISTIC" and step == (feed_steps // 2) and random.random() < 0.16:
            time.sleep(random.uniform(0.012, 0.04))


def cycle_theme(current_theme):
    names = list(THEME_STYLES.keys())
    if current_theme not in names:
        return names[0]
    idx = (names.index(current_theme) + 1) % len(names)
    return names[idx]


def feed_paper_stack(state, feed_lines, sound_engine=None):
    if feed_lines <= 0:
        return False

    if state.page_line_count == 0:
        print_tractor_paper_header(state)

    base_line = state.page_line_count
    for offset in range(feed_lines):
        physical_line_number = ((base_line + offset) % STANDARD_PAGE_LINES) + 1
        hole = paper_hole(state, physical_line_number)
        print(f" {hole}  | {' ' * PAPER_WIDTH} |  {hole}")
        if sound_engine:
            sound_engine.play_once("paper", sync=True)
        time.sleep(max(0.04, min(0.16, state.line_delay() * 0.8)))

    return False


def apply_pending_manual_feed(state, sound_engine=None):
    # Visible line feed / top-of-form simulation while idle.
    reset_triggered = False
    if state.request_form_feed:
        reset_triggered = feed_paper_stack(state, TOP_OF_FORM_LINES, sound_engine=sound_engine) or reset_triggered
        state.request_form_feed = False
        reset_triggered = force_form_feed_page_break(state) or reset_triggered
        state.last_key_message = "Top-of-form feed completed."

    if state.request_line_feed > 0:
        reset_triggered = feed_paper_stack(state, state.request_line_feed, sound_engine=sound_engine) or reset_triggered
        page_breaks = advance_page_counter(state, state.request_line_feed)
        for _ in range(page_breaks):
            reset_triggered = advance_paper_stack_after_line(state) or reset_triggered
        state.last_key_message = f"Manual line feed: {state.request_line_feed} line(s)."
        state.request_line_feed = 0
    return reset_triggered


def apply_live_settings_from_file(state, settings_path, last_mtime, sync_theme=True):
    try:
        stat_info = os.stat(settings_path)
        current_mtime = getattr(stat_info, "st_mtime_ns", int(stat_info.st_mtime * 1_000_000_000))
    except OSError:
        return last_mtime
    if last_mtime is not None and current_mtime <= last_mtime:
        return last_mtime

    loaded = PrinterState()
    loaded.__dict__.update(state.__dict__)
    load_settings(loaded, settings_path)
    previous_theme = state.paper_theme
    state.mode_name = loaded.mode_name
    state.sound_enabled = loaded.sound_enabled
    state.sound_profile = loaded.sound_profile
    state.head_profile = loaded.head_profile
    state.grunge_enabled = loaded.grunge_enabled
    state.speed_scale = loaded.speed_scale
    state.feed_mode = loaded.feed_mode
    if sync_theme:
        state.paper_theme = loaded.paper_theme
    state.fit_mode = loaded.fit_mode
    state.paused = loaded.paused
    state.stop_all_jobs = loaded.stop_all_jobs
    state.hard_exit_worker = loaded.hard_exit_worker
    state.paper_stack_used = loaded.paper_stack_used
    state.paper_jam_active = loaded.paper_jam_active
    state.ribbon_depleted_active = loaded.ribbon_depleted_active
    state.jam_reason = loaded.jam_reason
    state.jam_trigger_line = loaded.jam_trigger_line
    state.page_count = loaded.page_count
    state.page_line_count = loaded.page_line_count
    state.ribbon_health = loaded.ribbon_health
    state.ribbon_chars_used = loaded.ribbon_chars_used
    enforce_clean_ribbon_override(state)
    if state.paper_jam_active:
        state.status = "JAMMED"
    elif state.ribbon_depleted_active:
        state.status = "RIBBON_DEPLETED"
    if sync_theme and state.paper_theme != previous_theme:
        set_console_theme(state)
    return current_mtime


def wait_if_paused_centralized(state, settings_path, last_mtime, sound_engine=None):
    while state.paused and not state.abort_job:
        if sound_engine:
            drain_keyboard(state, sound_engine, settings_path=settings_path, menu_window=False)
        time.sleep(0.08)
        last_mtime = apply_live_settings_from_file(state, settings_path, last_mtime)
    return last_mtime


def advance_paper_stack_after_line(state):
    if state.page_line_count != 0:
        return False
    state.paper_stack_used += 1
    if state.paper_stack_used >= state.paper_stack_capacity:
        state.paper_stack_used = 0
        return True
    return False


def wait_if_jammed(state, settings_path, last_mtime, sound_engine=None):
    grinding_engine = PrinterSoundEngine(SOUND_FILES, state, base_dir=os.path.dirname(settings_path) if settings_path else ".")
    while state.paper_jam_active and not state.abort_job:
        if sound_engine:
            sound_engine.stop()
        grinding_engine.play_loop("grind")
        if sound_engine:
            drain_keyboard(state, sound_engine, settings_path=settings_path, menu_window=False)
        time.sleep(0.08)
        if settings_path:
            last_mtime = apply_live_settings_from_file(state, settings_path, last_mtime)
    grinding_engine.stop()
    if settings_path and not state.paper_jam_active:
        safe_save_settings(state, settings_path)
    return last_mtime


def wait_if_ribbon_depleted(state, settings_path, last_mtime, sound_engine=None):
    while state.ribbon_depleted_active and not state.abort_job:
        if enforce_clean_ribbon_override(state):
            break
        if sound_engine:
            sound_engine.stop()
            drain_keyboard(state, sound_engine, settings_path=settings_path, menu_window=False)
        time.sleep(0.08)
        if settings_path:
            last_mtime = apply_live_settings_from_file(state, settings_path, last_mtime)
    if settings_path and not state.ribbon_depleted_active:
        safe_save_settings(state, settings_path)
    return last_mtime


class PygameChannelRouter:
    def __init__(self):
        self.loop_channels = []
        self.strike_channels = []
        self._strike_cursor = 0

    def configure(self):
        self.loop_channels = [pygame.mixer.Channel(0), pygame.mixer.Channel(1)]
        self.strike_channels = [pygame.mixer.Channel(i) for i in range(2, 8)]

    def play_strike(self, snd, volume):
        if not self.strike_channels:
            return False
        total = len(self.strike_channels)
        idx = self._strike_cursor % total
        chosen = self.strike_channels[idx]
        self._strike_cursor = (idx + 1) % total
        for ch in self.strike_channels:
            try:
                if ch.get_busy():
                    ch.stop()
            except Exception:
                pass
        try:
            chosen.stop()
        except Exception:
            pass
        chosen.set_volume(max(0.0, min(1.0, volume)))
        chosen.play(snd)
        return True

    def stop_all(self):
        for ch in self.loop_channels + self.strike_channels:
            try:
                ch.stop()
            except Exception:
                pass

class PrinterSoundEngine:
    def __init__(self, sound_files, state, base_dir="."):
        self.state = state
        self.base_dir = base_dir
        self.sound_files = sound_files
        self._asset_library_dir = os.path.join(self.base_dir, ASSET_LIBRARY_DIR_NAME)
        self._linefeed_cache_dir = os.path.join(self._asset_library_dir, IBM_DIR_NAME, STATE_STEADY_DIR_NAME)
        self._print_cache_dir = os.path.join(self._asset_library_dir, PAN_DIR_NAME, STATE_STEADY_DIR_NAME)
        self._linefeed_variant_cache = {}
        self._print_trim_cache = {}
        self._print_speed_cache = {}
        self._pygame_sound_cache = {}
        self._asset_registry = {
            "ibm_9pin": {"warmup": {}, "steady_state": {}},
            "panasonic_24pin": {"warmup": {}, "steady_state": {}},
        }
        self._pygame_ready = False
        self._pygame_failed = False
        self._pygame_channel = None
        self._channel_router = PygameChannelRouter()
        self._last_print_tick = 0.0
        self._print_speed_steps = [round(0.95 + (i * 0.01), 2) for i in range(11)]
        self._cold_warmup_lines = random.randint(12, 17)
        try:
            ensure_directory(self._asset_library_dir)
            ensure_directory(os.path.join(self._asset_library_dir, IBM_DIR_NAME, STATE_WARMUP_DIR_NAME))
            ensure_directory(os.path.join(self._asset_library_dir, IBM_DIR_NAME, STATE_STEADY_DIR_NAME))
            ensure_directory(os.path.join(self._asset_library_dir, PAN_DIR_NAME, STATE_WARMUP_DIR_NAME))
            ensure_directory(os.path.join(self._asset_library_dir, PAN_DIR_NAME, STATE_STEADY_DIR_NAME))
            ensure_directory(self._linefeed_cache_dir)
            ensure_directory(self._print_cache_dir)
        except Exception:
            self._linefeed_cache_dir = self.base_dir
            self._print_cache_dir = self.base_dir
        self._build_asset_registry()
        self._init_pygame()
        self._prewarm_all_head_assets()

    def _init_pygame(self):
        if pygame is None or self._pygame_ready or self._pygame_failed:
            return
        try:
            if not pygame.mixer.get_init():
                pygame.mixer.init(frequency=44100, size=-16, channels=2, buffer=256)
            pygame.mixer.set_num_channels(max(8, pygame.mixer.get_num_channels()))
            self._pygame_channel = pygame.mixer.Channel(2)
            self._channel_router.configure()
            self._pygame_ready = True
        except Exception:
            self._pygame_failed = True
            self._pygame_ready = False

    def _registry_scan_state(self, printer_key, state_key):
        state_dir = os.path.join(self._asset_library_dir, printer_key, state_key)
        if not os.path.isdir(state_dir):
            return
        mapping = {}
        for name in os.listdir(state_dir):
            if not name.lower().endswith(".wav"):
                continue
            full = os.path.join(state_dir, name)
            pitch_index = 0
            m = re.search(r"_s(\d+)", name)
            if m:
                try:
                    pitch_index = int(m.group(1))
                except ValueError:
                    pitch_index = 0
            mapping[pitch_index] = full
        self._asset_registry[printer_key][state_key] = mapping

    def _build_asset_registry(self):
        self._asset_registry = {
            "ibm_9pin": {"warmup": {}, "steady_state": {}},
            "panasonic_24pin": {"warmup": {}, "steady_state": {}},
        }
        self._registry_scan_state("ibm_9pin", "warmup")
        self._registry_scan_state("ibm_9pin", "steady_state")
        self._registry_scan_state("panasonic_24pin", "warmup")
        self._registry_scan_state("panasonic_24pin", "steady_state")

    def _registry_get(self, printer_key, state_key, pitch_index):
        state_map = self._asset_registry.get(printer_key, {}).get(state_key, {})
        if not state_map:
            return None
        return state_map.get(pitch_index)

    def _resolve_file(self, name):
        base_name = self.sound_files.get(name)
        if not base_name:
            return None
        stem, ext = os.path.splitext(base_name)
        profile = self.state.sound_profile.upper()
        candidate_names = []
        candidate_names.append(base_name)
        candidate_names.append(f"{stem}_{profile.lower()}{ext}")
        for candidate in candidate_names:
            full_path = os.path.join(self.base_dir, candidate)
            if os.path.exists(full_path):
                return full_path
        return None

    def _current_printer_key(self):
        return "panasonic_24pin" if self.state.head_profile.upper() == "PAN24" else "ibm_9pin"

    def _asset_state_dir(self, printer_key, state_key):
        return os.path.join(self._asset_library_dir, printer_key, state_key)

    def _resolve_print_source(self):
        if self.state.head_profile.upper() == "PAN24":
            candidates = [
                "24pin_dotmatrixprinter.wav",
                "dot_matrix_sound_pan24.wav",
                "dot_matrix_sound.wav",
            ]
        else:
            candidates = [
                "256900__system_fm__nadeldrucker.wav",
                "256899__system_fm__nadeldrucker2.wav",
                "256898__system_fm__nadeldrucker3.wav",
                "256897__system_fm__nadeldrucker4.wav",
                "dot_matrix_sound_ibm9.wav",
                "dot_matrix_sound.wav",
            ]
        for name in candidates:
            path = os.path.join(self.base_dir, name)
            if os.path.exists(path):
                return path
        return self._resolve_file("print")

    def _synth_click(self, freq, ms):
        try:
            winsound.Beep(int(freq), int(ms))
        except RuntimeError:
            pass

    def _synth_line_feed(self):
        sweep = [(840, 16), (740, 18), (660, 20), (570, 20)]
        for freq, dur in sweep:
            self._synth_click(freq, dur)

    def _linefeed_variant_path(self, source_path, speed_factor, gain_factor):
        source_key = os.path.basename(source_path).replace(".", "_")
        speed_key = int(round(speed_factor * 1000))
        gain_key = int(round(gain_factor * 1000))
        file_name = f"linefeed_{source_key}_s{speed_key}_g{gain_key}.wav"
        return os.path.join(self._asset_state_dir(self._current_printer_key(), "warmup"), file_name)

    def _build_linefeed_variant(self, source_path, speed_factor, gain_factor):
        key = (source_path, speed_factor, gain_factor)
        cached = self._linefeed_variant_cache.get(key)
        if cached and os.path.exists(cached):
            return cached

        out_path = self._linefeed_variant_path(source_path, speed_factor, gain_factor)
        registry_hit = self._registry_get(self._current_printer_key(), "warmup", int(round(speed_factor * 1000)))
        if registry_hit and os.path.exists(registry_hit):
            self._linefeed_variant_cache[key] = registry_hit
            return registry_hit
        if os.path.exists(out_path):
            self._linefeed_variant_cache[key] = out_path
            return out_path

        try:
            with wave.open(source_path, "rb") as src:
                nch = src.getnchannels()
                sw = src.getsampwidth()
                fr = src.getframerate()
                frames = src.readframes(src.getnframes())

            adjusted = frames
            if sw == 2:
                samples = array.array("h")
                samples.frombytes(frames)
                for i, value in enumerate(samples):
                    boosted = int(value * gain_factor)
                    if boosted > 32767:
                        boosted = 32767
                    elif boosted < -32768:
                        boosted = -32768
                    samples[i] = boosted
                adjusted = samples.tobytes()
            out_rate = max(8000, min(96000, int(fr * speed_factor)))
            with wave.open(out_path, "wb") as out:
                out.setnchannels(nch)
                out.setsampwidth(sw)
                out.setframerate(out_rate)
                out.writeframes(adjusted)
            self._linefeed_variant_cache[key] = out_path
            self._asset_registry[self._current_printer_key()]["warmup"][int(round(speed_factor * 1000))] = out_path
            return out_path
        except Exception:
            return source_path

    def _trimmed_print_path(self, source_path, trim_seconds=1.8):
        try:
            mtime = int(os.path.getmtime(source_path))
            size = int(os.path.getsize(source_path))
        except OSError:
            return source_path
        key = (source_path, mtime, size, int(trim_seconds * 1000))
        cached = self._print_trim_cache.get(key)
        if cached and os.path.exists(cached):
            return cached
        source_key = os.path.basename(source_path).replace(".", "_")
        out_name = f"print_trim_{source_key}_t{int(trim_seconds * 1000)}.wav"
        out_path = os.path.join(self._asset_state_dir(self._current_printer_key(), "warmup"), out_name)
        if os.path.exists(out_path):
            self._print_trim_cache[key] = out_path
            return out_path
        try:
            with wave.open(source_path, "rb") as src:
                nch = src.getnchannels()
                sw = src.getsampwidth()
                fr = src.getframerate()
                nframes = src.getnframes()
                frames = src.readframes(nframes)

            frame_bytes = max(1, nch * sw)
            start_frame = int(max(0.0, trim_seconds) * fr)
            start_byte = min(len(frames), start_frame * frame_bytes)
            trimmed_frames = frames[start_byte:]
            if len(trimmed_frames) < frame_bytes * 32:
                trimmed_frames = frames

            with wave.open(out_path, "wb") as out:
                out.setnchannels(nch)
                out.setsampwidth(sw)
                out.setframerate(fr)
                out.writeframes(trimmed_frames)
            self._print_trim_cache[key] = out_path
            return out_path
        except Exception:
            return source_path

    def _print_speed_variant_path(self, source_path, speed_factor):
        source_key = os.path.basename(source_path).replace(".", "_")
        speed_key = int(round(speed_factor * 1000))
        file_name = f"print_speed_{source_key}_s{speed_key}.wav"
        return os.path.join(self._asset_state_dir(self._current_printer_key(), "steady_state"), file_name)

    def _build_print_speed_variant(self, source_path, speed_factor):
        key = (source_path, speed_factor)
        cached = self._print_speed_cache.get(key)
        if cached and os.path.exists(cached):
            return cached

        out_path = self._print_speed_variant_path(source_path, speed_factor)
        printer_key = "panasonic_24pin" if self.state.head_profile.upper() == "PAN24" else "ibm_9pin"
        registry_hit = self._registry_get(printer_key, "steady_state", int(round(speed_factor * 1000)))
        if registry_hit and os.path.exists(registry_hit):
            self._print_speed_cache[key] = registry_hit
            return registry_hit
        if os.path.exists(out_path):
            self._print_speed_cache[key] = out_path
            return out_path
        try:
            with wave.open(source_path, "rb") as src:
                nch = src.getnchannels()
                sw = src.getsampwidth()
                fr = src.getframerate()
                frames = src.readframes(src.getnframes())
            out_rate = max(8000, min(96000, int(fr * speed_factor)))
            with wave.open(out_path, "wb") as out:
                out.setnchannels(nch)
                out.setsampwidth(sw)
                out.setframerate(out_rate)
                out.writeframes(frames)
            self._print_speed_cache[key] = out_path
            self._asset_registry[printer_key]["steady_state"][int(round(speed_factor * 1000))] = out_path
            return out_path
        except Exception:
            return source_path

    def _get_print_pygame_sound(self, speed_factor):
        source = self._resolve_print_source()
        if not source:
            return None
        trimmed = self._trimmed_print_path(source, trim_seconds=1.8)
        variant = self._build_print_speed_variant(trimmed, speed_factor)
        cache_key = ("print", variant)
        cached = self._pygame_sound_cache.get(cache_key)
        if cached is not None:
            return cached
        try:
            snd = pygame.mixer.Sound(variant)
            self._pygame_sound_cache[cache_key] = snd
            return snd
        except Exception:
            return None

    def _prewarm_head_assets(self, head_profile):
        prev = self.state.head_profile
        try:
            self.state.head_profile = head_profile
            source = self._resolve_print_source()
            if not source or (not os.path.exists(source)):
                return
            trimmed = self._trimmed_print_path(source, trim_seconds=1.8)
            for speed_factor in self._print_speed_steps:
                variant = self._build_print_speed_variant(trimmed, speed_factor)
                if self._pygame_ready and variant and os.path.exists(variant):
                    cache_key = ("print", variant)
                    if cache_key not in self._pygame_sound_cache:
                        try:
                            self._pygame_sound_cache[cache_key] = pygame.mixer.Sound(variant)
                        except Exception:
                            pass
        finally:
            self.state.head_profile = prev

    def _prewarm_all_head_assets(self):
        self._prewarm_head_assets("IBM9")
        self._prewarm_head_assets("PAN24")

    def prepare_active_head_assets(self):
        self._prewarm_head_assets(self.state.head_profile)

    def _play_print_tick(self):
        self._init_pygame()
        if self._pygame_ready and self._pygame_channel is not None:
            line_idx = max(1, int(getattr(self.state, "current_line", 1)))
            is_pan24 = (self.state.head_profile.upper() == "PAN24")
            if is_pan24:
                # PAN24 warmup applies only during initial startup lines.
                phase_line = line_idx
            else:
                # IBM9 keeps cloned warmup envelope across the full job.
                phase_line = ((line_idx - 1) % 11) + 1
            warm_end = 11
            cold_phase = (phase_line <= warm_end) if is_pan24 else True
            if cold_phase:
                # From line 6 onward, progressively reduce startup artifacts until warmup ends.
                taper = 1.0
                if phase_line >= 6:
                    taper = max(0.0, (warm_end - phase_line) / max(1.0, float(warm_end - 6)))
                if phase_line < 9:
                    # Subtle warmup from lines 5-8.
                    speed_factor = round(random.uniform(0.92, 0.99), 2)
                    # Startup hang/stutter physics: occasional heavier mechanical pause.
                    if random.random() < (0.06 * taper):
                        time.sleep(random.uniform(0.28, 0.55) * taper)
                        return True
                    if random.random() < (0.04 * taper):
                        time.sleep(random.uniform(0.003, 0.010) * max(0.35, taper))
                        return True
                else:
                    # More obvious catch-up behavior from lines 9-11.
                    ramp = min(1.0, max(0.0, (phase_line - 9) / 2.0))
                    speed_min = 0.88 + (0.10 * ramp)
                    speed_max = 0.98 + (0.05 * ramp)
                    speed_factor = round(random.uniform(speed_min, speed_max), 2)
                    skip_chance = 0.16 * (1.0 - ramp) * taper
                    hang_chance = 0.14 * (1.0 - ramp) * taper
                    if random.random() < hang_chance:
                        time.sleep(0.10 + random.uniform(0.08, 0.45) * (1.0 - ramp) * taper)
                        return True
                    if random.random() < skip_chance:
                        time.sleep(0.004 + random.uniform(0.004, 0.022) * (1.0 - ramp) * taper)
                        return True
            else:
                speed_factor = round(random.uniform(0.95, 1.05), 2)
            snd = self._get_print_pygame_sound(speed_factor)
            if snd is not None:
                volume = random.uniform(0.90, 1.00)
                if self.state.sound_profile.upper() == "QUIET":
                    volume *= 0.65
                try:
                    if cold_phase:
                        if phase_line < 9:
                            time.sleep(random.uniform(0.003, 0.010) * max(0.35, taper))
                        else:
                            ramp = min(1.0, max(0.0, (phase_line - 9) / 2.0))
                            time.sleep(0.002 + random.uniform(0.003, 0.016) * (1.0 - ramp) * taper)
                    else:
                        if not is_pan24:
                            # Keep IBM9 micro-jitter active for the full print job.
                            time.sleep(random.uniform(0.001, 0.012))
                            if random.random() < (0.020 if self.state.mode_name == "DRAFT" else 0.010):
                                time.sleep(random.uniform(0.004, 0.022))
                    played = self._channel_router.play_strike(snd, volume)
                    if played:
                        return True
                    # Pool saturated; skip instead of forcing overlap/truncation.
                    return True
                except Exception:
                    pass

        source = self._resolve_print_source()
        if source and os.path.exists(source):
            try:
                winsound.PlaySound(source, winsound.SND_FILENAME | winsound.SND_ASYNC)
                return True
            except RuntimeError:
                return False
        return False

    def play_loop(self, name):
        if not self.state.sound_enabled:
            return
        if name == "print":
            return
        file_name = self._resolve_file(name)
        self._init_pygame()
        if self._pygame_ready and file_name and os.path.exists(file_name):
            try:
                loop_idx = 0 if name == "carriage" else 1
                if loop_idx < len(self._channel_router.loop_channels):
                    cache_key = ("loop", file_name)
                    snd = self._pygame_sound_cache.get(cache_key)
                    if snd is None:
                        snd = pygame.mixer.Sound(file_name)
                        self._pygame_sound_cache[cache_key] = snd
                    ch = self._channel_router.loop_channels[loop_idx]
                    if not ch.get_busy():
                        ch.play(snd, loops=-1)
                    return
            except Exception:
                pass
        if file_name:
            try:
                winsound.PlaySound(
                    file_name,
                    winsound.SND_FILENAME | winsound.SND_ASYNC | winsound.SND_LOOP,
                )
            except RuntimeError:
                pass

    def stop(self):
        if self._pygame_ready and self._pygame_channel is not None:
            try:
                self._pygame_channel.stop()
            except Exception:
                pass
            self._channel_router.stop_all()
        winsound.PlaySound(None, winsound.SND_PURGE)

    def play_once(self, name, fallback_freq=None, fallback_ms=45, sync=False):
        if not self.state.sound_enabled:
            return
        file_name = self._resolve_file(name)
        if name == "paper":
            quiet_feed = os.path.join(self.base_dir, "dot_matrix_paper_feed_quiet.wav")
            if os.path.exists(quiet_feed):
                speed_factor = round(random.uniform(0.95, 1.00), 3)
                gain_factor = 0.22 if self.state.sound_profile.upper() == "LOUD" else 0.16
                file_name = self._build_linefeed_variant(quiet_feed, speed_factor, gain_factor)
            elif file_name and os.path.exists(file_name):
                speed_factor = round(random.uniform(0.95, 1.00), 3)
                gain_factor = 0.22 if self.state.sound_profile.upper() == "LOUD" else 0.16
                file_name = self._build_linefeed_variant(file_name, speed_factor, gain_factor)
        if file_name:
            try:
                flags = winsound.SND_FILENAME | (winsound.SND_SYNC if sync else winsound.SND_ASYNC)
                winsound.PlaySound(file_name, flags)
            except RuntimeError:
                if fallback_freq:
                    try:
                        winsound.Beep(fallback_freq, fallback_ms)
                    except RuntimeError:
                        pass
        elif name == "paper":
            self._synth_line_feed()
        elif name == "carriage":
            self._synth_click(1020, 24)
            self._synth_click(860, 26)
        elif fallback_freq:
            try:
                winsound.Beep(fallback_freq, fallback_ms)
            except RuntimeError:
                pass

    def tick(self):
        if not self.state.sound_enabled:
            return

        now = time.time()
        min_gap = max(0.004, self.state.char_delay() * 0.35)
        if now - self._last_print_tick < min_gap:
            return
        self._last_print_tick = now

        if self._play_print_tick():
            return

        mode = self.state.mode()
        if not self._resolve_file("print"):
            self._synth_click(mode["beep_freq"] + random.randint(-90, 90), mode["beep_ms"])


def drain_keyboard(state, sound_engine=None, job_queue=None, spool_history=None, settings_path=None, menu_window=True):
    changed = False
    settings_changed = False
    while msvcrt.kbhit():
        key = msvcrt.getwch().lower()
        if not menu_window:
            if key == "q":
                state.abort_job = True
                state.last_key_message = "Cancelling current job after this character."
                changed = True
            continue

        if state.paper_jam_active:
            if key == "q":
                if sound_engine is None:
                    state.stop_all_jobs = True
                    state.hard_exit_worker = True
                    state.request_exit_pending = True
                    state.stop_signal_deadline = time.time() + 1.0
                    state.last_key_message = "Stop requested while jammed. Closing print windows."
                    settings_changed = True
                else:
                    state.abort_job = True
                    state.last_key_message = "Cancelling current jammed print job."
                changed = True
                continue

            consumed, cleared = process_typed_command(state, key)
            if consumed:
                changed = True
                settings_changed = settings_changed or cleared
                continue

        if state.ribbon_depleted_active:
            if key == "q":
                if sound_engine is None:
                    state.stop_all_jobs = True
                    state.hard_exit_worker = True
                    state.request_exit_pending = True
                    state.stop_signal_deadline = time.time() + 1.0
                    state.last_key_message = "Stop requested while ribbon depleted. Closing print windows."
                    settings_changed = True
                else:
                    state.abort_job = True
                    state.last_key_message = "Cancelling current ribbon-depleted print job."
                changed = True
                continue

            consumed, cleared = process_typed_command(state, key)
            if consumed:
                changed = True
                settings_changed = settings_changed or cleared
                continue

        if state.command_mode:
            consumed, cleared = process_typed_command(state, key)
            if consumed:
                changed = True
                settings_changed = settings_changed or cleared
                continue

        if key == ":":
            state.command_mode = True
            state.command_buffer = ""
            state.last_key_message = "Command mode active: type --replace-ribbon then Enter."
            changed = True
            continue

        if key == "m":
            state.mode_name = "NLQ" if state.mode_name == "DRAFT" else "DRAFT"
            state.last_key_message = f"Mode switched to {state.mode_name}."
            changed = True
            settings_changed = True
        elif key in ("+", "="):
            state.speed_scale = max(0.25, round(state.speed_scale - 0.10, 2))
            state.last_key_message = f"Speed increased to {state.speed_scale:.2f}x delay."
            changed = True
            settings_changed = True
        elif key in ("-", "_"):
            state.speed_scale = min(3.00, round(state.speed_scale + 0.10, 2))
            state.last_key_message = f"Speed decreased to {state.speed_scale:.2f}x delay."
            changed = True
            settings_changed = True
        elif key == "s":
            state.sound_enabled = not state.sound_enabled
            if not state.sound_enabled and sound_engine:
                sound_engine.stop()
            state.last_key_message = "Sound enabled." if state.sound_enabled else "Sound muted."
            changed = True
            settings_changed = True
        elif key == "k":
            state.sound_profile = "QUIET" if state.sound_profile == "LOUD" else "LOUD"
            state.last_key_message = f"Sound profile set to {state.sound_profile}."
            changed = True
            settings_changed = True
        elif key == "v":
            if sound_engine:
                sound_engine.stop()
            state.head_profile = cycle_head_profile(state.head_profile)
            if sound_engine:
                sound_engine.prepare_active_head_assets()
                sound_engine._last_print_tick = 0.0
            state.last_key_message = f"Head sound set to {state.head_profile}."
            changed = True
            settings_changed = True
        elif key == "g":
            state.grunge_enabled = not state.grunge_enabled
            if state.grunge_enabled:
                state.last_key_message = "Ribbon wear enabled."
            else:
                enforce_clean_ribbon_override(state)
                state.last_key_message = "Clean ribbon enabled. Ribbon life locked at 100%."
            changed = True
            settings_changed = True
        elif key == "y":
            state.paper_theme = cycle_theme(state.paper_theme)
            state.last_key_message = f"Paper theme set to {state.paper_theme}."
            changed = True
            settings_changed = True
        elif key == "f":
            state.feed_mode = "FAST" if state.feed_mode == "REALISTIC" else "REALISTIC"
            state.last_key_message = f"Feed style set to {state.feed_mode}."
            changed = True
            settings_changed = True
        elif key == "a":
            state.fit_mode = "WRAP" if state.fit_mode == "TRUNCATE" else "TRUNCATE"
            state.last_key_message = f"Auto-fit mode set to {state.fit_mode}."
            changed = True
            settings_changed = True
        elif key == "u":
            if settings_path and os.path.exists(settings_path):
                load_settings(state, settings_path)
                state.last_key_message = "Refreshed from current settings file."
            else:
                state.last_key_message = "Refresh skipped: settings file not found."
            changed = True
        elif key == "i":
            if settings_path and os.path.exists(settings_path):
                load_settings(state, settings_path)
                state.last_key_message = "Loaded saved config from settings file."
            else:
                state.last_key_message = "Load skipped: settings file not found."
            changed = True
        elif key == "w":
            if settings_path:
                if safe_save_settings(state, settings_path):
                    state.last_key_message = "Settings written to disk."
                else:
                    state.last_key_message = "Settings write failed."
            else:
                state.last_key_message = "Settings write skipped: no settings path."
            changed = True
        elif key == "l":
            state.request_line_feed += 1
            state.last_key_message = f"Queued manual line feed ({state.request_line_feed})."
            changed = True
        elif key == "t":
            state.request_form_feed = True
            state.last_key_message = "Queued top-of-form feed."
            changed = True
        elif key == "x":
            if job_queue is None or spool_history is None:
                state.last_key_message = "Demo-page key works from main menu window."
            else:
                state.request_demo_job = True
                state.last_key_message = "Queued demo printer test page."
            changed = True
        elif key == "h":
            if spool_history is None:
                state.last_key_message = "History controls are available in the main window."
            elif spool_history:
                if state.history_cursor < 0:
                    state.history_cursor = len(spool_history) - 1
                else:
                    state.history_cursor = (state.history_cursor - 1) % len(spool_history)
                selected = spool_history[state.history_cursor]
                state.last_key_message = f"Selected history job #{selected['job_id']:04d} ({os.path.basename(selected['spool_path'])})."
            else:
                state.last_key_message = "No spool history yet."
            changed = True
        elif key == "j":
            if spool_history is None:
                state.last_key_message = "History controls are available in the main window."
            elif spool_history:
                if state.history_cursor < 0:
                    state.history_cursor = 0
                else:
                    state.history_cursor = (state.history_cursor + 1) % len(spool_history)
                selected = spool_history[state.history_cursor]
                state.last_key_message = f"Selected history job #{selected['job_id']:04d} ({os.path.basename(selected['spool_path'])})."
            else:
                state.last_key_message = "No spool history yet."
            changed = True
        elif key == "r":
            if spool_history:
                if state.history_cursor < 0:
                    state.history_cursor = len(spool_history) - 1
                state.request_reprint = True
                selected = spool_history[state.history_cursor]
                state.last_key_message = f"Queued reprint request for job #{selected['job_id']:04d}."
            else:
                state.last_key_message = "No spool history to reprint."
            changed = True
        elif key == "p":
            state.paused = not state.paused
            if state.paused and sound_engine:
                sound_engine.stop()
            state.last_key_message = "Paused." if state.paused else "Resumed."
            changed = True
            settings_changed = True
        elif key == "q":
            if sound_engine is None:
                state.stop_all_jobs = True
                state.hard_exit_worker = True
                state.request_exit_pending = True
                state.stop_signal_deadline = time.time() + 1.0
                state.last_key_message = "Shutdown requested. Waiting for print windows to close..."
                settings_changed = True
            else:
                state.abort_job = True
                state.last_key_message = "Cancelling current job after this character."
            changed = True

    if settings_changed and settings_path:
        if safe_save_settings(state, settings_path):
            state.last_key_message = f"{state.last_key_message} [saved]"

    return changed


def wait_if_paused(state, sound_engine, show_dashboard=True):
    while state.paused and not state.abort_job:
        drain_keyboard(state, sound_engine, menu_window=False)
        redraw_print_view(state, show_dashboard=show_dashboard)
        time.sleep(0.08)


def compute_dynamic_paper_width(lines, min_width=40, max_width=PAPER_WIDTH, hard_stream_limit=80):
    max_visible = 0
    for line in lines:
        if line is None:
            continue
        max_visible = max(max_visible, len(str(line)[:hard_stream_limit]))
    if max_visible <= 0:
        return max_width
    return max(min_width, min(max_width, max_visible))


def ribbon_decay_curve(health):
    h = max(0.0, min(1.0, health))
    wear = 1.0 - h
    smooth = wear ** 3.4
    collapse = 0.0
    if h < 0.09:
        collapse = ((0.09 - h) / 0.09) ** 2.1
    return smooth, collapse


def age_character(char, state):
    if char == " " or not state.grunge_enabled:
        return char

    health = max(0.0, min(1.0, state.ribbon_health))
    # Strict floor: absolutely no degradation above the 9% collapse threshold.
    if health >= 0.09:
        return char
    if health <= 0.0:
        return " "

    mode = state.mode()
    smooth, collapse = ribbon_decay_curve(health)
    extra_dropout = (0.06 * smooth) + (0.65 * collapse)
    # Hotfix #5: intensify low-ribbon weight fade while keeping fixed-width output.
    extra_faint = (0.16 * smooth) + (0.50 * collapse)
    ghost_chance = (0.09 * smooth) + (0.45 * collapse)
    fade_strength = min(1.0, (0.09 - health) / 0.09)
    roll = random.random()
    if roll < extra_dropout:
        return " "
    if roll < (extra_dropout + mode["faint_chance"] + extra_faint):
        if health <= 0.04:
            if char in "|-_=+":
                return "."
            return random.choice([".", ":", "`", "'"])
        if char in "#@MW8":
            return "." if fade_strength > 0.55 else "+"
        if char in "|-_=+":
            return "."
        if fade_strength > 0.70:
            return random.choice([".", ":", "`", "'"])
        return char.lower()
    if roll < (extra_dropout + mode["faint_chance"] + extra_faint + ghost_chance):
        return random.choice([".", ",", "`", "'"])
    return char


def format_incoming_text(text_content, is_csv=True):
    lines = []

    if is_csv:
        csv_file = io.StringIO(text_content)
        reader = list(csv.reader(csv_file))

        if reader:
            column_width = 15
            formatted_rows = [
                '| ' + ' | '.join(f'{cell:<{column_width-3}}' for cell in r) + ' |'
                for r in reader if r
            ]

            if formatted_rows:
                row_length = len(formatted_rows[0])
                border = '+' + '-' * (row_length - 2) + '+'
                lines = [border] + formatted_rows + [border]
    else:
        normalized = text_content.replace("\r\n", "\n").replace("\r", "\n")
        current = []
        for ch in normalized:
            if ch == "\f":
                lines.append("".join(current))
                current = []
                lines.append("\f")
            elif ch == "\n":
                lines.append("".join(current))
                current = []
            else:
                current.append(ch)
        lines.append("".join(current))

    return lines


def expand_lines_for_fit_mode(lines, fit_mode, width=PAPER_WIDTH):
    if fit_mode != "WRAP":
        return [line[:width] for line in lines]

    expanded = []
    for line in lines:
        if not line:
            expanded.append("")
            continue
        start = 0
        while start < len(line):
            expanded.append(line[start:start + width])
            start += width
    return expanded


def simulate_dot_matrix_print(
    text_content,
    state,
    is_csv=True,
    settings_path=None,
    centralized_control=False,
    assets_dir=".",
    show_dashboard=True,
):
    # Force per-job line counters to reset at the start of every print job.
    state.current_line = 0
    state.total_lines = 0
    state.page_count = 1
    state.page_line_count = 0
    sound_engine = PrinterSoundEngine(SOUND_FILES, state, base_dir=assets_dir)
    state.abort_job = False
    state.printed_chars = 0

    try:
        lines = format_incoming_text(text_content, is_csv=is_csv)
        paper_width = compute_dynamic_paper_width(lines, min_width=40, max_width=PAPER_WIDTH, hard_stream_limit=80)
        state.active_paper_width = paper_width
        render_lines = expand_lines_for_fit_mode(lines, state.fit_mode, width=paper_width)
        state.total_lines = len(render_lines)
        state.current_line = 0
        state.status = "PRINTING"
        reset_print_surface(state, show_dashboard=show_dashboard)
        print_tractor_paper_header(state, width=paper_width)

        live_settings_mtime = None
        for line_number, line in enumerate(render_lines, start=1):
            state.current_line = line_number
            enforce_clean_ribbon_override(state)
            if state.ribbon_depleted_active:
                live_settings_mtime = wait_if_ribbon_depleted(
                    state,
                    settings_path,
                    live_settings_mtime,
                    sound_engine=sound_engine,
                )
            if centralized_control:
                drain_keyboard(state, sound_engine, settings_path=settings_path, menu_window=False)
                live_settings_mtime = apply_live_settings_from_file(state, settings_path, live_settings_mtime)
                if state.stop_all_jobs:
                    state.abort_job = True
                    if state.hard_exit_worker:
                        state.hard_exit_worker = False
                        safe_save_settings(state, settings_path)
                        os._exit(0)
                live_settings_mtime = wait_if_paused_centralized(state, settings_path, live_settings_mtime, sound_engine=sound_engine)
            else:
                drain_keyboard(state, sound_engine, menu_window=False)
                if state.stop_all_jobs:
                    state.abort_job = True
                wait_if_paused(state, sound_engine, show_dashboard=show_dashboard)
            if state.abort_job:
                break

            if line == "\f":
                force_form_feed_page_break(state)
                if settings_path:
                    safe_save_settings(state, settings_path)
                sound_engine.play_once("paper", sync=True)
                animate_paper_feed(line_number, state.line_delay(), state)
                print("    [Form feed detected - advanced to next page]")
                continue

            sound_engine.play_loop("print")
            physical_line_number = (state.page_line_count % STANDARD_PAGE_LINES) + 1
            hole = paper_hole(state, physical_line_number)
            print(f" {hole}  | ", end="", flush=True)
            ibm9_active = (state.head_profile.upper() == "IBM9")
            nlq_double_pass = (ibm9_active and state.mode_name.upper() == "NLQ")
            pass_count = 2 if nlq_double_pass else 1
            column = 0

            for pass_idx in range(pass_count):
                if pass_idx == 1:
                    # IBM9 + NLQ: mechanical second strike over same line.
                    sound_engine.play_once("carriage")
                    time.sleep(max(0.012, state.char_delay() * 1.60))
                    print(f"\r {hole}  | ", end="", flush=True)
                    column = 0

                for char in line:
                    if centralized_control:
                        drain_keyboard(state, sound_engine, settings_path=settings_path, menu_window=False)
                        live_settings_mtime = apply_live_settings_from_file(state, settings_path, live_settings_mtime)
                        if state.stop_all_jobs:
                            state.abort_job = True
                            if state.hard_exit_worker:
                                state.hard_exit_worker = False
                                safe_save_settings(state, settings_path)
                                os._exit(0)
                        live_settings_mtime = wait_if_paused_centralized(state, settings_path, live_settings_mtime, sound_engine=sound_engine)
                    else:
                        drain_keyboard(state, sound_engine, menu_window=False)
                        if state.stop_all_jobs:
                            state.abort_job = True
                        wait_if_paused(state, sound_engine, show_dashboard=show_dashboard)
                    if state.abort_job:
                        break

                    if column >= paper_width:
                        break

                    if (
                        state.grunge_enabled
                        and state.ribbon_health < 0.09
                        and random.random() < state.mode()["jitter_chance"]
                        and column < paper_width
                    ):
                        print(" ", end="", flush=True)
                        column += 1
                        time.sleep(state.char_delay() * 0.35)
                        if column >= paper_width:
                            break

                    out_char = age_character(char, state)
                    if ibm9_active and out_char != " ":
                        if nlq_double_pass:
                            if pass_idx == 0:
                                out_char = f"\x1b[2m{out_char}\x1b[22m"
                            else:
                                out_char = f"\x1b[1m{out_char}\x1b[22m"
                        elif state.mode_name.upper() == "DRAFT":
                            out_char = f"\x1b[2m{out_char}\x1b[22m"
                    print(out_char, end="", flush=True)
                    column += 1
                    state.printed_chars += 1
                    apply_ribbon_wear(state)
                    if state.ribbon_health <= 0.0 and (not state.ribbon_depleted_active):
                        trigger_ribbon_depleted(state)
                        if settings_path:
                            safe_save_settings(state, settings_path)
                        live_settings_mtime = wait_if_ribbon_depleted(
                            state,
                            settings_path,
                            live_settings_mtime,
                            sound_engine=sound_engine,
                        )
                        if state.abort_job:
                            break
                    sound_engine.tick()
                    sleep_mechanical_impact(state)
                    if nlq_double_pass:
                        time.sleep(state.char_delay() * 0.45)

                if state.abort_job:
                    break

            sound_engine.stop()
            if column < paper_width:
                print(" " * (paper_width - column), end="")
            print(f" |  {hole}")
            if physical_line_number % 3 == 0:
                print("    | " + perforation_char(state, physical_line_number) * paper_width + " |")

            if state.abort_job:
                break

            if (line_number % JAM_CHECK_BLOCK_LINES == 0) and (not state.paper_jam_active):
                if random.random() < JAM_CHANCE_PER_BLOCK:
                    trigger_paper_jam(state, line_number)
                    jam_noise = garble_ascii_burst(random.randint(16, 28))
                    print(f" !!!| {jam_noise:<{paper_width}} |!!!")
                    if settings_path:
                        safe_save_settings(state, settings_path)
                    live_settings_mtime = wait_if_jammed(
                        state,
                        settings_path,
                        live_settings_mtime,
                        sound_engine=sound_engine,
                    )
                    if state.abort_job:
                        break

            if state.head_profile.upper() in ("IBM9", "PAN24"):
                time.sleep(0.20)
            sound_engine.play_once("carriage")
            time.sleep(line_settle_delay(state))
            sound_engine.play_once("paper", sync=True)
            animate_paper_feed(line_number, state.line_delay(), state)
            time.sleep(line_settle_delay(state) * 0.7)
            page_breaks = advance_page_counter(state, 1)
            wrapped_stack = False
            for _ in range(page_breaks):
                wrapped_stack = advance_paper_stack_after_line(state) or wrapped_stack
            if settings_path:
                safe_save_settings(state, settings_path)
            if wrapped_stack:
                print("    | " + perforation_char(state, 1) * paper_width + " |")
                print("    [Stack end reached - loading fresh paper stack]")
                print_tractor_paper_header(state, width=paper_width)

            if state.request_form_feed or state.request_line_feed > 0:
                manual_sound = PrinterSoundEngine(SOUND_FILES, state, base_dir=assets_dir)
                if apply_pending_manual_feed(state, sound_engine=manual_sound):
                    show_waiting_screen(state)

        sound_engine.stop()
        state.status = "CANCELLED" if state.abort_job else "COMPLETE"
        state.last_key_message = "Job cancelled." if state.abort_job else "Job complete. Waiting for the next network print."
        state.current_line = 0
        state.total_lines = 0
        state.page_line_count = 0
        if settings_path:
            safe_save_settings(state, settings_path)
        print() 
        print(f"[{state.last_key_message}]")

    except Exception as e:
        sound_engine.stop()
        state.status = "ERROR"
        state.last_key_message = f"Render error: {e}"
        sound_engine.play_once("error", fallback_freq=320, fallback_ms=180)
        reset_print_surface(state, show_dashboard=show_dashboard)
        print(f"An error occurred during rendering: {e}")


def receive_all(conn, idle_timeout=0.45, max_bytes=MAX_INCOMING_JOB_BYTES):
    image_sig_jpeg = b"\xFF\xD8\xFF"
    image_sig_png = b"\x89PNG"

    chunks = []
    total_bytes = 0
    conn.settimeout(idle_timeout)
    stream_kind = "BINARY"

    try:
        first_chunk = conn.recv(8)
    except socket.timeout:
        first_chunk = b""

    if first_chunk:
        chunks.append(first_chunk)
        total_bytes = len(first_chunk)
        if first_chunk.startswith(image_sig_jpeg) or first_chunk.startswith(image_sig_png):
            stream_kind = "IMAGE"
        else:
            stream_kind = "TEXT" if looks_like_text_stream(first_chunk) else "BINARY"

    while True:
        try:
            chunk = conn.recv(4096)
        except socket.timeout:
            # Some clients keep the socket open; treat a short idle gap as end-of-job.
            if chunks:
                break
            continue

        if not chunk:
            break

        if total_bytes >= max_bytes:
            break

        remaining = max_bytes - total_bytes
        if len(chunk) > remaining:
            chunks.append(chunk[:remaining])
            total_bytes += remaining
            break

        chunks.append(chunk)
        total_bytes += len(chunk)

    return b"".join(chunks), stream_kind


def looks_like_text_stream(sample):
    if not sample:
        return False
    try:
        text = sample.decode("utf-8")
    except UnicodeDecodeError:
        return False
    for ch in text:
        if ch in ("\n", "\r", "\t"):
            continue
        if not ch.isprintable():
            return False
    return True


def image_bytes_to_ascii(image_bytes, max_width=PAPER_WIDTH):
    if Image is None:
        return "[IMAGE PAYLOAD RECEIVED - install Pillow to enable image-to-ASCII conversion]"
    try:
        with Image.open(io.BytesIO(image_bytes)) as img:
            gray = img.convert("L")
            src_w, src_h = gray.size
            if src_w <= 0 or src_h <= 0:
                return "[IMAGE PAYLOAD RECEIVED - invalid image dimensions]"
            out_w = max(8, min(max_width, src_w))
            aspect = src_h / float(src_w)
            out_h = max(8, int(round(out_w * aspect * 0.5)))
            resized = gray.resize((out_w, out_h))
            bw = resized.point(lambda p: 0 if p < 128 else 255, mode="1")
            rows = []
            px = bw.load()
            for y in range(out_h):
                line_chars = []
                for x in range(out_w):
                    line_chars.append("#" if px[x, y] == 0 else " ")
                rows.append("".join(line_chars))
            ascii_block = "\n".join(rows)
            if ascii_block.replace("\n", "").strip() == "":
                return "[IMAGE PAYLOAD RECEIVED - no visible pixels after conversion]"
            return ascii_block
    except Exception as exc:
        return f"[IMAGE PAYLOAD RECEIVED - conversion failed: {exc}]"


def process_image_to_ascii(image_bytes, max_width=PAPER_WIDTH):
    return image_bytes_to_ascii(image_bytes, max_width=max_width)


def classify_and_format_payload(raw_bytes, stream_kind_hint):
    if not raw_bytes:
        return "", "EMPTY"
    kind = stream_kind_hint
    if kind == "IMAGE":
        return image_bytes_to_ascii(raw_bytes, max_width=PAPER_WIDTH), "IMAGE"
    if kind == "TEXT":
        return raw_bytes.decode("utf-8", errors="ignore"), "TEXT"
    if looks_like_text_stream(raw_bytes[:2048]):
        return raw_bytes.decode("utf-8", errors="ignore"), "TEXT"
    # Fallback for unknown binary: preserve visibility without crashing queue/renderer.
    fallback = raw_bytes[:4096].decode("utf-8", errors="ignore")
    if fallback.strip():
        return fallback, "TEXT"
    return "[BINARY PAYLOAD RECEIVED - unsupported format]", "BINARY"


def receiver_loop(server_socket, incoming_queue, stop_event):
    while not stop_event.is_set():
        try:
            conn, addr = server_socket.accept()
        except socket.timeout:
            continue
        except OSError:
            break

        try:
            data, stream_kind = receive_all(conn)
            if data:
                formatted_text, payload_type = classify_and_format_payload(data, stream_kind)
                try:
                    incoming_queue.put_nowait((addr, formatted_text, payload_type))
                except queue.Full:
                    try:
                        _ = incoming_queue.get_nowait()
                    except queue.Empty:
                        pass
                    try:
                        incoming_queue.put_nowait((addr, formatted_text, payload_type))
                    except queue.Full:
                        pass
        finally:
            conn.close()



def reset_print_surface(state, show_dashboard=True):
    # Force a fresh console frame so old paper output never bleeds into the next job.
    set_console_theme(state)
    clear_screen()
    if show_dashboard:
        draw_dashboard(state)


def show_waiting_screen(state, preserve_paper=False, force_redraw=False):
    state.status = "ONLINE"
    state.client_label = "IDLE"
    state.byte_count = 0
    state.total_lines = 0
    state.current_line = 0
    state.printed_chars = 0
    if preserve_paper and state.paper_stack_used > 0 and not force_redraw:
        return
    reset_print_surface(state)
    print("Listening on 0.0.0.0:9999. Send a job from Debian when ready.")


def startup_config_screen(state, startup_self_test_line=""):
    # Quick pre-online config pass with auto-continue.
    deadline = time.time() + 8.0
    while True:
        seconds_left = max(0, int(deadline - time.time()))
        reset_print_surface(state)
        print()
        print("STARTUP CONFIG")
        print("  M mode   +/- speed   S sound   V head profile   G ribbon   F feed style")
        print("  Y theme  A auto-fit  D defaults   Enter/O go online")
        if startup_self_test_line:
            print(f"  {startup_self_test_line}")
        print(f"  Auto online in {seconds_left}s")

        if seconds_left == 0:
            state.last_key_message = "Startup config complete. Going online."
            return

        if msvcrt.kbhit():
            key = msvcrt.getwch().lower()
            if key in ("\r", "\n", "o"):
                state.last_key_message = "Startup config complete. Going online."
                return
            if key == "d":
                state.mode_name = "DRAFT"
                state.sound_enabled = True
                state.head_profile = "IBM9"
                state.grunge_enabled = True
                state.speed_scale = 0.70
                state.feed_mode = "REALISTIC"
                state.paper_theme = "CLASSIC"
                state.fit_mode = "TRUNCATE"
                state.ribbon_health = 1.0
                state.ribbon_chars_used = 0
                state.last_key_message = "Defaults restored."
            else:
                if key == "m":
                    state.mode_name = "NLQ" if state.mode_name == "DRAFT" else "DRAFT"
                elif key in ("+", "="):
                    state.speed_scale = max(0.25, round(state.speed_scale - 0.10, 2))
                elif key in ("-", "_"):
                    state.speed_scale = min(3.00, round(state.speed_scale + 0.10, 2))
                elif key == "s":
                    state.sound_enabled = not state.sound_enabled
                elif key == "v":
                    state.head_profile = cycle_head_profile(state.head_profile)
                elif key == "g":
                    state.grunge_enabled = not state.grunge_enabled
                elif key == "f":
                    state.feed_mode = "FAST" if state.feed_mode == "REALISTIC" else "REALISTIC"
                elif key == "y":
                    state.paper_theme = cycle_theme(state.paper_theme)
                elif key == "a":
                    state.fit_mode = "WRAP" if state.fit_mode == "TRUNCATE" else "TRUNCATE"
                state.last_key_message = "Startup settings updated."
            deadline = time.time() + 8.0

        time.sleep(0.08)


def parse_cli_args(argv):
    parsed = {
        "print_job": None,
        "job_id": 0,
        "source": "NETWORK",
        "is_csv": False,
        "self_test": False,
        "replace_ribbon": False,
    }
    i = 1
    while i < len(argv):
        token = argv[i]
        if token == "--print-job" and i + 1 < len(argv):
            parsed["print_job"] = argv[i + 1]
            i += 2
            continue
        if token == "--job-id" and i + 1 < len(argv):
            try:
                parsed["job_id"] = int(argv[i + 1])
            except ValueError:
                parsed["job_id"] = 0
            i += 2
            continue
        if token == "--source" and i + 1 < len(argv):
            parsed["source"] = argv[i + 1]
            i += 2
            continue
        if token == "--is-csv":
            parsed["is_csv"] = True
            i += 1
            continue
        if token == "--self-test":
            parsed["self_test"] = True
            i += 1
            continue
        if token == "--replace-ribbon":
            parsed["replace_ribbon"] = True
            i += 1
            continue
        i += 1
    return parsed


def collect_self_test_results(settings_path, assets_dir, spool_dir, include_render=True):
    results = []

    def add(name, ok, detail=""):
        results.append((name, ok, detail))

    try:
        with tempfile.TemporaryDirectory() as td:
            test_settings = os.path.join(td, SETTINGS_FILE_NAME)
            state_out = PrinterState()
            state_out.mode_name = "NLQ"
            state_out.sound_enabled = False
            state_out.sound_profile = "QUIET"
            state_out.head_profile = "PAN24"
            state_out.grunge_enabled = False
            state_out.speed_scale = 1.20
            state_out.feed_mode = "FAST"
            state_out.paper_theme = "AGED"
            state_out.fit_mode = "WRAP"
            state_out.paused = True
            save_settings(state_out, test_settings)

            state_in = PrinterState()
            load_settings(state_in, test_settings)

            ok = (
                state_in.mode_name == "NLQ"
                and state_in.sound_enabled is False
                and state_in.sound_profile == "QUIET"
                and state_in.head_profile == "PAN24"
                and state_in.grunge_enabled is False
                and abs(state_in.speed_scale - 1.20) < 0.001
                and state_in.feed_mode == "FAST"
                and state_in.paper_theme == "AGED"
                and state_in.fit_mode == "WRAP"
                and state_in.paused is True
            )
            add("Settings roundtrip", ok)
    except Exception as exc:
        add("Settings roundtrip", False, str(exc))

    try:
        state_sound = PrinterState()
        engine = PrinterSoundEngine(SOUND_FILES, state_sound, base_dir=assets_dir)
        state_sound.sound_profile = "LOUD"
        loud_ok = all(engine._resolve_file(name) for name in ("print", "carriage", "paper"))
        state_sound.sound_profile = "QUIET"
        quiet_ok = all(engine._resolve_file(name) for name in ("print", "carriage", "paper"))
        add("Sound profile assets", loud_ok and quiet_ok)
    except Exception as exc:
        add("Sound profile assets", False, str(exc))

    try:
        history = load_spool_history(spool_dir)
        add("Spool history load", isinstance(history, list), f"entries={len(history)}")
    except Exception as exc:
        add("Spool history load", False, str(exc))

    try:
        with tempfile.TemporaryDirectory() as td:
            test_settings = os.path.join(td, SETTINGS_FILE_NAME)
            state_write = PrinterState()
            state_write.sound_profile = "LOUD"
            state_write.head_profile = "IBM9"
            save_settings(state_write, test_settings)
            state_live = PrinterState()
            last = apply_live_settings_from_file(state_live, test_settings, None)
            state_write.sound_profile = "QUIET"
            state_write.head_profile = "PAN24"
            save_settings(state_write, test_settings)
            _ = apply_live_settings_from_file(state_live, test_settings, last)
            add("Live settings sync", state_live.sound_profile == "QUIET" and state_live.head_profile == "PAN24")
    except Exception as exc:
        add("Live settings sync", False, str(exc))

    if include_render:
        try:
            state_render = PrinterState()
            load_settings(state_render, settings_path)
            state_render.sound_enabled = False
            state_render.speed_scale = 0.25
            simulate_dot_matrix_print(
                "SELF-TEST",
                state_render,
                is_csv=False,
                settings_path=settings_path,
                centralized_control=False,
                assets_dir=assets_dir,
            )
            add("Render smoke test", True)
        except Exception as exc:
            add("Render smoke test", False, str(exc))

    return results


def startup_self_test_line(settings_path, assets_dir, spool_dir):
    results = collect_self_test_results(settings_path, assets_dir, spool_dir, include_render=False)
    passed = sum(1 for _, ok, _ in results if ok)
    total = len(results)
    status = "PASS" if passed == total else "FAIL"
    return f"Startup self-test: {status} ({passed}/{total})"


def run_self_test(script_dir, settings_path, assets_dir, spool_dir):
    print("Running self-test...")
    results = collect_self_test_results(settings_path, assets_dir, spool_dir, include_render=True)
    checks = []
    for name, ok, detail in results:
        status = "PASS" if ok else "FAIL"
        line = f"[{status}] {name}"
        if detail:
            line += f" - {detail}"
        print(line)
        checks.append(ok)
    all_ok = all(checks) if checks else False
    print("Self-test complete:", "PASS" if all_ok else "FAIL")
    return 0 if all_ok else 1


def spawn_print_worker(script_path, job):
    python_exe = sys.executable
    if not python_exe or not os.path.exists(python_exe):
        python_exe = "python"

    args = [
        python_exe,
        script_path,
        "--print-job",
        job["spool_path"],
        "--job-id",
        str(job["job_id"]),
        "--source",
        job["source"],
    ]
    if job["is_csv"]:
        args.append("--is-csv")
    startupinfo = None
    if os.name == "nt":
        startupinfo = subprocess.STARTUPINFO()
        startupinfo.dwFlags |= getattr(subprocess, "STARTF_USECOUNTCHARS", 0x00000008)
        startupinfo.dwXCountChars = 84
        startupinfo.dwYCountChars = 71
    return subprocess.Popen(
        args,
        creationflags=subprocess.CREATE_NEW_CONSOLE,
        startupinfo=startupinfo,
    )


def run_print_worker(script_dir, settings_path, cli):
    state = PrinterState(mode_name=os.environ.get("DOT_MATRIX_MODE", "DRAFT").upper())
    load_settings(state, settings_path)
    set_console_theme(state)
    clear_screen()
    set_worker_console_size()

    spool_path = cli["print_job"]
    if not spool_path or not os.path.exists(spool_path):
        print(f"Spool file not found: {spool_path}")
        time.sleep(4.0)
        return 1

    with open(spool_path, "r", encoding="utf-8", errors="ignore") as handle:
        text = handle.read()

    state.job_number = cli["job_id"]
    state.client_label = cli["source"]
    state.byte_count = len(text.encode("utf-8", errors="ignore"))
    state.last_key_message = "Worker follows live settings from main window."
    assets_dir = os.path.join(script_dir, EXTRA_DIR_NAME)
    simulate_dot_matrix_print(
        text,
        state,
        is_csv=cli["is_csv"],
        settings_path=settings_path,
        centralized_control=True,
        assets_dir=assets_dir,
        show_dashboard=False,
    )
    print()
    print("[Print job complete. Press Q to close this print window.]")
    while True:
        if msvcrt.kbhit():
            key = msvcrt.getwch().lower()
            if key == "q":
                break
        time.sleep(0.08)
    return 0


if __name__ == "__main__":
    script_dir = os.path.dirname(os.path.abspath(__file__))
    os.chdir(script_dir)
    extra_dir = os.path.join(script_dir, EXTRA_DIR_NAME)
    ensure_directory(extra_dir)
    assets_dir = extra_dir
    settings_path = os.path.join(extra_dir, SETTINGS_FILE_NAME)
    cli = parse_cli_args(sys.argv)

    spool_dir = os.path.join(script_dir, SPOOL_DIR_NAME)
    ensure_directory(spool_dir)

    if cli["replace_ribbon"] and not cli["print_job"] and not cli["self_test"]:
        state_maint = PrinterState(mode_name=os.environ.get("DOT_MATRIX_MODE", "DRAFT").upper())
        load_settings(state_maint, settings_path)
        replace_ribbon(state_maint)
        safe_save_settings(state_maint, settings_path)
        print("Ribbon maintenance complete: ribbon replaced and life restored to 100%.")
        sys.exit(0)

    if cli["self_test"]:
        sys.exit(run_self_test(script_dir, settings_path, assets_dir, spool_dir))

    if cli["print_job"]:
        sys.exit(run_print_worker(script_dir, settings_path, cli))

    if "SPAWNED" not in os.environ:
        script_path = os.path.abspath(__file__)
        env = os.environ.copy()
        env["SPAWNED"] = "1"
        subprocess.Popen(
            [sys.executable, script_path],
            env=env,
            creationflags=subprocess.CREATE_NEW_CONSOLE,
        )
        sys.exit()

    server_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    state = PrinterState(mode_name=os.environ.get("DOT_MATRIX_MODE", "DRAFT").upper())
    load_settings(state, settings_path)
    # Startup reset: line counters should always begin at zero on launch.
    state.current_line = 0
    state.total_lines = 0
    state.page_line_count = 0
    safe_save_settings(state, settings_path)
    if state.stop_all_jobs or state.hard_exit_worker:
        state.stop_all_jobs = False
        state.hard_exit_worker = False
        safe_save_settings(state, settings_path)
    state.last_key_message = "Startup checks deferred to external debug harness."
    set_console_theme(state)
    clear_screen()
    startup_config_screen(state)
    safe_save_settings(state, settings_path)
    job_queue = []
    worker_procs = []
    incoming_queue = queue.Queue(maxsize=INCOMING_QUEUE_MAX)
    stop_receiver = threading.Event()
    receiver = None
    spool_history = load_spool_history(spool_dir)
    next_job_id = max((job["job_id"] for job in spool_history), default=0) + 1
    update_queue_metrics(state, job_queue, spool_history)

    try:
        server_socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        server_socket.bind(('0.0.0.0', 9999))
        server_socket.listen(1)
        server_socket.settimeout(0.15)
        receiver = threading.Thread(
            target=receiver_loop,
            args=(server_socket, incoming_queue, stop_receiver),
            daemon=True,
        )
        receiver.start()

        show_waiting_screen(state)
        main_live_settings_mtime = None
        next_jam_flash = 0.0
        last_live_render = (
            state.page_count,
            state.page_line_count,
            state.paper_stack_used,
            state.ribbon_chars_used,
            round(state.ribbon_health, 5),
        )

        while True:
            worker_procs = [proc for proc in worker_procs if proc.poll() is None]
            state.active_workers = len(worker_procs)
            main_live_settings_mtime = apply_live_settings_from_file(
                state,
                settings_path,
                main_live_settings_mtime,
                sync_theme=False,
            )
            live_render = (
                state.page_count,
                state.page_line_count,
                state.paper_stack_used,
                state.ribbon_chars_used,
                round(state.ribbon_health, 5),
            )
            if live_render != last_live_render:
                show_waiting_screen(state, preserve_paper=True, force_redraw=True)
                last_live_render = live_render
            if state.request_exit_pending and not worker_procs:
                state.request_exit = True
                state.last_key_message = "All print windows closed. Exiting main menu."
                safe_save_settings(state, settings_path)
            if state.paper_jam_active and time.time() >= next_jam_flash:
                state.jam_alert_phase = not state.jam_alert_phase
                show_waiting_screen(state, force_redraw=True)
                next_jam_flash = time.time() + 0.35
            if state.ribbon_depleted_active and time.time() >= next_jam_flash:
                show_waiting_screen(state, force_redraw=True)
                next_jam_flash = time.time() + 0.35
            if state.stop_signal_deadline > 0 and time.time() >= state.stop_signal_deadline:
                if state.stop_all_jobs or state.hard_exit_worker:
                    state.stop_all_jobs = False
                    state.hard_exit_worker = False
                    safe_save_settings(state, settings_path)
                    if not state.request_exit_pending:
                        state.last_key_message = "Stop signal cleared. Ready for new jobs."
                state.stop_signal_deadline = 0.0

            if drain_keyboard(state, job_queue=job_queue, spool_history=spool_history, settings_path=settings_path):
                if state.request_exit:
                    break
                if state.request_reprint:
                    next_job_id = enqueue_selected_history_job(
                        state,
                        job_queue,
                        spool_history,
                        spool_dir,
                        next_job_id,
                    )
                    state.request_reprint = False
                    update_queue_metrics(state, job_queue, spool_history)
                if state.request_demo_job:
                    demo_job = create_job_record(
                        next_job_id,
                        "demo_test_page",
                        build_demo_test_page(),
                        False,
                        spool_dir,
                    )
                    next_job_id += 1
                    job_queue.append(demo_job)
                    spool_history.append(demo_job)
                    state.history_cursor = len(spool_history) - 1
                    state.request_demo_job = False
                    state.last_key_message = f"Demo page queued as job #{demo_job['job_id']:04d}."
                    update_queue_metrics(state, job_queue, spool_history)
                if state.request_form_feed or state.request_line_feed > 0:
                    manual_sound = PrinterSoundEngine(SOUND_FILES, state, base_dir=assets_dir)
                    if apply_pending_manual_feed(state, sound_engine=manual_sound):
                        show_waiting_screen(state, force_redraw=True)
                else:
                    show_waiting_screen(
                        state,
                        preserve_paper=state.paper_stack_used > 0,
                        force_redraw=True,
                    )

            queued_from_network = 0
            if not state.ribbon_depleted_active:
                while queued_from_network < 8:
                    try:
                        addr, text_received, payload_type = incoming_queue.get_nowait()
                    except queue.Empty:
                        break
                    is_csv_data = ',' in text_received
                    source_label = f"{addr[0]}_{addr[1]}"
                    new_job = create_job_record(
                        next_job_id,
                        source_label,
                        text_received,
                        is_csv_data,
                        spool_dir,
                    )
                    next_job_id += 1
                    job_queue.append(new_job)
                    spool_history.append(new_job)
                    state.history_cursor = len(spool_history) - 1
                    update_queue_metrics(state, job_queue, spool_history)
                    state.last_key_message = (
                        f"Queued {payload_type} network job #{new_job['job_id']:04d} from {addr[0]}:{addr[1]}."
                    )
                    queued_from_network += 1

            if queued_from_network:
                show_waiting_screen(state, preserve_paper=state.paper_stack_used > 0)

            if job_queue and (not state.ribbon_depleted_active):
                current_job = job_queue.pop(0)
                update_queue_metrics(state, job_queue, spool_history)

                proc = spawn_print_worker(os.path.abspath(__file__), current_job)
                worker_procs.append(proc)
                state.last_key_message = f"Launched job #{current_job['job_id']:04d} in a new PowerShell print window."
                state.completed_jobs += 1
                state.active_workers = len(worker_procs)
                update_queue_metrics(state, job_queue, spool_history)
                show_waiting_screen(state, preserve_paper=state.paper_stack_used > 0)

    except KeyboardInterrupt:
        print("\nShutting down printer server cleanly.")
    except Exception as server_error:
        print(f"Server Startup Error: {server_error}")
        input("\nPress Enter to close this diagnostic screen...")
    finally:
        try:
            stop_receiver.set()
            if receiver is not None:
                receiver.join(timeout=1.0)
        except Exception:
            pass
        try:
            save_settings(state, settings_path)
        except Exception as save_error:
            print(f"\nSettings save failed: {save_error}")
        server_socket.close()









