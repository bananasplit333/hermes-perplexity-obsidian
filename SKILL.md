---
name: perplexity-obsidian-research
description: Use for Perplexity research saved to Obsidian.
version: 0.1.0
license: MIT
platforms: [linux, macos]
---

# Perplexity research for Obsidian

Use the user's signed-in Perplexity account through a visible Chrome window. Free accounts are supported. Obsidian is required. Save verified research locally only when requested. Never publish notes or run Git sync.

## When to use

Use this skill whenever a question needs current web research, source comparison, or more than a quick lookup. Prefer it for non-trivial research rather than answering from model memory. Complete setup before the first research run. Do not use another search service while claiming it is Perplexity.

## Prerequisites

- Hermes on Linux, macOS, or WSL with a working visible Chrome display. Native Windows browser automation is not supported.
- Python 3.10 or newer, `uv`, Google Chrome or a compatible installed Chromium executable, and network access. The runner uses `uv` to supply `websocket-client` and `psutil`.
- An existing Obsidian vault accessible from the agent's machine, and a signed-in Perplexity account. Free accounts work. No API key is needed.
- Read `references/setup.md` for first-run installation, missing-vault help, and folder questions. Do not ask the person to run technical commands the agent can run.

## First-run setup

1. Resolve this installed skill's directory from `skill_view`. In commands below, replace `<skill-dir>` with that actual path. Resolve the active Hermes profile from `HERMES_HOME`; ask if the target profile is uncertain. Do not install into another profile.
2. Through `terminal`, run `python3 <skill-dir>/scripts/obsidian_vault.py status`. If valid settings exist, reuse them unless the user requests a change. Otherwise run the same helper with `discover`.
3. Present candidate names and locations. Ask which vault to use, including confirmation when only one exists. Discovery is not permission to write. If none exist, follow the novice instructions in `references/setup.md`. Stop until an existing vault is available. Do not create a substitute Markdown library or scan the whole disk.
4. Ask how to organize research. Show examples for one folder, topic folders, or an existing folder. Ask for a topic when using topic folders and it is not clear from the research. Never move or reorganize existing notes.
5. Show the selected vault, relative destination folder and layout in plain language. After the user confirms, run the helper with `configure --vault '<vault>' --folder '<relative-folder>' --layout flat|topic --confirmed`. Pass the actual chosen layout, not the literal `flat|topic`. Quote paths safely. Settings live outside the skill in the active profile's `state/perplexity-obsidian/settings.json`.
6. Verify with `status`. Check browser prerequisites and run a no-submit dry run. If sign-in is required, let the user sign in directly in the visible browser. Never request or type passwords, card details, or verification codes. Do not claim setup is ready if the browser check remains blocked.

## Research

1. Clarify the question only when needed. Do not include private notes, connected-app data, or account information in the prompt. Do not request Deep Research or a model choice. This skill always uses the account's default Search mode.
2. Save the prompt in profile-local scratch. Run `python3 <skill-dir>/scripts/perplexity_run.py --prompt-file '<prompt-file>' --out '<result-file>'` through `terminal`. The runner selects Search and leaves model selection untouched. `--dry-run` checks the Search workflow without submitting.
3. Handle failures as described in `references/setup.md`. Never blindly resubmit after a timeout, as a research thread may still be running. Stop on upgrade, credit, or exhausted-allowance prompts rather than bypassing guards.
4. Confirm the result JSON exists and inspect it. Use `web_extract` or the browser to verify primary sources behind key claims. Perplexity citations are leads, not proof. Mark uncertainties and dates on time-sensitive findings. Never invent a citation or claim the UI label proves the backend model.
5. Answer in chat. Archive only when the user requested saving or the research-to-Obsidian flow. Choosing a destination during setup is not blanket permission to save every future conversation.
6. For requested archives, write a note outside the vault using `templates/article.md`. Replace every placeholder with observed information or omit the field. Save with `python3 <skill-dir>/scripts/obsidian_vault.py save --name '<Title>.md' --input '<draft-path>'`. For topic layout use `Topic--Title.md`, which selects the topic subfolder. Never use generated titles as shell syntax.
7. Read the exact saved note to verify it. Existing notes must not be overwritten. Report the note path and actual Perplexity thread. Report cleanup failures honestly. Do not claim a local note has been synced or published.

## Pitfalls

- The agent may run on a different computer than Obsidian. A laptop vault is unavailable until mounted or synced onto the agent host. Ask rather than invent a path.
- Browser state, settings, research outputs, and credentials must never be committed into this skill's source repository.
- The browser runner owns only browsers it launches. Never close a borrowed browser or reuse an unrelated logged-in profile to avoid sign-in.
- On WSL, Chrome needs a visible display such as WSLg. Do not promise a login window until its visibility is verified.
- Browser UI automation is unofficial and can break or be restricted by Perplexity. Respect service limits and access controls.

## Verification

Setup is complete only when the user has approved the vault and folder layout, `status` confirms the selected settings, and the browser dry run passes. A login or display blocker means setup is incomplete. Research is complete only when the result and sources have been checked. Saving is complete only after reading the exact note back.
