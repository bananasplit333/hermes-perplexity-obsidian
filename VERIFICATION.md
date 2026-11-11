# Release verification

## Executed on Linux / WSL

- Test suite passed with 31 tests and 17 subtests.
- Installed the complete skill into an isolated Hermes home without touching the existing installation.
- Configured a temporary test vault and exercised safe note creation in tests.
- Started the installed research runner against the real Perplexity page. It correctly stopped at sign-in with exit 3. No research was submitted.
- Launched Chrome on a dedicated loopback port, checked ownership in a separate process, closed it, and confirmed an empty session registry.
- Fixed a Linux process identity issue found during live testing. Linux now uses stable kernel start ticks rather than wall-clock-derived process timestamps. A regression test covers clock changes.

## Simulated platform checks

- macOS Chrome application discovery.
- macOS Obsidian registry discovery and configuration without Linux `/proc` access.
- WSL Windows-drive path conversion.

These tests run on Linux with controlled platform inputs. They are not a live macOS certification.

## Not verified in this release environment

- A signed-in Perplexity research submission or complete Deep Research dry run.
- Copy extraction against a completed live research answer after clipboard isolation changes.
- A live native macOS Chrome session.

Each installing agent must run the setup check and report any login, service-UI, or OS-specific blocker. Do not describe these unverified paths as tested.
