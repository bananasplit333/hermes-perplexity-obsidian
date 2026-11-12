# Perplexity research for Obsidian

A Hermes skill that researches through a signed-in Perplexity account, checks sources, and saves requested research into your existing Obsidian notes. Free accounts use Search with the default model. Deep Research and model selection are not available in this workflow.

**Obsidian is required. Browser automation targets Linux, macOS, and Windows through WSLg, not native Windows.** Hermes must be able to access the vault and show you a Chrome window for sign-in. A remote server cannot automatically access notes on your laptop.

## Give this to your Hermes agent

Copy this message into Hermes. You do not need to run terminal commands yourself.

> Set up https://github.com/bananasplit333/hermes-perplexity-obsidian for my current Hermes profile. Read the repository and follow its setup guide. Check prerequisites, install without replacing existing skills, and find my existing Obsidian vaults. Ask which vault to use and how I want research folders organized. If you cannot find my notes, help me locate them. Obsidian is required. Let me sign into Perplexity directly in the browser, then run the no-submit setup check. Do not move notes, publish them, or configure Git sync. Tell me what passed and anything still blocking setup.

The agent handles discovery and commands. You choose where notes go and sign in yourself. No passwords or verification codes belong in chat.

## What happens during setup

1. The agent checks the machine where Hermes runs.
2. It looks for existing Obsidian vaults without reading your notes.
3. You select a vault. If none are found, it helps you locate one.
4. You choose one research folder, topic folders, or an existing destination.
5. It saves those choices locally and checks the Perplexity browser without submitting research.

No Obsidian yet? The agent helps you get it set up first. This version does not substitute an ordinary Markdown directory.

## Requirements

- Hermes on Linux, macOS, or WSL with a visible Chrome display.
- Python 3.10+, `uv`, Chrome, and network access. The browser runner fetches `websocket-client` and `psutil` through `uv`.
- An existing accessible Obsidian vault and a signed-in Perplexity account. Free accounts work.

The vault helper includes discovery paths for Linux, Windows, macOS, and WSL. Native Windows browser operation is not supported. macOS-specific code is covered by simulated tests; a live Mac browser test is still required on the installing machine. Linux browser checks and Mac simulation results are reported separately.

## Manual installation for agents

Clone this repository into a source folder, confirm the target Hermes profile, then run:

```sh
python3 install.py --hermes-home '<confirmed Hermes profile directory>'
```

For example, a default profile often uses `~/.hermes`, but a named profile has a different directory. Do not guess. The installer refuses an existing skill destination.

Read [the skill](SKILL.md) and [setup guide](references/setup.md) after installation. Refresh the Hermes session if the skill has not appeared yet.

## Commands

Run from this repository or use the corresponding installed script paths:

```sh
python3 scripts/obsidian_vault.py discover
python3 scripts/obsidian_vault.py status
python3 scripts/obsidian_vault.py --help
python3 scripts/perplexity_run.py --help
python3 scripts/perplexity_run.py --prompt 'Setup check' --dry-run
python3 scripts/perplexity_run.py --prompt-file question.txt --out result.json
```

Free accounts use Search with the default model. The runner does not offer Deep Research or model selection. `--dry-run` checks the Search workflow without submitting a question. Actual research can consume the account's available allowance. Saving is a separate step, performed only when requested.

## Privacy and limits

Settings, browser profiles and research output stay in your active Hermes profile, outside this repository. Browser profiles contain login cookies. Do not commit or share them, and exclude them from public backups. During sign-in, a loopback browser-control port remains open; other processes running as you can access it. Close the setup session when abandoning login. Existing notes are not overwritten. No Git sync or note publishing is included. The browser lifecycle tracks owned windows and does not close borrowed ones.

Perplexity UI automation is unofficial. Its selectors, plan limits, and service rules can change. Stop on upgrade or credit prompts. Do not use this tool to bypass access restrictions. Sources still need to be checked before relying on an answer.

## Development checks

```sh
uv run --no-project --with websocket-client --with psutil --with pytest python -m pytest tests -q
```

Tests use temporary vaults and isolated settings. A passing unit suite does not prove a user's live Perplexity account is ready. Each installation needs the browser setup check.

Licensed under MIT. This is an independent project, not affiliated with Perplexity or Obsidian.
