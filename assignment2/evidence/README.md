# Execution evidence

These are captured command outputs from the actual implementation, not reconstructed examples. UTC timestamps and exit statuses are included.

- `00-setup-collection-error.txt`: initial editable-package setup failed to discover the package because the environment was built before the package files existed. Reinstalled only this editable project; this is **not** counted as the TDD red phase.
- `01-core-red.txt`: 37 behavior failures against explicit unimplemented contracts, after collection was fixed.
- `02-core-green.txt`: 37 passing tests after arithmetic, validation, and auditing were implemented.
- `03-figure-red.txt`: 4 new behavior failures; 38 tests passed. The independently transcribed XML source check already passed and is not claimed as an implementation red/green test.
- `04-figure-green.txt`: 42 passing tests after plotting/export implementation.
- `05-delivery-red.txt`: 4 new behavior failures; 43 tests passed. The source-acquisition hash/metadata check already passed.
- `06-delivery-green.txt`: all 47 tests passed after CLI/page implementation.
- `07-generate.txt`: offline regeneration of the outputs and portfolio page, exit 0.
- `08-paper-agreement.txt`: strict 0.1% paper check, **expected exit 1**, with four failed comparisons listed.
- `09-clean-environment.txt`: fresh ignored environment, locked/offline sync, all 47 tests passed.
- `10-browser-check.md`, `browser-desktop.png`, `browser-desktop-dom.txt`, `browser-stderr.txt`: partial browser verification. DOM and screenshot were obtained and inspected, but the browser command timed out after producing them; this is not a clean browser-test pass.
- `11-final-verification.txt`: 47 passing tests, locked/offline lockfile validation, generated-page freshness, audit/output/code hashes, and valid portfolio devlog JSON.

A nonzero **paper check** is not a software-test failure: the 0.1% audit deliberately rejects inconsistent published values. Read `../outputs/audit.json` for individual comparisons, not only a suite summary.
