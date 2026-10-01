# Desktop browser verification — partial success, not a clean exit

2026-10-01 UTC. This is a factual verification summary, not a fabricated terminal transcript.

Ran installed Google Chrome headlessly against the local `project-assignment2.html` using a new temporary `--user-data-dir`, a 1440 × 1100 window, `--virtual-time-budget=2000`, `--dump-dom`, and `--screenshot`. Other flags disabled first-run prompts, extensions, GPU use, and background networking. The real user profile was not accessed and the browser sandbox was **not** disabled.

The command exceeded its 60-second timeout, **after** it produced both the screenshot and complete DOM. Captured artifacts:

- `browser-desktop.png`: actual initial-viewport screenshot, visually inspected for readable layout and navigation.
- `browser-desktop-dom.txt`: raw rendered DOM, saved as text rather than another runnable site page.
- `browser-stderr.txt`: original stderr, including macOS display-link/allocator warnings and confirmation of the screenshot write.

An HTML parser checked the captured rendered DOM: the active links were exactly `project.html` and `project-assignment2.html`, demonstrating that both shared navigation scripts executed and selected the correct entries. The plot's standalone exported PNG was inspected separately.

Do **not** interpret this as a clean browser-process exit, a full-page visual inspection, a mobile-layout test, a zero-console-error test, or verification of the live GitHub Pages deployment. The complete local test suite separately checks generated table values, escaping, duplicate IDs, source hashes, CLI behavior, and local asset-link targets.
