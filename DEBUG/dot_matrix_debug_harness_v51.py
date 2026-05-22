import os
import sys
import time
import json
import queue
import socket
import random
import threading
import tempfile
import traceback
import importlib.util
from datetime import datetime


DEBUG_DIR = os.path.dirname(os.path.abspath(__file__))
ROOT_DIR = os.path.dirname(DEBUG_DIR)
TARGET_SCRIPT = os.path.join(ROOT_DIR, "dot_matrix_printer_network_simv51.py")
REPORT_PATH = os.path.join(DEBUG_DIR, "debug_report_v51.txt")


def load_target():
    spec = importlib.util.spec_from_file_location("dot_matrix_v50", TARGET_SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def run_test(name, fn, results):
    started = time.time()
    try:
        detail = fn()
        elapsed = time.time() - started
        results.append({"name": name, "pass": True, "detail": detail, "elapsed_s": round(elapsed, 3)})
    except Exception as exc:
        elapsed = time.time() - started
        results.append(
            {
                "name": name,
                "pass": False,
                "detail": f"{exc}",
                "elapsed_s": round(elapsed, 3),
                "traceback": traceback.format_exc(),
            }
        )


def make_tests(mod):
    def t_compile_syntax():
        import py_compile
        py_compile.compile(TARGET_SCRIPT, doraise=True)
        return "py_compile OK"

    def t_settings_roundtrip():
        with tempfile.TemporaryDirectory() as td:
            sep = os.path.join(td, mod.SETTINGS_FILE_NAME)
            s = mod.PrinterState()
            s.mode_name = "NLQ"
            s.sound_enabled = False
            s.sound_profile = "QUIET"
            s.head_profile = "PAN24"
            s.speed_scale = 1.2
            s.feed_mode = "FAST"
            s.paper_theme = "AGED"
            s.fit_mode = "WRAP"
            s.paused = True
            mod.save_settings(s, sep)
            t = mod.PrinterState()
            mod.load_settings(t, sep)
            assert t.mode_name == "NLQ"
            assert not t.sound_enabled
            assert t.sound_profile == "QUIET"
            assert t.head_profile == "PAN24"
            assert abs(t.speed_scale - 1.2) < 1e-3
            assert t.feed_mode == "FAST"
            assert t.paper_theme == "AGED"
            assert t.fit_mode == "WRAP"
            assert t.paused is True
            return "settings fields persisted and restored"

    def t_ribbon_precision_persistence():
        with tempfile.TemporaryDirectory() as td:
            sep = os.path.join(td, mod.SETTINGS_FILE_NAME)
            s = mod.PrinterState()
            for _ in range(137):
                mod.apply_ribbon_wear(s)
            mod.safe_save_settings(s, sep)
            t = mod.PrinterState()
            mod.load_settings(t, sep)
            assert t.ribbon_chars_used == 137
            assert abs(t.ribbon_health - s.ribbon_health) < 1e-8
            return f"chars={t.ribbon_chars_used}, health={t.ribbon_health:.8f}"

    def t_replace_ribbon_command_path():
        with tempfile.TemporaryDirectory() as td:
            sep = os.path.join(td, mod.SETTINGS_FILE_NAME)
            s = mod.PrinterState()
            for _ in range(500):
                mod.apply_ribbon_wear(s)
            mod.safe_save_settings(s, sep)
            s.command_mode = True
            for ch in "--replace-ribbon":
                mod.process_typed_command(s, ch)
            consumed, changed = mod.process_typed_command(s, "\r")
            assert consumed and changed
            mod.safe_save_settings(s, sep)
            t = mod.PrinterState()
            mod.load_settings(t, sep)
            assert t.ribbon_chars_used == 0
            assert abs(t.ribbon_health - 1.0) < 1e-9
            return "command reset persisted (chars=0, health=1.0)"

    def t_page_stack_65_no_rollover():
        s = mod.PrinterState()
        s.sound_enabled = False
        s.feed_mode = "FAST"
        s.speed_scale = 0.25
        txt = "\n".join([f"L{i}" for i in range(65)])
        mod.simulate_dot_matrix_print(
            txt,
            s,
            is_csv=False,
            settings_path=None,
            centralized_control=False,
            assets_dir=os.path.join(ROOT_DIR, "extra"),
            show_dashboard=False,
        )
        assert s.page_count == 1 and s.page_line_count == 65 and s.paper_stack_used == 0
        return f"page={s.page_count}, line={s.page_line_count}, stack={s.paper_stack_used}"

    def t_page_stack_66_rollover():
        s = mod.PrinterState()
        s.sound_enabled = False
        s.feed_mode = "FAST"
        s.speed_scale = 0.25
        txt = "\n".join([f"L{i}" for i in range(66)])
        mod.simulate_dot_matrix_print(
            txt,
            s,
            is_csv=False,
            settings_path=None,
            centralized_control=False,
            assets_dir=os.path.join(ROOT_DIR, "extra"),
            show_dashboard=False,
        )
        assert s.page_count == 2 and s.page_line_count == 0 and s.paper_stack_used == 1
        return f"page={s.page_count}, line={s.page_line_count}, stack={s.paper_stack_used}"

    def t_form_feed_boundary():
        s = mod.PrinterState()
        s.sound_enabled = False
        s.feed_mode = "FAST"
        s.speed_scale = 0.25
        mod.simulate_dot_matrix_print(
            "A\n\f\nB",
            s,
            is_csv=False,
            settings_path=None,
            centralized_control=False,
            assets_dir=os.path.join(ROOT_DIR, "extra"),
            show_dashboard=False,
        )
        assert s.page_count == 2 and s.paper_stack_used == 1
        return f"page={s.page_count}, line={s.page_line_count}, stack={s.paper_stack_used}"

    def t_worker_close_writes_settings():
        with tempfile.TemporaryDirectory() as td:
            settings = os.path.join(td, mod.SETTINGS_FILE_NAME)
            spool = os.path.join(td, "job.txt")
            with open(spool, "w", encoding="utf-8") as f:
                f.write("ABC\nDEF\nGHI\n")
            s = mod.PrinterState()
            s.sound_enabled = False
            s.feed_mode = "FAST"
            s.speed_scale = 0.25
            mod.save_settings(s, settings)
            before = os.path.getmtime(settings)
            time.sleep(0.02)
            rc = mod.run_print_worker(ROOT_DIR, settings, {"print_job": spool, "job_id": 9001, "source": "DBG", "is_csv": False})
            after = os.path.getmtime(settings)
            t = mod.PrinterState()
            mod.load_settings(t, settings)
            assert rc == 0
            assert after > before
            assert t.ribbon_chars_used > 0 and t.ribbon_health < 1.0
            return f"mtime {before} -> {after}, ribbon chars={t.ribbon_chars_used}"

    def t_receiver_bounded_window():
        host, port = "127.0.0.1", 10121
        srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        srv.bind((host, port))
        srv.listen(8)
        srv.settimeout(0.15)
        q = queue.Queue(maxsize=20)
        stop = threading.Event()
        rx = threading.Thread(target=mod.receiver_loop, args=(srv, q, stop), daemon=True)
        rx.start()
        errs = 0
        for i in range(120):
            payload = (f"JOB{i:04d}|" + ("X" * (400 + i % 43))).encode("utf-8", errors="ignore")
            try:
                with socket.create_connection((host, port), timeout=1.0) as c:
                    c.sendall(payload)
            except Exception:
                errs += 1
        time.sleep(1.0)
        ids = []
        while True:
            try:
                _, data = q.get_nowait()
            except queue.Empty:
                break
            txt = data.decode("utf-8", errors="ignore")
            ids.append(int(txt.split("|", 1)[0][3:]))
        stop.set()
        srv.close()
        rx.join(timeout=1.0)
        assert errs == 0
        assert len(ids) == 20
        assert min(ids) >= 100 and max(ids) == 119
        return f"len={len(ids)}, min={min(ids)}, max={max(ids)}"

    def t_payload_cap_enforced():
        payload = b"A" * (mod.MAX_INCOMING_JOB_BYTES + 250000)

        class DummyConn:
            def __init__(self, data):
                self.data = data
                self.pos = 0

            def settimeout(self, _):
                return

            def recv(self, n):
                if self.pos >= len(self.data):
                    return b""
                chunk = self.data[self.pos : self.pos + n]
                self.pos += len(chunk)
                return chunk

        data = mod.receive_all(DummyConn(payload), idle_timeout=0.01)
        assert len(data) == mod.MAX_INCOMING_JOB_BYTES
        return f"bytes={len(data)} (cap={mod.MAX_INCOMING_JOB_BYTES})"

    def t_spool_trim_bounded():
        with tempfile.TemporaryDirectory() as td:
            for i in range(mod.MAX_SPOOL_FILES + 8):
                mod.spool_job_text(td, i + 1, "dbg", f"job {i}")
                time.sleep(0.001)
            files = [x for x in os.listdir(td) if x.endswith(".txt")]
            assert len(files) <= mod.MAX_SPOOL_FILES
            return f"files={len(files)} (max={mod.MAX_SPOOL_FILES})"

    return [
        ("Compile/syntax", t_compile_syntax),
        ("Settings roundtrip", t_settings_roundtrip),
        ("Ribbon precision persistence", t_ribbon_precision_persistence),
        ("Replace-ribbon command path", t_replace_ribbon_command_path),
        ("Page/stack 65-line invariant", t_page_stack_65_no_rollover),
        ("Page/stack 66-line rollover", t_page_stack_66_rollover),
        ("Form-feed boundary", t_form_feed_boundary),
        ("Worker close writes settings", t_worker_close_writes_settings),
        ("Receiver bounded newest-window", t_receiver_bounded_window),
        ("Incoming payload cap", t_payload_cap_enforced),
        ("Spool trim bounded", t_spool_trim_bounded),
    ]


def render_report(results):
    passed = sum(1 for r in results if r["pass"])
    total = len(results)
    lines = []
    lines.append("Dot Matrix Emulator Debug Report - v50")
    lines.append(f"Generated: {datetime.now().isoformat(timespec='seconds')}")
    lines.append(f"Target: {TARGET_SCRIPT}")
    lines.append("")
    lines.append(f"Summary: {passed}/{total} tests passed")
    lines.append("")
    for r in results:
        tag = "PASS" if r["pass"] else "FAIL"
        lines.append(f"[{tag}] {r['name']} ({r['elapsed_s']:.3f}s)")
        lines.append(f"  Detail: {r.get('detail', '')}")
        tb = r.get("traceback")
        if tb:
            lines.append("  Traceback:")
            for tline in tb.rstrip().splitlines():
                lines.append(f"    {tline}")
    lines.append("")
    lines.append("Raw JSON:")
    lines.append(json.dumps(results, indent=2))
    return "\n".join(lines) + "\n"


def main():
    if not os.path.exists(TARGET_SCRIPT):
        print(f"Missing target script: {TARGET_SCRIPT}")
        return 2

    mod = load_target()
    tests = make_tests(mod)
    results = []
    for name, fn in tests:
        run_test(name, fn, results)
    report = render_report(results)
    with open(REPORT_PATH, "w", encoding="utf-8") as f:
        f.write(report)
    print(f"Wrote report: {REPORT_PATH}")
    passed = sum(1 for r in results if r["pass"])
    total = len(results)
    print(f"Result: {passed}/{total} passed")
    return 0 if passed == total else 1


if __name__ == "__main__":
    raise SystemExit(main())

