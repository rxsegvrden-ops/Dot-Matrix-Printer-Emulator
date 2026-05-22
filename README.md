# Dot Matrix Printer Network Emulator

A Windows terminal-based dot matrix printer emulator that accepts print jobs over the network, renders them as scrolling tractor-feed paper, and simulates vintage printer behavior with line feeds, paper jams, ribbon wear, audio, and multiple print head profiles.

The current working build is:

```text
current_working_build/dot_matrix_printer_network_simb0.0.17hotfix3.py
```

This project started as a practical terminal printer receiver and grew into a small hardware-style simulator. It is meant to feel like an old continuous-feed printer sitting on the network, with the main menu staying available while print jobs open in their own console window.


Development Methodology & Approach

The Python code in this repository was primarily generated using Codex. 
While I do not specialize in writing Python syntax, I used my background in programming and debugging to test, troubleshoot, and refine the AI's output into a fully functional release.

**Project Status**
This Dot Matrix Printer Emulator should run right out of the box. While a few features might still need some polish and aren't 100% perfect yet, the core program is simple and ready to go.

I've personally debugged and cleaned up the files to make sure there's no "AI code slop" or weird generated clutter left behind. That said, if you do happen to spot any messy code or catch a bug I missed, just let me know or open an issue!

## Current Status

- Current version: `b0.0.17hotfix3/v1.0.0`
- Active source file: `current_working_build/dot_matrix_printer_network_simb0.0.17hotfix3.py`
- Network listener: `0.0.0.0:9999`
- Latest validation: `25/25` smoke test pass
- Main platform: Windows / PowerShell
- Print source: network socket, local demo job, or spool reprint


## Main Features

- Listens for incoming print jobs over TCP port `9999`.
- Opens print jobs in separate terminal windows while the main menu stays available.
- Renders text on simulated tractor-feed paper.
- Supports continuous paper, page counting, line feeds, and top-of-form behavior.
- Tracks a virtual paper stack and resets after the stack capacity is reached.
- Supports IBM 9-pin and Panasonic 24-pin print head profiles.
- Includes draft and near-letter-quality style print modes.
- Simulates ribbon wear, ribbon replacement, paper jams, and recovery commands.
- Supports text payloads and image-to-ASCII conversion for image payloads.
- Keeps spool history for reprinting recent jobs.
- Saves settings to a `.sep` file.
- Includes multiple log files for build history, releases, patches, and debugging.


## Requirements

The emulator is mostly standard-library Python, with optional packages for enhanced behavior.

Recommended:

```text
Python 3.10+
pygame
Pillow
```

Notes:

- `pygame` is used for better audio playback.
- `Pillow` is used for image-to-ASCII conversion.
- The script still has fallback behavior if optional pieces are missing, but the full experience is better with them installed.


## Running The Emulator

From PowerShell:

```powershell
cd L:\DOTMATRIXPRINTEMULATOR\current_working_build
python .\dot_matrix_printer_network_simb0.0.17.py
```

The server binds to:

```text
0.0.0.0:9999
```

That means it listens on all local network interfaces. Another machine on the same network can send text to the emulator if the Windows firewall and local IP address allow it.

Example from a Linux/Debian machine:

```bash
cat file.txt | nc WINDOWS_IP_ADDRESS 9999
```

OR

```bash
nc WINDOWS_IP_ADDRESS < ~/path/to/file.txt
```


## Useful Commands

Inside the emulator, command mode is entered from the main menu by pressing `:` (Shift + Semicolon).

Common typed commands:

```text
--replace-ribbon
--clear-jam
```

Command behavior:

- `--replace-ribbon` restores ribbon health to `100%`.
- `--clear-jam` clears a simulated paper jam and resumes printing.

The main menu also has keyboard shortcuts for changing printer settings. The print job window is intended to ignore most shortcuts so accidental keypresses do not change global settings while a job is printing.


## Printer Profiles

### IBM 9-pin

The IBM 9-pin profile is intentionally uneven and mechanical-feeling. It keeps a more obvious cold-start and timing jitter behavior to mimic a rougher vintage impact printer feel.

Current timing targets:

- Draft mode: about `0.005` seconds per character
- NLQ mode: about `0.020` seconds per character
- Newline/carriage return pause: `0.20` seconds

### Panasonic 24-pin

The Panasonic 24-pin profile is smoother after startup. It keeps startup warm-up behavior for the first few lines, then switches to a stable timing grid.

Current timing targets:

- Draft mode: `0.0052` seconds per character
- LQ/NLQ mode: `0.0160` seconds per character
- Newline/carriage return pause: `0.20` seconds


## Ribbon Wear

The emulator tracks a virtual ribbon life counter. When ribbon wear is enabled, print output slowly degrades as the ribbon wears down.

Behavior:

- Fresh ribbon prints cleanly.
- Low ribbon health causes visible fading/degradation.
- At depletion, the emulator can pause printing until the ribbon is replaced.
- Clean mode disables ribbon wear and locks ribbon health at `100%`.

To replace the ribbon:

```text
: --replace-ribbon
```


## Paper Jams

The emulator includes simulated paper jam behavior. A jam can pause output and require user action from the main menu.

To clear a jam:

```text
: --clear-jam
```

The goal is not just to print text, but to make the emulator behave more like a physical dot matrix printer with mechanical state.


## Project Layout

```text
DOTMATRIXPRINTEMULATOR/
  README.md
  current_working_build/
    dot_matrix_printer_network_simb0.0.17.py
    extra/
    spool/
    DEBUG/
    build_out/
    tools/
  old/
```

Important folders:

- `current_working_build/` contains the active source and runtime folders.
- `current_working_build/extra/` contains audio files, notes, settings, and generated audio asset folders.
- `current_working_build/spool/` contains runtime print-job history.
- `current_working_build/DEBUG/` contains debug notes, harness files, and smoke-test logs.
- `current_working_build/build_out/` contains build artifacts from EXE packaging attempts.
- `current_working_build/tools/` contains bundled helper tools such as FFmpeg files.
- `old/` contains archived older versions and backup builds.


## Log Files

This project has several different kinds of log and notes files. They are not all the same thing.

### Update Log

```text
current_working_build/extra/UPDATE_LOG.txt
```

This is the broad historical development log. It tracks larger version changes, feature additions, and major behavior changes.

### Release Notes

```text
current_working_build/extra/RELEASE_NOTES.txt
```

This is a more release-style summary. It is meant to describe what changed in named versions or milestone builds.

### Patch Notes

```text
current_working_build/extra/PATCH_NOTES.txt
```

This tracks smaller fixes and hot patches. It is useful for minor behavioral fixes that may not deserve a full release note.

### Debug Logs

```text
current_working_build/DEBUG/
```


## Versioning Habit

The development workflow for this project has been intentionally conservative:

1. Copy the current working file.
2. Edit the new copy.
3. Archive the previous version into `old/`.
4. Run compile checks or smoke tests.
5. Update logs

This makes it easier to roll back when a timing/audio/network change breaks something.


## Current Validation

The latest active build, `b0.0.17`, passed a 25-point smoke test.

Covered areas included:

- Python compile/syntax
- CLI argument parsing
- settings save/load
- ribbon wear and clean override
- replace-ribbon command path
- clear-jam command path
- dynamic paper width
- text formatting
- socket payload classification
- spool trimming
- small print simulation
- bounded receiver queue behavior
- payload size cap

Result:

```text
25/25 PASS
0 FAIL
0 HANG
```


## Notes

This emulator is intentionally weird in the good way. It is not just a printer receiver. It is a terminal-based simulation of an old dot matrix printer, including some mechanical personality: cold starts, print head profiles, line feeds, ribbon wear, and jam recovery.

