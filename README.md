# vman — your personal CLI man page

*A writeable, searchable, beautiful CLI notebook for the exact commands **you** use — organized by tool, tagged, and instantly insertable into your shell prompt.*

- **Writeable**: save your own commands/snippets with descriptions  
- **Searchable**: find by tool, tag, description, command name, or snippet text  
- **Fast entry**: `wizard`, `use` + `cmd`, quick one-liners (`qtool`, `qcmd`), TOML import  
- **Pretty output**: tables & syntax highlighting (Rich)  
- **Prompt insertion**: drop a snippet directly into your zsh prompt (edit placeholders, then hit Enter)  
- **Local & simple**: single Python file + SQLite DB

---

## Table of contents

- [Why vman?](#why-vman)
- [Install](#install)
- [Quick start](#quick-start)
- [Concepts](#concepts)
- [Core commands](#core-commands)
- [Convenience commands (speedy entry)](#convenience-commands-speedy-entry)
- [Search](#search)
- [Run / print snippets](#run--print-snippets)
- [Prompt insertion (zsh)](#prompt-insertion-zsh)
- [Bulk import / export](#bulk-import--export)
- [Config & storage](#config--storage)
- [Troubleshooting](#troubleshooting)
- [Roadmap / ideas](#roadmap--ideas)
- [License](#license)

---

## Why vman?

`man` / `--help` explain what a tool *can* do. **vman** remembers what *you actually do* — the exact commands, flags, paths, and notes that work in your environment or team.

It **complements** `man`, `tldr`, and your dotfiles: use those to learn or look up syntax; use vman to **save, search, and reuse** your personal recipes.

---

## Install

> **Requirements:** Python 3.11+ recommended (3.8+ works; install `tomli` on <3.11). macOS/Linux, zsh or bash.

1. **Place the app**
   ```bash
   mkdir -p ~/vman
   # put vman.py in ~/vman
   ```

2. **Create a virtualenv & install deps**
   ```bash
   python3 -m venv ~/vman/venv
   ~/vman/venv/bin/pip install 'typer[all]' rich sqlite-utils tomli
   ```

3. **Add a tiny launcher** so `vman` works from anywhere
   ```bash
   mkdir -p ~/bin
   cat > ~/bin/vman <<'SH'
   #!/usr/bin/env bash
   set -euo pipefail
   APP="$HOME/vman/vman.py"
   PY="$HOME/vman/venv/bin/python"
   exec "$PY" "$APP" "$@"
   SH
   chmod +x ~/bin/vman
   ```

4. **Put `~/bin` on your PATH**
   ```bash
   # zsh:
   echo 'export PATH="$HOME/bin:$PATH"' >> ~/.zshrc
   # (optional) for login shells (tmux):
   echo 'export PATH="$HOME/bin:$PATH"' >> ~/.zprofile
   source ~/.zshrc
   ```

5. **(tmux) ensure login shells** (so PATH applies)
   ```bash
   echo 'set -g default-command "${SHELL} -l"' >> ~/.tmux.conf
   tmux kill-server  # then open a fresh tmux session
   ```

> If you previously made an alias like `alias vman=/path/to/vman.py`, remove it (`unalias vman`), or it will shadow the launcher.

---

## Quick start

```bash
# Create your first tool and set it as context
vman use docker -d "Containers" -t dev -t ops

# Add a few commands
vman cmd compose-up -d "Start services (detached)" -r "docker compose up -d"
vman cmd build      -d "Build image in CWD"       -r "docker build -t <image>:<tag> ."

# Browse / search
vman show docker
vman search "build" -T docker

# Drop a snippet into your prompt to edit & run
# (see 'Prompt insertion' below to enable vmip / Alt+v)
vmip docker build
```

---

## Concepts

- **Tool**: a CLI program (e.g., `git`, `docker`). Has a name, description, and tags.  
- **Command**: a subcommand or action of a tool with a description and a **snippet** (the actual line to run).  
- **Context**: the “current tool” set by `vman use`, so you can add commands without repeating the tool name.  
- **DB**: a local SQLite file (default `~/.myman.db`).

---

## Core commands

```bash
# Tools
vman add-tool <tool> -d "DESC" -t tag1 -t tag2   # create/update a tool
vman list                                         # list tools
vman list -t <tag>                                # filter by tag
vman tags                                         # show tags + counts
vman show <tool>                                  # full details + all commands
vman rm-tool <tool>                               # delete a tool (and its commands)

# Commands
vman add-cmd <tool> <name> -d "DESC" -r "SNIPPET" # add/update a command
vman rm-cmd  <tool> <name>                        # delete one command

# Search (see more below)
vman search "<text>"                              # search across tools & commands
```

---

## Convenience commands (speedy entry)

These cut the typing dramatically.

```bash
# Set / create a tool and make it default context
vman use <tool> -d "DESC" -t tag1 -t tag2

# Add/update a command for the current tool
vman cmd <name> -d "DESC" -r "SNIPPET"
vman cmd <name> --clip          # paste snippet from clipboard (macOS)
vman cmd <name> -T <tool> ...   # override tool

# Interactive wizard: create a tool then loop to add commands
vman wizard
vman wizard -T <tool>

# One-liners
vman qtool "curl: HTTP client #http,net"
vman qcmd  "curl.get: Simple GET | curl -s https://example.com"

# Export your library to Markdown
vman export-md ~/Desktop/vman.md
```

---

## Search

Flexible search across tools & commands, with useful filters.

```bash
vman search "<text>"                    # search everywhere (names/descriptions/snippets)
vman search -T <tool>                   # list all commands for one tool
vman search -T <tool> -C <substring>    # match command name (substring)
vman search -T <tool> -C <name> --exact # exact command name
vman search "<text>" -t <tag>           # also filter by tag
```

- Matches: tool **name/description**, command **name/description/snippet** (SQLite `LIKE`).

---

## Run / print snippets

By default, **`vman run` prints the snippet** (so you can edit placeholders) and **does not execute**. Add `--exec` to run.

```bash
# Print the snippet (default)
vman run docker compose-up

# Copy to clipboard too
vman run docker compose-up --copy

# Pretty preview (panel + highlighted code), still does not run
vman run docker compose-up --preview --no-raw

# Actually execute (with confirm)
vman run docker compose-up --exec

# Execute immediately (no prompt)
vman run docker compose-up -x -y
```

**Flags**
- `--exec, -x` run the snippet  
- `--yes, -y` skip confirmation when executing  
- `--copy, -c` copy snippet to clipboard (macOS `pbcopy`)  
- `--preview` show a nice panel before printing/executing  
- `--raw/--no-raw` print raw vs. highlighted block  
- `--shell` choose the shell to execute under (default `/bin/zsh`)

---

## Prompt insertion (zsh)

Want the snippet to **appear in your prompt**, ready to edit & run (not executed yet)? Add this zsh integration.

> This uses a ZLE widget; it can insert interactively or directly by tool+cmd.

**Add to `~/.zshrc`:**
```zsh
# --- vman ➜ insert snippet into the prompt (editable, not run) ---
_vman_ctx_file=${MYMAN_CONTEXT_FILE:-$HOME/.myman.context}

vman-insert-widget() {
  emulate -L zsh
  local tool cmd snippet ctx
  zle -I
  [[ -r "$_vman_ctx_file" ]] && ctx=$(<"$_vman_ctx_file")
  vared -p "Tool (${ctx:-none}): " tool
  [[ -z "$tool" ]] && tool="$ctx"
  [[ -z "$tool" ]] && { zle -M "No tool specified."; return 1; }
  vared -p "Command: " cmd
  [[ -z "$cmd" ]] && { zle -M "No command specified."; return 1; }
  snippet="$(vman run "$tool" "$cmd" --raw)" || { zle -M "Not found: $tool $cmd"; return 1; }
  BUFFER="$snippet"; CURSOR=${#BUFFER}; zle redisplay
}
zle -N vman-insert-widget
bindkey '^[v' vman-insert-widget   # Alt+v

# Convenience: vmip <tool> <cmd> (insert without prompts)
vmip() {
  local tool="$1" cmd="$2" s
  [[ -z "$tool" || -z "$cmd" ]] && { echo "usage: vmip <tool> <cmd>"; return 1; }
  s="$(vman run "$tool" "$cmd" --raw)" || return
  if zle >/dev/null 2>&1; then LBUFFER+="$s"; CURSOR=${#LBUFFER}; else print -z -- "$s"; fi
}
```

Reload your shell:
```bash
source ~/.zshrc
```

Usage:
```bash
# Interactive: press Alt+v and follow the prompts
# Direct:
vmip docker build
```

---

## Bulk import / export

**Export everything to Markdown**
```bash
vman export-md ~/Desktop/vman.md
```

**TOML template**
```bash
vman template-toml > tools.toml
# edit tools.toml, then:
vman import-toml tools.toml   # Python 3.11+ or `pip install tomli`
```

**Sample `tools.toml`:**
```toml
title = "vman import"

[[tools]]
name = "docker"
description = "Containers"
tags = ["dev","ops"]

  [[tools.commands]]
  name = "compose-up"
  description = "Create and start services (detached)."
  snippet = "docker compose up -d"
```

---

## Config & storage

- **Database file**  
  Default: `~/.myman.db`  
  Override per shell/session or in your launcher:
  ```bash
  export MYMAN_DB="$HOME/vman/vman.db"
  ```

- **Context file**  
  Default: `~/.myman.context`  
  Override:
  ```bash
  export MYMAN_CONTEXT_FILE="$HOME/.config/vman/context"
  ```

- **Backups / sync**  
  - Keep the DB in iCloud/Dropbox by pointing `MYMAN_DB` to a synced path.  
  - Or just `vman export-md` and commit/share the Markdown.

---

## Troubleshooting

- **`vman: command not found`**  
  Ensure `~/bin` is on PATH and the launcher exists & is executable.

- **It runs the `.py` as a shell script (you see ImageMagick “import” help)**  
  Remove old aliases (`unalias vman`). Always call through the launcher.

- **`ModuleNotFoundError: No module named 'typer'`**  
  You’re not using the venv. Install deps into `~/vman/venv` and ensure the launcher points to `~/vman/venv/bin/python`.

- **PEP 668 / externally-managed environment**  
  venv avoids this. Don’t install system-wide; keep it in `~/vman/venv`.

- **tmux doesn’t see `vman`**  
  Use a login shell: `set -g default-command "${SHELL} -l"` in `~/.tmux.conf`, then `tmux kill-server`.

- **zsh `compdef` error when sourcing `~/.zshrc`**  
  Add (once):
  ```zsh
  if ! typeset -f compdef >/dev/null; then autoload -Uz compinit; compinit; fi
  ```

---

## Roadmap / ideas

- Fuzzy search (RapidFuzz)  
- Placeholder substitution: `vman run <tool> <cmd> --set name=value`  
- Clipboard-only helper: `vman copy <tool> <cmd>`  
- YAML import/export  
- TUI view (Textual) for browsing & editing

---

## License

MIT. See `LICENSE`.
