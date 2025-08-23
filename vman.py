#!/usr/bin/env python3
# vman.py — your personal, pretty CLI man page
from __future__ import annotations

import os
import sqlite3
import textwrap
import shutil
import subprocess
import tempfile
import sys
from pathlib import Path
from typing import Optional, List, Tuple

import typer
from rich.console import Console
from rich.table import Table
from rich.panel import Panel
from rich.syntax import Syntax
from rich.rule import Rule

# ------------------------------------------------------------
# App setup
# ------------------------------------------------------------
app = typer.Typer(add_completion=False, help="Your personal, pretty, CLI man page.")
console = Console()

DB_PATH = Path(os.environ.get("MYMAN_DB", Path.home() / ".myman.db"))

SCHEMA = """
PRAGMA foreign_keys=ON;
CREATE TABLE IF NOT EXISTS tools(
  id INTEGER PRIMARY KEY,
  name TEXT UNIQUE NOT NULL,
  description TEXT
);
CREATE TABLE IF NOT EXISTS tags(
  id INTEGER PRIMARY KEY,
  name TEXT UNIQUE NOT NULL
);
CREATE TABLE IF NOT EXISTS tool_tags(
  tool_id INTEGER NOT NULL REFERENCES tools(id) ON DELETE CASCADE,
  tag_id INTEGER NOT NULL REFERENCES tags(id) ON DELETE CASCADE,
  UNIQUE(tool_id, tag_id)
);
CREATE TABLE IF NOT EXISTS commands(
  id INTEGER PRIMARY KEY,
  tool_id INTEGER NOT NULL REFERENCES tools(id) ON DELETE CASCADE,
  name TEXT NOT NULL,
  description TEXT,
  snippet TEXT,
  UNIQUE(tool_id, name)
);
"""

def db() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.execute("PRAGMA foreign_keys=ON;")
    return conn

def ensure_schema():
    with db() as conn:
        conn.executescript(SCHEMA)

def ensure_tool(conn: sqlite3.Connection, name: str, description: str = "") -> int:
    cur = conn.execute("SELECT id FROM tools WHERE name = ?", (name,))
    row = cur.fetchone()
    if row:
        if description:
            conn.execute("UPDATE tools SET description=? WHERE id=?", (description, row[0]))
        return row[0]
    cur = conn.execute("INSERT INTO tools(name, description) VALUES(?, ?)", (name, description))
    return cur.lastrowid

def ensure_tag(conn: sqlite3.Connection, tag: str) -> int:
    cur = conn.execute("SELECT id FROM tags WHERE name = ?", (tag,))
    row = cur.fetchone()
    if row:
        return row[0]
    cur = conn.execute("INSERT INTO tags(name) VALUES(?)", (tag,))
    return cur.lastrowid

def attach_tags(conn: sqlite3.Connection, tool_id: int, tags: List[str]):
    for t in tags:
        t = t.strip()
        if not t:
            continue
        tid = ensure_tag(conn, t)
        conn.execute("INSERT OR IGNORE INTO tool_tags(tool_id, tag_id) VALUES(?, ?)", (tool_id, tid))

def get_tool_id(conn: sqlite3.Connection, name: str) -> Optional[int]:
    row = conn.execute("SELECT id FROM tools WHERE name=?", (name,)).fetchone()
    return row[0] if row else None

# ------------------------------------------------------------
# Initialize schema on any run
# ------------------------------------------------------------
@app.callback()
def _init():
    ensure_schema()

# ------------------------------------------------------------
# Core commands
# ------------------------------------------------------------
@app.command("add-tool")
def add_tool(
    name: str = typer.Argument(..., help="Tool name, e.g., 'ironclad'"),
    description: str = typer.Option("", "--desc", "-d", help="Short description"),
    tags: List[str] = typer.Option([], "--tag", "-t", help="Tags (repeatable)"),
):
    """Add or update a tool."""
    with db() as conn:
        tool_id = ensure_tool(conn, name, description)
        attach_tags(conn, tool_id, tags)
        conn.commit()
    console.print(Panel.fit(f"[bold]{name}[/] added/updated."))

@app.command("add-cmd")
def add_cmd(
    tool: str = typer.Argument(..., help="Tool name to attach to"),
    name: str = typer.Argument(..., help="Subcommand/verb, e.g., 'init', 'list'"),
    description: str = typer.Option("", "--desc", "-d", help="What it does"),
    snippet: str = typer.Option("", "--run", "-r", help="Command line snippet"),
):
    """Add or update a command for a tool."""
    with db() as conn:
        tool_id = get_tool_id(conn, tool)
        if not tool_id:
            raise typer.Exit(f"Tool '{tool}' not found. Add it first with add-tool.")
        existing = conn.execute("SELECT id FROM commands WHERE tool_id=? AND name=?", (tool_id, name)).fetchone()
        if existing:
            conn.execute("UPDATE commands SET description=?, snippet=? WHERE id=?", (description, snippet, existing[0]))
        else:
            conn.execute(
                "INSERT INTO commands(tool_id, name, description, snippet) VALUES(?,?,?,?)",
                (tool_id, name, description, snippet),
            )
        conn.commit()
    console.print(Panel.fit(f"[bold]{tool}[/] · command [bold]{name}[/] added/updated."))

@app.command("list")
def list_tools(tag: Optional[str] = typer.Option(None, "--tag", "-t", help="Filter by tag")):
    """List tools (optionally by tag)."""
    with db() as conn:
        if tag:
            rows = conn.execute(
                """
                SELECT tools.name, tools.description
                FROM tools
                JOIN tool_tags ON tool_tags.tool_id = tools.id
                JOIN tags ON tags.id = tool_tags.tag_id
                WHERE tags.name = ?
                ORDER BY tools.name
                """,
                (tag,),
            ).fetchall()
        else:
            rows = conn.execute("SELECT name, description FROM tools ORDER BY name").fetchall()

    table = Table(title="Tools", show_lines=False)
    table.add_column("Tool", style="bold")
    table.add_column("Description", overflow="fold")
    for name, desc in rows:
        table.add_row(name, desc or "")
    console.print(table)

@app.command("tags")
def list_tags():
    """Show all tags and tool counts."""
    with db() as conn:
        rows = conn.execute(
            """
            SELECT tags.name, COUNT(tool_tags.tool_id)
            FROM tags LEFT JOIN tool_tags ON tags.id = tool_tags.tag_id
            GROUP BY tags.id
            ORDER BY tags.name
            """
        ).fetchall()
    table = Table(title="Tags", show_lines=False)
    table.add_column("Tag", style="bold")
    table.add_column("Tools", justify="right")
    for t, c in rows:
        table.add_row(t, str(c))
    console.print(table)

@app.command("show")
def show_tool(name: str):
    """Show a tool with its tags and commands."""
    with db() as conn:
        tool = conn.execute("SELECT id, name, description FROM tools WHERE name=?", (name,)).fetchone()
        if not tool:
            raise typer.Exit(f"Tool '{name}' not found.")
        tool_id, tname, desc = tool
        tags = [
            r[0]
            for r in conn.execute(
                """
                SELECT tags.name
                FROM tags JOIN tool_tags ON tags.id=tool_tags.tag_id
                WHERE tool_tags.tool_id=? ORDER BY tags.name
                """,
                (tool_id,),
            ).fetchall()
        ]
        cmds = conn.execute(
            "SELECT name, description, snippet FROM commands WHERE tool_id=? ORDER BY name",
            (tool_id,),
        ).fetchall()

    header = f"[bold]{tname}[/] — {desc}" if desc else f"[bold]{tname}[/]"
    console.print(Panel(header))
    if tags:
        console.print("[bold]Tags:[/]", ", ".join(tags))
    console.print(Rule("Commands"))
    if not cmds:
        console.print("No commands yet.")
        return
    for cname, cdesc, snip in cmds:
        console.print(f"[bold]{cname}[/]: {cdesc}")
        if snip:
            code = Syntax(snip, "bash", word_wrap=True)
            console.print(code)
        console.print()

@app.command("search")
def search(
    q: str = typer.Argument(..., help="Search text (name/desc/command)"),
    tag: Optional[str] = typer.Option(None, "--tag", "-t", help="Filter by tag"),
):
    """Search tools and commands."""
    like = f"%{q}%"
    with db() as conn:
        if tag:
            rows = conn.execute(
                """
                SELECT tools.name, COALESCE(commands.name, ''), COALESCE(commands.snippet, ''), COALESCE(commands.description, '')
                FROM tools
                JOIN tool_tags ON tool_tags.tool_id=tools.id
                JOIN tags ON tags.id=tool_tags.tag_id
                LEFT JOIN commands ON commands.tool_id=tools.id
                WHERE tags.name=? AND (
                    tools.name LIKE ? OR tools.description LIKE ? OR
                    commands.name LIKE ? OR commands.description LIKE ? OR commands.snippet LIKE ?
                )
                ORDER BY tools.name
                """,
                (tag, like, like, like, like, like),
            ).fetchall()
        else:
            rows = conn.execute(
                """
                SELECT tools.name, COALESCE(commands.name, ''), COALESCE(commands.snippet, ''), COALESCE(commands.description, '')
                FROM tools
                LEFT JOIN commands ON commands.tool_id=tools.id
                WHERE tools.name LIKE ? OR tools.description LIKE ?
                   OR commands.name LIKE ? OR commands.description LIKE ? OR commands.snippet LIKE ?
                ORDER BY tools.name
                """,
                (like, like, like, like, like),
            ).fetchall()

    if not rows:
        console.print("No results.")
        raise typer.Exit(0)

    table = Table(title=f"Search: {q}", show_lines=False)
    table.add_column("Tool", style="bold")
    table.add_column("Command")
    table.add_column("Summary")
    for tool, cname, snip, cdesc in rows:
        summary = cdesc or (snip[:60] + "…" if snip and len(snip) > 60 else snip)
        table.add_row(tool, cname, summary or "")
    console.print(table)

@app.command("rm-tool")
def rm_tool(name: str):
    """Delete a tool (and its commands)."""
    with db() as conn:
        conn.execute("DELETE FROM tools WHERE name=?", (name,))
        conn.commit()
    console.print(f"Removed tool: [bold]{name}[/].")

@app.command("rm-cmd")
def rm_cmd(tool: str, name: str):
    """Delete a command from a tool."""
    with db() as conn:
        conn.execute(
            """
            DELETE FROM commands
            WHERE id IN (
              SELECT commands.id
              FROM commands JOIN tools ON tools.id=commands.tool_id
              WHERE tools.name=? AND commands.name=?
            )
            """,
            (tool, name),
        )
        conn.commit()
    console.print(f"Removed [bold]{tool}[/] · command [bold]{name}[/].")

@app.command("export-md")
def export_md(output: Path = typer.Argument(..., help="Markdown file to write")):
    """Export your library to a single pretty Markdown file."""
    with db() as conn:
        tools = conn.execute("SELECT id, name, description FROM tools ORDER BY name").fetchall()
        with output.open("w", encoding="utf-8") as f:
            f.write("# My Personal Man Page\n\n")
            for tool_id, name, desc in tools:
                f.write(f"## {name}\n\n")
                if desc:
                    f.write(f"{desc}\n\n")
                tags = [
                    r[0]
                    for r in conn.execute(
                        """
                        SELECT tags.name
                        FROM tags JOIN tool_tags ON tags.id=tool_tags.tag_id
                        WHERE tool_tags.tool_id=? ORDER BY tags.name
                        """,
                        (tool_id,),
                    ).fetchall()
                ]
                if tags:
                    f.write(f"**Tags:** {', '.join(tags)}\n\n")
                cmds = conn.execute(
                    "SELECT name, description, snippet FROM commands WHERE tool_id=? ORDER BY name",
                    (tool_id,),
                ).fetchall()
                if not cmds:
                    f.write("_No commands yet._\n\n")
                else:
                    for cname, cdesc, snip in cmds:
                        f.write(f"### {cname}\n\n")
                        if cdesc:
                            f.write(f"{cdesc}\n\n")
                        if snip:
                            f.write("```bash\n")
                            f.write(snip.strip() + "\n")
                            f.write("```\n\n")
    console.print(Panel.fit(f"Exported to [bold]{output}[/]."))

# ------------------------------------------------------------
# Convenience features
# ------------------------------------------------------------
CONTEXT_PATH = Path(os.environ.get("MYMAN_CONTEXT_FILE", Path.home() / ".myman.context"))

def _set_context(name: str):
    CONTEXT_PATH.write_text((name or "").strip() + "\n", encoding="utf-8")

def _get_context() -> Optional[str]:
    try:
        val = CONTEXT_PATH.read_text(encoding="utf-8").strip()
        return val or None
    except FileNotFoundError:
        return None

def _split_tags(s: str) -> List[str]:
    # supports "#a,b" or "a, b"
    return [t.strip().lstrip("#") for t in s.split(",") if t.strip()]

@app.command("use")
def use_tool(
    name: str = typer.Argument(..., help="Tool to use by default"),
    description: str = typer.Option("", "--desc", "-d"),
    tags: List[str] = typer.Option([], "--tag", "-t"),
):
    """Create/update a tool and make it the default context."""
    with db() as conn:
        tid = ensure_tool(conn, name, description)
        if tags:
            attach_tags(conn, tid, tags)
        conn.commit()
    _set_context(name)
    console.print(Panel.fit(f"Default tool set to [bold]{name}[/]."))

@app.command("cmd")
def cmd_short(
    name: str = typer.Argument(..., help="Command name (e.g., init, list)"),
    description: str = typer.Option("", "--desc", "-d"),
    snippet: str = typer.Option("", "--run", "-r", help="Command to run"),
    tool: Optional[str] = typer.Option(None, "--tool", "-T", help="Override default tool"),
    clip: bool = typer.Option(False, "--clip", help="Use clipboard as snippet if --run empty (macOS pbpaste)"),
):
    """Add/update a command, preferring the default tool set via `vman use`."""
    t = tool or _get_context()
    if not t:
        raise typer.Exit("No default tool. Run: vman use <tool> [--desc ... --tag ...] OR pass --tool.")
    if clip and not snippet:
        try:
            snippet = subprocess.check_output(["pbpaste"]).decode()
        except Exception:
            raise typer.Exit("Clipboard read failed. Provide --run or remove --clip.")
    with db() as conn:
        tool_id = get_tool_id(conn, t)
        if not tool_id:
            raise typer.Exit(f"Tool '{t}' not found. Create it with: vman add-tool {t} --desc ...")
        existing = conn.execute("SELECT id FROM commands WHERE tool_id=? AND name=?", (tool_id, name)).fetchone()
        if existing:
            conn.execute("UPDATE commands SET description=?, snippet=? WHERE id=?", (description, snippet, existing[0]))
        else:
            conn.execute(
                "INSERT INTO commands(tool_id, name, description, snippet) VALUES(?,?,?,?)",
                (tool_id, name, description, snippet),
            )
        conn.commit()
    console.print(Panel.fit(f"Added/updated [bold]{t}[/] · command [bold]{name}[/]."))

@app.command("wizard")
def wizard(
    tool: Optional[str] = typer.Option(None, "--tool", "-T", help="Tool to edit (defaults to context or prompts)"),
):
    """Guided prompts to add/update a tool and then add multiple commands."""
    t = tool or _get_context() or typer.prompt("Tool name")
    desc = typer.prompt("Tool description", default="")
    tags_line = typer.prompt("Tags (comma-separated, optional)", default="")
    tags = _split_tags(tags_line)

    with db() as conn:
        tid = ensure_tool(conn, t, desc)
        attach_tags(conn, tid, tags)
        conn.commit()
    _set_context(t)
    console.print(Panel.fit(f"Tool [bold]{t}[/] saved. Context set. Let's add commands..."))

    while True:
        cname = typer.prompt("Command name (e.g., init, list)", default="")
        if not cname:
            break
        cdesc = typer.prompt("Command description", default="")
        snip = typer.prompt("Command snippet (paste the exact command)", default="")
        with db() as conn:
            tool_id = get_tool_id(conn, t)
            existing = conn.execute("SELECT id FROM commands WHERE tool_id=? AND name=?", (tool_id, cname)).fetchone()
            if existing:
                conn.execute("UPDATE commands SET description=?, snippet=? WHERE id=?", (cdesc, snip, existing[0]))
            else:
                conn.execute(
                    "INSERT INTO commands(tool_id, name, description, snippet) VALUES(?,?,?,?)",
                    (tool_id, cname, cdesc, snip),
                )
            conn.commit()
        console.print(f"Saved command [bold]{cname}[/].")
        if not typer.confirm("Add another command?", default=True):
            break
    console.print("Done.")

@app.command("qtool")
def qtool(
    spec: str = typer.Argument(..., help="E.g. 'curl: HTTP client #http,net'"),
):
    """Quickly add/update a tool from a compact spec."""
    if ":" in spec:
        name, rest = spec.split(":", 1)
    else:
        name, rest = spec, ""
    name = name.strip()
    desc, tags_str = rest, ""
    if "#" in rest:
        desc, tags_str = rest.split("#", 1)
    desc = desc.strip()
    tags = _split_tags(tags_str) if tags_str else []
    with db() as conn:
        tid = ensure_tool(conn, name, desc)
        attach_tags(conn, tid, tags)
        conn.commit()
    _set_context(name)
    console.print(Panel.fit(f"Tool [bold]{name}[/] saved. Context set."))

@app.command("qcmd")
def qcmd(
    spec: str = typer.Argument(..., help="E.g. 'curl.get: Simple GET | curl -s https://example.com'"),
    tool: Optional[str] = typer.Option(None, "--tool", "-T", help="Override default tool if not in spec"),
):
    """Quickly add/update a command from a compact spec."""
    if ":" not in spec:
        raise typer.Exit("Expected ':' in spec. Example: 'tool.cmd: Desc | snippet'")
    left, right = spec.split(":", 1)
    left = left.strip()
    desc = ""
    snippet = ""
    if "|" in right:
        desc, snippet = right.split("|", 1)
    else:
        desc = right
    desc = desc.strip()
    snippet = snippet.strip()

    if "." in left:
        t, cname = left.split(".", 1)
        t = t.strip()
    else:
        t = tool or _get_context()
        if not t:
            raise typer.Exit("No tool in spec and no default tool set. Use `vman use <tool>` or pass --tool.")
        cname = left.strip()

    with db() as conn:
        tool_id = get_tool_id(conn, t)
        if not tool_id:
            raise typer.Exit(f"Tool '{t}' not found. Create it with: vman add-tool {t} --desc ...")
        existing = conn.execute("SELECT id FROM commands WHERE tool_id=? AND name=?", (tool_id, cname)).fetchone()
        if existing:
            conn.execute("UPDATE commands SET description=?, snippet=? WHERE id=?", (desc, snippet, existing[0]))
        else:
            conn.execute(
                "INSERT INTO commands(tool_id, name, description, snippet) VALUES(?,?,?,?)",
                (tool_id, cname, desc, snippet),
            )
        conn.commit()
    console.print(Panel.fit(f"[bold]{t}[/] · [bold]{cname}[/] saved."))

# Optional: Bulk import via TOML (py3.11+), falls back to tomli if installed
try:
    import tomllib  # Python 3.11+
except Exception:  # pragma: no cover
    tomllib = None
    try:
        import tomli as tomllib  # type: ignore
    except Exception:
        tomllib = None

@app.command("template-toml")
def template_toml():
    """Print a TOML template for bulk import."""
    sample = """
# Save as tools.toml, then run: vman import-toml tools.toml
title = "vman import"

[[tools]]
name = "ironclad"
description = "Password manager (encrypted local vault)"
tags = ["password", "manage", "database"]

  [[tools.commands]]
  name = "init"
  description = "Create a new vault"
  snippet = "ironclad init --store ~/.secrets/ironclad.vault"

  [[tools.commands]]
  name = "list"
  description = "List entries"
  snippet = "ironclad list"
"""
    console.print(sample.strip())

@app.command("import-toml")
def import_toml(path: Path):
    """Import many tools/commands from a TOML file."""
    if tomllib is None:
        raise typer.Exit("TOML parser not available. Use Python 3.11+ or `pip install tomli` in your venv.")
    data = tomllib.loads(path.read_text(encoding="utf-8"))
    tools = data.get("tools", [])
    count_tools = 0
    count_cmds = 0
    with db() as conn:
        for t in tools:
            name = t["name"]
            desc = t.get("description", "")
            tags = t.get("tags", [])
            tid = ensure_tool(conn, name, desc)
            attach_tags(conn, tid, tags)
            for c in t.get("commands", []):
                cname = c["name"]
                cdesc = c.get("description", "")
                snip = c.get("snippet", "")
                existing = conn.execute(
                    "SELECT id FROM commands WHERE tool_id=? AND name=?", (tid, cname)
                ).fetchone()
                if existing:
                    conn.execute("UPDATE commands SET description=?, snippet=? WHERE id=?", (cdesc, snip, existing[0]))
                else:
                    conn.execute(
                        "INSERT INTO commands(tool_id, name, description, snippet) VALUES(?,?,?,?)",
                        (tid, cname, cdesc, snip),
                    )
                count_cmds += 1
            count_tools += 1
        conn.commit()
    console.print(Panel.fit(f"Imported {count_tools} tool(s), {count_cmds} command(s)."))

# ------------------------------------------------------------
# Optional: run a stored snippet (with confirm/copy)
# ------------------------------------------------------------
@app.command("run")
def run_snippet(
    tool: str = typer.Argument(..., help="Tool name"),
    name: str = typer.Argument(..., help="Command name"),
    yes: bool = typer.Option(False, "--yes", "-y", help="Skip confirmation"),
    copy: bool = typer.Option(False, "--copy", "-c", help="Copy snippet to clipboard before running (macOS pbcopy)"),
    print_only: bool = typer.Option(False, "--print", help="Only print snippet, don't execute"),
    shell: str = typer.Option("/bin/zsh", "--shell", help="Shell to execute under"),
):
    """Print (and optionally execute) a stored snippet."""
    with db() as conn:
        tool_id = get_tool_id(conn, tool)
        if not tool_id:
            raise typer.Exit(f"Tool '{tool}' not found.")
        row = conn.execute(
            "SELECT snippet, description FROM commands WHERE tool_id=? AND name=?", (tool_id, name)
        ).fetchone()
        if not row:
            raise typer.Exit(f"No command '{name}' for tool '{tool}'.")
        snippet, desc = row

    console.print(Panel.fit(f"[bold]{tool}[/] · [bold]{name}[/]\n{desc or ''}"))
    console.print(Syntax(snippet or "", "bash", word_wrap=True))

    if copy:
        try:
            p = subprocess.run(["pbcopy"], input=(snippet or "").encode(), check=True)
            console.print("[dim]Snippet copied to clipboard.[/]")
        except Exception:
            console.print("[dim]Clipboard copy failed (pbcopy not available).[/]")

    if print_only:
        raise typer.Exit(0)

    if not yes:
        if not typer.confirm("Run this command?", default=False):
            raise typer.Exit(1)

    # Execute via chosen shell
    try:
        subprocess.run([shell, "-lc", snippet], check=True)
    except subprocess.CalledProcessError as e:
        raise typer.Exit(e.returncode)

# ------------------------------------------------------------
# Main
# ------------------------------------------------------------
if __name__ == "__main__":
    app()

