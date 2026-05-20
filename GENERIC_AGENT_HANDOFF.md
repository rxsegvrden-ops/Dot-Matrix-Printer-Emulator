# Generic Agent Handoff Notes

This file is meant to give any coding agent the important project context without needing the full chat history.

## User Style and Workflow

- The user wants practical help, not long theory.
- When the user asks for code work, usually make the change instead of only describing it.
- Be careful with working files. The user strongly prefers copying before editing.
- For the dot matrix emulator, old versions usually go into an `old` folder after a new version is promoted.
- If the user says debugging only, do not edit code. Run checks and report results.
- If a change breaks unrelated working behavior, revert to the last known good file state.
- Keep edits narrow. Do not refactor unrelated code.
- The user likes plain text notes, patch notes, release notes, and short validation summaries.

## Main Dot Matrix Emulator Project

Protected original folder:

```text
L:\DOTMATRXEMU
```

Do not edit this folder unless the user explicitly says to. Treat it as the older protected/original backup.

Current active project folder:

```text
L:\DOTMATRIXPRINTEMULATOR
```

Current active build file:

```text
L:\DOTMATRIXPRINTEMULATOR\current_working_build\dot_matrix_printer_network_simb0.0.17.py
```

Archive folder:

```text
L:\DOTMATRIXPRINTEMULATOR\old
```

Current known state:

- Latest active version is `b0.0.17`.
- A 25-point smoke test was run against `b0.0.17`.
- Result was `25/25 PASS`, `0 FAIL`, `0 HANG`.
- The longest smoke check was the small print simulation at about 7.55 seconds.

## Emulator Behavior to Preserve

- IBM 9-pin keeps uneven/cold-start/jitter timing behavior.
- Panasonic 24-pin has startup warm-up only before line 12.
- Panasonic 24-pin then uses smooth fixed grid timing:
  - Draft: `0.0052` seconds per character.
  - LQ/NLQ: `0.0160` seconds per character.
  - Newline/carriage return pause: `0.20` seconds.
- Keyboard shortcuts should only work in the main menu window.
- Print windows should ignore menu hotkeys.
- Ribbon wear `Clean` mode should force ribbon health to 100 percent and prevent depletion popups.
- Do not touch audio/timing/ribbon/menu behavior unless the user specifically asks.

## Emulator Features Already Added

- Network socket print receiver.
- Separate print job windows.
- Central main menu window.
- Tractor paper terminal layout.
- Continuous paper stack/page tracking.
- Manual line feed and top-of-form controls.
- Persistent `.sep` settings file.
- Sound profiles and dot matrix audio handling.
- IBM 9-pin and Panasonic 24-pin head profiles.
- Ribbon wear/degradation.
- `--replace-ribbon` command.
- Paper jam behavior.
- `--clear-jam` command.
- Image/text payload detection at the socket layer.
- Image-to-ASCII conversion.
- Debug harness and smoke tests.


Folders/files to ignore for GitHub:

```gitignore
old/
__pycache__/
.codex/
current_working_build/__pycache__/
current_working_build/.venv/
current_working_build/build_out/
current_working_build/spool/
current_working_build/tmp_sound_extract/
current_working_build/dot_matrix_source_pack.zip
current_working_build/extra/assets/
current_working_build/extra/__print_variants/
current_working_build/extra/__linefeed_variants/
current_working_build/extra/dot_matrix_printer_settings.sep
```




## Validation Already Done

Dot matrix emulator:

- Target:

```text
L:\DOTMATRIXPRINTEMULATOR\current_working_build\dot_matrix_printer_network_simb0.0.17.py
```

- Result: `25/25 PASS`, `0 FAIL`, `0 HANG`.

Web/profile files:

- XML/profile files were checked for ASCII-only in earlier passes.
- `info page_textswap.html` parsed successfully with Python `HTMLParser`.
- The copied HTML style block matched the original.

## Caution List

- Do not over-edit the emulator.
- Do not touch `L:\DOTMATRXEMU` unless explicitly authorized.
- Do not upload generated audio caches to GitHub.
- Do not change emulator audio/timing/ribbon/menu behavior unless asked.
- If asked to debug only, do not edit files.
- If asked to make a new emulator version, copy first, edit the copy, then archive the prior active version.
- If editing HTML copies, preserve CSS/layout unless explicitly told to change formatting.


