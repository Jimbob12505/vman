#!/usr/bin/env python3
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

# --- fuzzy deps (add near other imports) ---
try:
    from rapidfuzz import process, fuzz
    HAS_RF = True
except Exception:  # RapidFuzz not installed
    HAS_RF = False

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
    q: Optional[str] = typer.Argument(None, help="Search text (omit to match all)"),
    tag: Optional[str] = typer.Option(None, "--tag", "-t", help="Filter by tag"),
    tool: Optional[str] = typer.Option(None, "--tool", "-T", help="Restrict to this tool"),
    cmd: Optional[str]  = typer.Option(None, "--cmd", "-C", help="Restrict by command name"),
    exact: bool = typer.Option(False, "--exact", help="Use exact match for --cmd"),
):
    """Search tools/commands with optional filters by tag, tool, and command name."""
    like = f"%{q}%" if q else None

    # Build query parts
    sql = [
        "SELECT tools.name, COALESCE(commands.name, ''),",
        "       COALESCE(commands.snippet, ''), COALESCE(commands.description, '')",
        "FROM tools",
    ]
    params = []
    conditions = []

    if tag:
        sql += [
            "JOIN tool_tags ON tool_tags.tool_id = tools.id",
            "JOIN tags ON tags.id = tool_tags.tag_id",
        ]

    sql += ["LEFT JOIN commands ON commands.tool_id = tools.id"]

    if like:
        conditions.append(
            "("
            "tools.name LIKE ? OR tools.description LIKE ? OR "
            "commands.name LIKE ? OR commands.description LIKE ? OR commands.snippet LIKE ?"
            ")"
        )
        params.extend([like, like, like, like, like])

    if tag:
        conditions.append("tags.name = ?")
        params.append(tag)

    if tool:
        conditions.append("tools.name = ?")
        params.append(tool)

    if cmd:
        if exact:
            conditions.append("commands.name = ?")
            params.append(cmd)
        else:
            conditions.append("commands.name LIKE ?")
            params.append(f"%{cmd}%")

    sql_str = "\n".join(sql)
    if conditions:
        sql_str += "\nWHERE " + " AND ".join(conditions)
    sql_str += "\nORDER BY tools.name, commands.name"

    with db() as conn:
        rows = conn.execute(sql_str, params).fetchall()

    if not rows:
        console.print("No results.")
        raise typer.Exit(0)

    table = Table(title=f"Search: {q or '*'}", show_lines=False)
    table.add_column("Tool", style="bold")
    table.add_column("Command")
    table.add_column("Summary")
    for tool_name, cname, snip, cdesc in rows:
        summary = cdesc or (snip[:60] + "…" if snip and len(snip) > 60 else snip)
        table.add_row(tool_name, cname, summary or "")
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
# Fuzzfinder
# ------------------------------------------------------------

def _build_catalog(conn: sqlite3.Connection, tool: Optional[str] = None, tag: Optional[str] = None):
    """
    Return a list of items: {tool, cmd, desc, snippet, summary, search}
    """
    sql = [
        "SELECT tools.name AS tool, commands.name AS cmd,",
        "       COALESCE(commands.description, ''), COALESCE(commands.snippet, '')",
        "FROM tools",
        "LEFT JOIN commands ON commands.tool_id = tools.id",
    ]
    params = []
    if tag:
        sql.insert(3, "JOIN tool_tags ON tool_tags.tool_id = tools.id")
        sql.insert(4, "JOIN tags ON tags.id = tool_tags.tag_id")

    where = []
    if tag:
        where.append("tags.name = ?")
        params.append(tag)
    if tool:
        where.append("tools.name = ?")
        params.append(tool)

    sql_str = "\n".join(sql)
    if where:
        sql_str += "\nWHERE " + " AND ".join(where)
    sql_str += "\nORDER BY tools.name, commands.name"

    rows = conn.execute(sql_str, params).fetchall()
    items = []
    for tool_name, cmd_name, desc, snip in rows:
        if not cmd_name:  # skip tools that have no command row
            continue
        summary = desc or (snip[:80] + "…" if snip and len(snip) > 80 else snip)
        searchable = " ".join(filter(None, [tool_name, cmd_name, desc, snip]))
        items.append({
            "tool": tool_name,
            "cmd": cmd_name,
            "desc": desc or "",
            "snippet": snip or "",
            "summary": summary or "",
            "search": searchable
        })
    return items

@app.command("fuzzy")
def fuzzy(
    query: Optional[str] = typer.Argument(None, help="Search text (optional)"),
    tool: Optional[str] = typer.Option(None, "--tool", "-T", help="Restrict to one tool"),
    tag: Optional[str]  = typer.Option(None, "--tag", "-t", help="Filter by tag"),
    top: int = typer.Option(10, "--top", "-n", help="Number of results to show"),
    choose: bool = typer.Option(False, "--choose", "-c", help="Prompt to choose and act on a result"),
    exec_: bool = typer.Option(False, "--exec", "-x", help="Execute chosen snippet"),
    yes: bool = typer.Option(False, "--yes", "-y", help="Skip confirm when executing"),
    copy: bool = typer.Option(False, "--copy", "-C", help="Copy chosen snippet to clipboard"),
    shell: str = typer.Option("/bin/zsh", "--shell", help="Shell to execute under with --exec"),
):
    """Fuzzy rank tools/commands (RapidFuzz). Use --choose to pick and act."""
    if not HAS_RF:
        raise typer.Exit("Fuzzy search requires 'rapidfuzz'. Install it with: pip install rapidfuzz")

    with db() as conn:
        catalog = _build_catalog(conn, tool=tool, tag=tag)
    if not catalog:
        console.print("No commands found.")
        raise typer.Exit(0)

    choices = [it["search"] for it in catalog]
    if query:
        matches = process.extract(query, choices, scorer=fuzz.WRatio, limit=top)
        ranked = [(idx, score) for choice, score, idx in matches]
    else:
        # No query: just take the first N (alphabetical by tool/cmd due to ORDER BY)
        ranked = [(i, 100) for i in range(min(top, len(catalog)))]

    table = Table(title=f"Fuzzy: {query or '*'}", show_lines=False)
    table.add_column("#", justify="right")
    table.add_column("Tool", style="bold")
    table.add_column("Command")
    table.add_column("Score", justify="right")
    table.add_column("Summary")
    for k, (idx, score) in enumerate(ranked, start=1):
        it = catalog[idx]
        table.add_row(str(k), it["tool"], it["cmd"], str(int(score)), it["summary"])
    console.print(table)

    if not choose:
        raise typer.Exit(0)

    # choose & act
    pick = typer.prompt("Pick #", type=int)
    if pick < 1 or pick > len(ranked):
        raise typer.Exit("Invalid selection.")
    chosen = catalog[ranked[pick - 1][0]]

    # optional clipboard
    if copy:
        try:
            subprocess.run(["pbcopy"], input=(chosen["snippet"]).encode(), check=True)
            console.print("[dim]Snippet copied to clipboard.[/]")
        except Exception:
            console.print("[dim]Clipboard copy failed (pbcopy not available).[/]")

    if exec_:
        if not yes and not typer.confirm(f"Run {chosen['tool']} · {chosen['cmd']} ?", default=False):
            raise typer.Exit(1)
        try:
            subprocess.run([shell, "-lc", chosen["snippet"]], check=True)
        except subprocess.CalledProcessError as e:
            raise typer.Exit(e.returncode)
    else:
        # default: print raw snippet (so zsh widget or user can edit placeholders easily)
        sys.stdout.write(chosen["snippet"] + ("\n" if not chosen["snippet"].endswith("\n") else ""))

@app.command("pick")
def pick(
    query: Optional[str] = typer.Argument(None, help="Initial query for fzf"),
    tool: Optional[str] = typer.Option(None, "--tool", "-T", help="Restrict to one tool"),
    tag: Optional[str]  = typer.Option(None, "--tag", "-t", help="Filter by tag"),
    exec_: bool = typer.Option(False, "--exec", "-x", help="Execute the selected snippet"),
    yes: bool = typer.Option(False, "--yes", "-y", help="Skip confirm when executing"),
    copy: bool = typer.Option(False, "--copy", "-C", help="Copy snippet to clipboard"),
    shell: str = typer.Option("/bin/zsh", "--shell", help="Shell to execute under with --exec"),
):
    """Interactive picker using fzf (if installed). Prints snippet by default."""
    if shutil.which("fzf") is None:
        raise typer.Exit("fzf not found. Install with: brew install fzf  (or use: vman fuzzy --choose)")

    with db() as conn:
        catalog = _build_catalog(conn, tool=tool, tag=tag)
    if not catalog:
        console.print("No commands found.")
        raise typer.Exit(0)

    # Each line: TOOL \t CMD \t SUMMARY
    lines = [f"{it['tool']}\t{it['cmd']}\t{it['summary']}" for it in catalog]

    fzf_cmd = [
        "fzf",
        "--ansi",
        "--delimiter", "\t",
        "--with-nth", "1,2,3",
        "--nth", "1,2,3",
        "--bind", "alt-y:execute-silent(echo {1}\t{2} | pbcopy)+abort",
        "--preview", "vman run {1} {2} --preview --no-raw",
        "--prompt", "vman> ",
    ]
    if query:
        fzf_cmd += ["--query", query]

    p = subprocess.run(fzf_cmd, input=("\n".join(lines)).encode(), stdout=subprocess.PIPE)
    if p.returncode != 0:
        raise typer.Exit(p.returncode)
    out = p.stdout.decode().strip().splitlines()
    if not out:
        raise typer.Exit(1)

    tool_name, cmd_name, _ = out[-1].split("\t", 2)

    # look up snippet
    with db() as conn:
        tid = get_tool_id(conn, tool_name)
        row = conn.execute("SELECT snippet FROM commands WHERE tool_id=? AND name=?", (tid, cmd_name)).fetchone()
    snippet = (row[0] if row else "").strip()

    if copy:
        try:
            subprocess.run(["pbcopy"], input=snippet.encode(), check=True)
            console.print("[dim]Snippet copied to clipboard.[/]")
        except Exception:
            console.print("[dim]Clipboard copy failed (pbcopy not available).[/]")

    if exec_:
        if not yes and not typer.confirm(f"Run {tool_name} · {cmd_name} ?", default=False):
            raise typer.Exit(1)
        try:
            subprocess.run([shell, "-lc", snippet], check=True)
        except subprocess.CalledProcessError as e:
            raise typer.Exit(e.returncode)
    else:
        # default: print raw snippet
        sys.stdout.write(snippet + ("\n" if not snippet.endswith("\n") else ""))

# ------------------------------------------------------------
# Optional: run a stored snippet (with confirm/copy)
# ------------------------------------------------------------

@app.command("run")
def run_snippet(
    tool: str = typer.Argument(..., help="Tool name"),
    name: str = typer.Argument(..., help="Command name"),
    exec_: bool = typer.Option(False, "--exec", "-x", help="Execute the snippet instead of just printing it"),
    yes: bool = typer.Option(False, "--yes", "-y", help="Skip confirmation when executing"),
    copy: bool = typer.Option(False, "--copy", "-c", help="Copy snippet to clipboard"),
    raw: bool = typer.Option(True, "--raw/--no-raw", help="Print raw snippet only (default)"),
    preview: bool = typer.Option(False, "--preview", help="Show a pretty panel before printing/executing"),
    shell: str = typer.Option("/bin/zsh", "--shell", help="Shell to execute under when using --exec"),
):
    """Default: print the stored snippet (easy to edit placeholders). Use --exec to actually run."""
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

    if preview:
        console.print(Panel.fit(f"[bold]{tool}[/] · [bold]{name}[/]\n{desc or ''}"))
        if not raw:
            console.print(Syntax(snippet or "", "bash", word_wrap=True))

    # Always optionally copy
    if copy:
        try:
            subprocess.run(["pbcopy"], input=(snippet or "").encode(), check=True)
            console.print("[dim]Snippet copied to clipboard.[/]")
        except Exception:
            console.print("[dim]Clipboard copy failed (pbcopy not available).[/]")

    # DEFAULT BEHAVIOR: print raw snippet and exit (no execution)
    if not exec_:
        # print just the snippet so you can edit placeholders quickly
        sys.stdout.write((snippet or "") + ("\n" if not (snippet or "").endswith("\n") else ""))
        raise typer.Exit(0)

    # EXECUTION PATH (only when --exec/-x is provided)
    # Show snippet plainly once when not in raw mode and no preview was requested
    if not preview and not raw:
        console.print(Syntax(snippet or "", "bash", word_wrap=True))

    if not yes:
        if not typer.confirm("Run this command?", default=False):
            raise typer.Exit(1)

    try:
        subprocess.run([shell, "-lc", snippet], check=True)
    except subprocess.CalledProcessError as e:
        raise typer.Exit(e.returncode)

# ------------------------------------------------------------
# Main
# ------------------------------------------------------------
if __name__ == "__main__":
    app()

