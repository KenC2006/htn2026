"""Arrow-key menus for the console: up/down to move, Enter to choose, Esc to cancel. No extra dependency."""
from __future__ import annotations

import sys

from rich.console import Console
from rich.live import Live
from rich.text import Text

ACCENT = "#00e0c4"


def _key() -> str:
    """One key press as 'up', 'down', 'enter', 'esc', 'space' or the character itself."""
    if sys.platform == "win32":
        import msvcrt
        ch = msvcrt.getwch()
        if ch in ("\x00", "\xe0"):
            return {"H": "up", "P": "down"}.get(msvcrt.getwch(), "")
    else:
        import termios
        import tty
        fd = sys.stdin.fileno()
        old = termios.tcgetattr(fd)
        try:
            tty.setraw(fd)
            ch = sys.stdin.read(1)
            if ch == "\x1b":
                import select
                if select.select([sys.stdin], [], [], 0.05)[0]:
                    return {"[A": "up", "[B": "down"}.get(sys.stdin.read(2), "")
        finally:
            termios.tcsetattr(fd, termios.TCSADRAIN, old)
    if ch == "\x03":
        raise KeyboardInterrupt
    return {"\r": "enter", "\n": "enter", "\x1b": "esc", " ": "space", "k": "up", "j": "down", "q": "esc"}.get(ch, ch)


def _draw(title: str, options: list[tuple[str, str, bool]], at: int, ticked: set[int] | None, room: int = 0) -> Text:
    """room: how many option rows fit on the screen (0 = all). A longer list scrolls with the cursor, so no row is ever cut off."""
    out = Text.assemble((f"  {title}\n", "bold"))
    first, last = 0, len(options)
    if room and len(options) > room:
        first = max(0, min(at - room // 2, len(options) - room))
        last = first + room
    if first:
        out.append(f"      ↑ {first} more\n", style="dim")
    for i, (label, note, enabled) in list(enumerate(options))[first:last]:
        here = i == at
        box = "" if ticked is None else ("◉ " if i in ticked else "○ ")
        out.append("  ❯ " if here else "    ", style=f"bold {ACCENT}")
        out.append(box + label, style=(f"bold {ACCENT}" if here else "default") if enabled else "dim")
        out.append(f"   {note}\n" if note else "\n", style="dim")
    if last < len(options):
        out.append(f"      ↓ {len(options) - last} more\n", style="dim")
    if ticked is not None:
        out.append(f"  {len(ticked)} of {sum(1 for o in options if o[2])} ticked   ", style=ACCENT)
    out.append("  ↑↓ move   " + ("space tick / untick   a all   n none   " if ticked is not None else "") + "enter choose   esc cancel", style="dim")
    return out


def pick(console: Console, title: str, options: list[tuple[str, str, bool]], start: int = 0) -> int | None:
    """options: (label, note, enabled). Returns the chosen index, or None if cancelled."""
    chosen = _menu(console, title, options, start, None)
    return chosen[0] if chosen else None


def pick_many(console: Console, title: str, options: list[tuple[str, str, bool]], ticked: set[int] | None = None) -> list[int] | None:
    """Like pick, with space to tick several. Returns the ticked indexes, or None if cancelled."""
    return _menu(console, title, options, 0, set(ticked or ()))


def _menu(console: Console, title: str, options, start: int, ticked: set[int] | None) -> list[int] | None:
    if not options:
        return None
    if not (console.is_terminal and sys.stdin.isatty()):          # piped input: fall back to typing a number
        for i, (label, note, _) in enumerate(options):
            console.print(f"  {i + 1}  {label}   [dim]{note}[/dim]", highlight=False)
        try:
            answer = console.input("[dim]  number(s):[/dim] ").replace(",", " ").split()
        except (EOFError, KeyboardInterrupt):
            return None
        picked = [int(a) - 1 for a in answer if a.isdigit() and 1 <= int(a) <= len(options) and options[int(a) - 1][2]]
        return (picked if ticked is not None else picked[:1]) or None
    at = start if 0 <= start < len(options) else 0
    room = lambda: max(console.size.height - 8, 3)  # noqa: E731   the title (may wrap), the two "more" lines, the help line (may wrap)
    with Live(_draw(title, options, at, ticked, room()), console=console, auto_refresh=False, transient=True) as live:
        while True:
            key = _key()
            if key in ("up", "down"):
                at = (at + (1 if key == "down" else -1)) % len(options)
            elif key == "space" and ticked is not None and options[at][2]:
                ticked ^= {at}
            elif key in ("a", "n") and ticked is not None:
                ticked.clear()
                ticked |= {i for i, o in enumerate(options) if o[2]} if key == "a" else set()
            elif key == "esc":
                return None
            elif key == "enter":
                if ticked is not None:
                    return sorted(ticked) or None
                if options[at][2]:
                    return [at]
            elif key.isdigit() and 1 <= int(key) <= len(options):
                at = int(key) - 1
            live.update(_draw(title, options, at, ticked, room()), refresh=True)
