"""Full-screen terminal UI (Textual). A front end over walink.actions, exactly like the CLI: the same functions,
the same rules, the same "Are you sure you want to delete <NAME>?" question.

Phone actions run in a plain non-daemon thread, one at a time: if the SSH connection drops (SIGHUP) or the UI
dies, the running action still finishes (and locks the phone) before the process exits."""
from __future__ import annotations

import asyncio
import json
import logging
import signal
import threading
from typing import Any, Callable

from rich.text import Text
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.screen import ModalScreen
from textual.widgets import Button, DataTable, Footer, Input, Label, OptionList, RichLog, Static

from . import actions as walink_actions
from .code import CodeError, normalize_code
from .lock import Busy
from .messages import format_message

log = logging.getLogger("walink")

FREE_SLOT = "(free slot)"
LOCKED = "[locked] "
CODE_HINT = "Show the code on the computer first; it expires in about a minute."
QUIT_QUESTION = "Quit after the current action finishes?"
# Actions that need the phone: disabled while one is running.
PHONE_ACTIONS = {"refresh", "link", "delete", "last", "keepalive", "status"}


def _valid_code(raw: str) -> bool:
    try:
        normalize_code(raw)
        return True
    except CodeError:
        return False


class ConfirmScreen(ModalScreen[bool]):
    """Yes/No question. Focus starts on No, so Enter alone never confirms."""
    BINDINGS = [("y", "answer(True)", "Yes"), ("n,escape", "answer(False)", "No")]

    def __init__(self, question: str):
        super().__init__()
        self.question = question

    def compose(self) -> ComposeResult:
        with Vertical(classes="dialog"):
            yield Label(self.question, classes="question", markup=False)
            with Horizontal(classes="buttons"):
                yield Button("No", id="no", variant="primary")
                yield Button("Yes", id="yes", variant="error")

    def on_mount(self) -> None:
        self.query_one("#no").focus()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        self.dismiss(event.button.id == "yes")

    def action_answer(self, value: bool) -> None:
        self.dismiss(value)


class LinkScreen(ModalScreen):
    """Code + name for a replace (name pre-filled) or a free slot. Returns (code, name) or None."""
    BINDINGS = [("escape", "cancel", "Cancel")]

    def __init__(self, replace: str | None):
        super().__init__()
        self.replace = replace

    def compose(self) -> ComposeResult:
        title = f"Replace '{self.replace}' with a new link" if self.replace else "Link a new device"
        with Vertical(classes="dialog"):
            yield Label(title, classes="question", markup=False)
            yield Static(CODE_HINT, classes="hint")
            yield Input(placeholder="Code, e.g. ABCD-EFGH", id="code")
            yield Input(value=self.replace or "", placeholder="Device name", id="name")
            with Horizontal(classes="buttons"):
                yield Button("Cancel", id="cancel")
                yield Button("Link", id="run", variant="primary", disabled=True)

    def on_mount(self) -> None:
        self.query_one("#code").focus()

    def _ready(self) -> bool:
        return _valid_code(self.query_one("#code", Input).value) and bool(self.query_one("#name", Input).value.strip())

    def on_input_changed(self, event: Input.Changed) -> None:
        self.query_one("#run", Button).disabled = not self._ready()

    def on_input_submitted(self, event: Input.Submitted) -> None:
        if self._ready():
            self._submit()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "run" and self._ready():
            self._submit()
        elif event.button.id == "cancel":
            self.dismiss(None)

    def _submit(self) -> None:
        self.dismiss((normalize_code(self.query_one("#code", Input).value), self.query_one("#name", Input).value.strip()))

    def action_cancel(self) -> None:
        self.dismiss(None)


class LastScreen(ModalScreen):
    """Pick a quick chat or type any chat name. Returns the name or None."""
    BINDINGS = [("escape", "cancel", "Cancel")]

    def __init__(self, chats: list[str]):
        super().__init__()
        self.chats = chats

    def compose(self) -> ComposeResult:
        with Vertical(classes="dialog"):
            yield Label("Last message of which chat? (opening it marks it as read)", classes="question")
            if self.chats:
                yield OptionList(*self.chats, id="quick")
            yield Input(placeholder="Exact chat name, as in the chat list", id="chat")
            with Horizontal(classes="buttons"):
                yield Button("Cancel", id="cancel")
                yield Button("Read", id="read", variant="primary")

    def on_mount(self) -> None:
        (self.query_one("#quick") if self.chats else self.query_one("#chat")).focus()

    def on_option_list_option_selected(self, event: OptionList.OptionSelected) -> None:
        self.dismiss(str(event.option.prompt))

    def on_input_submitted(self, event: Input.Submitted) -> None:
        if event.value.strip():
            self.dismiss(event.value.strip())

    def on_button_pressed(self, event: Button.Pressed) -> None:
        value = self.query_one("#chat", Input).value.strip()
        if event.button.id == "read" and value:
            self.dismiss(value)
        elif event.button.id == "cancel":
            self.dismiss(None)

    def action_cancel(self) -> None:
        self.dismiss(None)


class MessageScreen(ModalScreen):
    """Shows a result until dismissed. Never logged."""
    BINDINGS = [("escape,enter,q", "close", "Close")]

    def __init__(self, title: str, text: str):
        super().__init__()
        self.title_text, self.text = title, text

    def compose(self) -> ComposeResult:
        with Vertical(classes="dialog"):
            yield Label(self.title_text, classes="question", markup=False)
            yield Static(self.text, classes="result", markup=False)
            with Horizontal(classes="buttons"):
                yield Button("Close", id="close", variant="primary")

    def on_mount(self) -> None:
        self.query_one("#close").focus()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        self.dismiss(None)

    def action_close(self) -> None:
        self.dismiss(None)


class _PaneHandler(logging.Handler):
    """Mirrors walink log lines into the progress pane, from any thread."""

    def __init__(self, app: "WalinkApp"):
        super().__init__(logging.INFO)
        self.app = app
        self.setFormatter(logging.Formatter("%(asctime)s %(message)s", "%H:%M:%S"))

    def emit(self, record: logging.LogRecord) -> None:
        try:
            self.app.ui(self.app.write_progress, self.format(record))
        except Exception:
            pass


class WalinkApp(App):
    TITLE = "walink"
    CSS = """
    #header { height: 1; background: $primary; color: $text; padding: 0 1; }
    #main { height: 1fr; }
    #devices { width: 1fr; border: round $primary; }
    #progress { width: 1fr; border: round $secondary; }
    ModalScreen { align: center middle; }
    .dialog { width: 70; max-width: 100%; height: auto; border: thick $primary; background: $surface; padding: 1 2; }
    .question { width: 100%; margin-bottom: 1; text-style: bold; }
    .hint { color: $text-muted; margin-bottom: 1; }
    .result { margin-bottom: 1; }
    .buttons { height: auto; align: right middle; }
    .buttons Button { margin-left: 2; }
    #quick { height: auto; max-height: 8; margin-bottom: 1; }
    Input { margin-bottom: 1; }
    """
    BINDINGS = [
        Binding("r", "refresh", "Refresh"),
        Binding("l", "link", "Link/replace"),
        Binding("d", "delete", "Delete"),
        Binding("m", "last", "Last message"),
        Binding("k", "keepalive", "Keepalive"),
        Binding("s", "status", "Status"),
        Binding("q", "quit", "Quit"),
        Binding("ctrl+c", "quit", "Quit", show=False, priority=True),
    ]

    def __init__(self, cfg: dict, label: str, serial: str | None = None, dry_run: bool = False,
                 actions: Any = walink_actions):
        super().__init__()
        self.cfg, self.label, self.serial, self.dry_run, self.actions = cfg, label, serial, dry_run, actions
        self.busy: str | None = None
        self.info: dict | None = None
        self._rows: list[dict] = []                 # parallel to the table rows
        self._thread: threading.Thread | None = None
        self._quit_after = False
        self._pending_confirm: Callable[[bool], None] | None = None
        self._event_loop: asyncio.AbstractEventLoop | None = None
        self._main_thread = threading.get_ident()
        self._handler = _PaneHandler(self)

    # ---------- layout ----------
    def compose(self) -> ComposeResult:
        yield Static(id="header")
        with Horizontal(id="main"):
            yield DataTable(id="devices", cursor_type="row", zebra_stripes=True)
            yield RichLog(id="progress", wrap=True, markup=False, highlight=False)
        yield Footer()

    def on_mount(self) -> None:
        self._event_loop = asyncio.get_running_loop()
        self._main_thread = threading.get_ident()
        table = self.query_one("#devices", DataTable)
        table.add_columns("Device", "Status")
        table.border_title = "Linked devices"
        self.query_one("#progress", RichLog).border_title = "Progress"
        log.addHandler(self._handler)
        if log.level == logging.NOTSET:
            log.setLevel(logging.INFO)
        self._update_header()
        self.action_refresh()

    def on_unmount(self) -> None:
        log.removeHandler(self._handler)

    def _update_header(self) -> None:
        phone = f"busy ({self.busy})" if self.busy else "idle"
        dry = "   DRY RUN" if self.dry_run else ""
        self.query_one("#header", Static).update(f"walink — key: {self.label}   phone: {phone}{dry}")

    def write_progress(self, line: str) -> None:
        self.query_one("#progress", RichLog).write(line)

    def row_labels(self) -> list[str]:
        return [FREE_SLOT if r is None else (LOCKED if r.get("protected") else "") + r["name"] for r in self._rows]

    def _show_devices(self, info: dict) -> None:
        self.info = info
        table = self.query_one("#devices", DataTable)
        table.clear()
        self._rows = list(info["devices"]) + ([None] if info["count"] < info["max"] else [])
        for label, row in zip(self.row_labels(), self._rows):
            table.add_row(Text(label), Text("" if row is None else row["status"]))   # names are not markup
        table.border_title = f"Linked devices ({info['count']} of {info['max']})"

    def _selected(self) -> dict | None | bool:
        """The selected row: a device dict, None for the free slot, False when nothing is loaded."""
        if not self._rows:
            return False
        return self._rows[min(self.query_one("#devices", DataTable).cursor_row, len(self._rows) - 1)]

    # ---------- threads ----------
    def ui(self, fn: Callable, *args, **kwargs) -> Any:
        """Run `fn` on the UI thread and return its result; False if the UI is gone (e.g. after a hangup)."""
        if threading.get_ident() == self._main_thread:
            return fn(*args, **kwargs)
        try:
            return self.call_from_thread(fn, *args, **kwargs)
        except Exception:
            return False

    def _confirm(self, question: str) -> bool:
        """Called by the action thread: blocks on the modal. A hangup or a dead UI answers No."""
        done, answer = threading.Event(), {"value": False}

        def resolve(value) -> None:
            if not done.is_set():
                answer["value"] = bool(value)
                self._pending_confirm = None
                done.set()
        self._pending_confirm = resolve
        if self._quit_after or self.ui(self.push_screen, ConfirmScreen(question), resolve) is False:
            resolve(False)
        done.wait()
        return answer["value"]

    def _action_context(self):
        return walink_actions.Context(cfg=self.cfg, label=self.label, confirm=self._confirm,
                                      serial=self.serial, dry_run=self.dry_run)

    def check_action(self, action: str, parameters: tuple) -> bool | None:
        return not (self.busy and action in PHONE_ACTIONS)

    def _start(self, name: str, fn: Callable, on_done: Callable | None = None) -> None:
        if self.busy:
            return
        self.busy = name
        self._update_header()
        self.refresh_bindings()
        self.write_progress(f"— {name} —")
        self._thread = threading.Thread(target=self._run_action, args=(fn, on_done), name=f"walink-{name}")
        self._thread.start()

    def _run_action(self, fn: Callable, on_done: Callable | None) -> None:
        try:
            result = fn(self._action_context())
        except Busy:
            self.ui(self.notify, "Phone busy, try again", severity="warning")
        except self.actions.Refused as e:
            self.ui(self.notify, str(e), severity="warning", markup=False)
        except self.actions.ActionFailed as e:
            where = f" (evidence in {e.evidence})" if e.evidence else ""
            self.ui(self.notify, f"Failed: {e}{where}", severity="error", timeout=15, markup=False)
        except Exception as e:
            log.exception("unexpected error")
            self.ui(self.notify, f"Failed: {e}", severity="error", timeout=15, markup=False)
        else:
            if on_done:
                self.ui(on_done, result)
        finally:
            self.ui(self._finished)

    def _finished(self) -> None:
        self.busy = None
        self._update_header()
        self.refresh_bindings()
        if self._quit_after:
            self.exit()

    def join_action(self) -> None:
        """Wait for a running action (used after the UI has stopped)."""
        if self._pending_confirm:
            self._pending_confirm(False)
        if self._thread:
            self._thread.join()

    def hangup(self) -> None:
        """The terminal went away: answer any pending question with No, finish the running action, then exit."""
        self._quit_after = True
        if self._pending_confirm:
            self._pending_confirm(False)
        if not self.busy and self._event_loop:
            self._event_loop.call_soon_threadsafe(self.exit)

    # ---------- actions ----------
    def action_refresh(self) -> None:
        self._start("list", self.actions.list_devices, self._show_devices)

    def action_link(self) -> None:
        sel = self._selected()
        if sel is False:
            self.notify("Load the device list first (r)", severity="warning")
            return
        if sel and sel.get("protected"):
            self.notify(f"'{sel['name']}' is protected", severity="warning", markup=False)
            return
        replace = sel["name"] if sel else None

        def go(result) -> None:
            if result:
                code, name = result
                self._start("link", lambda ctx: self.actions.link(ctx, code, replace=replace, name=name),
                            lambda res: self._linked(replace, res))
        self.push_screen(LinkScreen(replace), go)

    def _linked(self, replaced: str | None, res: dict) -> None:
        self.notify(f"Linked '{res['name']}': {res['status']}", markup=False)
        if self.info and not self.dry_run:
            devices = [d for d in self.info["devices"] if d["name"] != replaced]
            devices.append({**res, "protected": False})
            self._show_devices({**self.info, "count": len(devices), "devices": devices})

    def action_delete(self) -> None:
        sel = self._selected()
        if not sel:
            self.notify("Select a device first", severity="warning")
            return
        if sel.get("protected"):
            self.notify(f"'{sel['name']}' is protected", severity="warning", markup=False)
            return
        name = sel["name"]
        self._start("remove", lambda ctx: self.actions.remove(ctx, name), lambda _: self._removed(name))

    def _removed(self, name: str) -> None:
        self.notify(f"{'[dry-run] would remove' if self.dry_run else 'Removed'} '{name}'", markup=False)
        if self.info and not self.dry_run:
            devices = [d for d in self.info["devices"] if d["name"] != name]
            self._show_devices({**self.info, "count": len(devices), "devices": devices})

    def action_last(self) -> None:
        def go(chat) -> None:
            if chat:
                self._start("last", lambda ctx: self.actions.last_message(ctx, chat),
                            lambda msg: self.push_screen(MessageScreen(f"Last message — {chat}", format_message(msg))))
        self.push_screen(LastScreen(self.actions.quick_chats(self.cfg)), go)

    def action_keepalive(self) -> None:
        self._start("keepalive", self.actions.keepalive, lambda _: self.notify("Keepalive done"))

    def action_status(self) -> None:
        self._start("status", self.actions.status,
                    lambda info: self.push_screen(MessageScreen("Phone status", json.dumps(info, indent=2))))

    async def action_quit(self) -> None:
        if not self.busy:
            self.exit()
            return

        def answer(yes) -> None:
            if yes:
                self._quit_after = True
                self.notify("Quitting when the current action finishes")
        self.push_screen(ConfirmScreen(QUIT_QUESTION), answer)


def run(cfg: dict, label: str, serial: str | None = None, dry_run: bool = False) -> int:
    # Log lines on stderr would draw over the screen; the progress pane and per-run file logs take over.
    for handler in list(log.handlers):
        if type(handler) is logging.StreamHandler:
            log.removeHandler(handler)
    log.setLevel(logging.INFO)
    app = WalinkApp(cfg, label, serial=serial, dry_run=dry_run)
    previous = None
    if hasattr(signal, "SIGHUP"):
        previous = signal.signal(signal.SIGHUP, lambda signum, frame: app.hangup())
    try:
        app.run()
    finally:
        app.join_action()              # never leave the phone mid-flow
        if previous is not None:
            signal.signal(signal.SIGHUP, previous)
    return 0
