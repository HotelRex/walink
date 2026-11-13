"""Full-screen terminal UI (Textual). A front end over walink.actions, exactly like the CLI: the same functions,
the same rules, the same "Are you sure you want to delete <NAME>?" question.

The start screen is a menu; the phone is only touched when an action is chosen. While the phone works a progress
dialog shows the current step in plain words. Technical log lines appear only with --debug.

Phone actions run in a plain non-daemon thread, one at a time: if the SSH connection drops (SIGHUP) or the UI
dies, the running action still finishes (and locks the phone) before the process exits."""
from __future__ import annotations

import asyncio
import concurrent.futures
import inspect
import json
import logging
import signal
import threading
import time
from typing import Any, Callable

from rich.text import Text
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.screen import ModalScreen
from textual.widgets import Button, DataTable, Footer, Input, Label, OptionList, ProgressBar, RichLog, Static
from textual.widgets.option_list import Option

from . import actions as walink_actions
from .code import CodeError, normalize_code
from .lock import Busy
from .messages import format_message

log = logging.getLogger("walink")

FREE_SLOT = "(free slot)"
LOCKED = "[locked] "
CODE_HINT = "Show the code on the computer first (Link with phone number); it expires in about a minute."
QUIT_QUESTION = "Quit after the current action finishes?"
# Actions that need the phone: disabled while one is running.
PHONE_ACTIONS = {"last", "link", "delete", "devices", "keepalive", "status"}
MENU = [
    ("last", "m", "Read the last message of a chat"),
    ("link", "l", "Link a computer (replace a device or use a free slot)"),
    ("delete", "d", "Remove a linked device"),
    ("devices", "r", "Show the linked devices"),
    ("keepalive", "k", "Keepalive (keeps the account active)"),
    ("status", "s", "Phone status"),
    ("quit", "q", "Quit"),
]
# Maintenance items: only with --debug (the CLI always has them).
DEBUG_ONLY = {"devices", "keepalive", "status"}


def _valid_code(raw: str) -> bool:
    try:
        normalize_code(raw)
        return True
    except CodeError:
        return False


class ConfirmScreen(ModalScreen[bool]):
    """Yes/No question. Focus starts on No, so Enter alone never confirms. Arrows move between the buttons."""
    BINDINGS = [("y", "answer(True)", "Yes"), ("n,escape", "answer(False)", "No"),
                Binding("left,up", "choose('no')", show=False), Binding("right,down", "choose('yes')", show=False)]

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

    def action_choose(self, button_id: str) -> None:
        self.query_one(f"#{button_id}").focus()


class ProgressScreen(ModalScreen):
    """What the phone is doing right now: a title, a bar, the current step and the elapsed time."""
    BINDINGS = [("q", "app.quit", "Quit")]

    def __init__(self, title: str):
        super().__init__()
        self.title_text = title
        self.step, self.percent = "Starting", 0
        self.started = time.monotonic()

    def compose(self) -> ComposeResult:
        with Vertical(classes="dialog"):
            yield Label(self.title_text, classes="question", markup=False)
            yield ProgressBar(total=100, show_eta=False, id="bar")
            yield Label(self.step, id="step", markup=False)
            yield Label("", id="elapsed", classes="hint")

    def on_mount(self) -> None:
        self._show()
        self._tick()
        self.set_interval(1, self._tick)

    def _tick(self) -> None:
        self.query_one("#elapsed", Label).update(
            f"{int(time.monotonic() - self.started)} s — the phone is working, please wait")

    def update_step(self, text: str, fraction: float) -> None:
        """May arrive before the dialog is drawn: keep the latest step and show it on mount."""
        self.step, self.percent = text, max(self.percent, round(fraction * 100))
        if self.is_mounted:
            self._show()

    def _show(self) -> None:
        self.query_one("#step", Label).update(self.step)
        self.query_one("#bar", ProgressBar).update(progress=self.percent)


class DevicePickScreen(ModalScreen):
    """The linked devices. mode 'link' / 'remove': pick one (returns the row dict, or FREE_SLOT); 'view': just look."""
    BINDINGS = [("escape", "cancel", "Back")]
    TITLES = {"link": "Which device should the new link replace?",
              "remove": "Which device do you want to remove?",
              "view": "Linked devices"}

    def __init__(self, info: dict, mode: str):
        super().__init__()
        self.info, self.mode = info, mode
        self.rows: list = list(info["devices"])
        if mode == "link" and info["count"] < info["max"]:
            self.rows.append(FREE_SLOT)

    def labels(self) -> list[str]:
        return [r if r == FREE_SLOT else (LOCKED if r.get("protected") else "") + r["name"] for r in self.rows]

    def compose(self) -> ComposeResult:
        with Vertical(classes="dialog wide"):
            yield Label(f"{self.TITLES[self.mode]}  ({self.info['count']} of {self.info['max']} linked)",
                        classes="question", markup=False)
            yield DataTable(id="devices", cursor_type="row", zebra_stripes=True)
            hint = "[locked] devices are protected and cannot be removed or replaced."
            if self.mode != "view":
                hint = "Arrows to choose, Enter to select. " + hint
            yield Static(hint, classes="hint", markup=False)
            with Horizontal(classes="buttons"):
                yield Button("Back" if self.mode != "view" else "Close", id="cancel")

    def on_mount(self) -> None:
        table = self.query_one("#devices", DataTable)
        table.add_columns("Device", "Status")
        for label, row in zip(self.labels(), self.rows):
            table.add_row(Text(label), Text("" if row == FREE_SLOT else row["status"]))   # names are not markup
        table.focus()

    def on_data_table_row_selected(self, event: DataTable.RowSelected) -> None:
        if self.mode == "view":
            return
        row = self.rows[event.cursor_row]
        if row != FREE_SLOT and row.get("protected"):
            self.app.notify(f"'{row['name']}' is protected", severity="warning", markup=False)
            return
        self.dismiss(row)

    def on_button_pressed(self, event: Button.Pressed) -> None:
        self.dismiss(None)

    def action_cancel(self) -> None:
        self.dismiss(None)


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
            yield Label("Last message of which chat?", classes="question")
            yield Static("Opening the chat marks it as read on the phone.", classes="hint")
            if self.chats:
                yield OptionList(*self.chats, id="quick")
            yield Input(placeholder="Or type the exact chat name, as in the chat list", id="chat")
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
    """A result or an error, until dismissed. Never logged."""
    BINDINGS = [("escape,enter,q", "close", "Close")]

    def __init__(self, title: str, text: str, error: bool = False):
        super().__init__()
        self.title_text, self.text, self.error = title, text, error

    def compose(self) -> ComposeResult:
        with Vertical(classes="dialog error" if self.error else "dialog"):
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
    """--debug: mirrors walink log lines into the log pane, from any thread."""

    def __init__(self, app: "WalinkApp"):
        super().__init__(logging.INFO)
        self.app = app
        self.setFormatter(logging.Formatter("%(asctime)s %(message)s", "%H:%M:%S"))

    def emit(self, record: logging.LogRecord) -> None:
        try:
            self.app.ui(self.app.write_log, self.format(record))
        except Exception:
            pass


class WalinkApp(App):
    TITLE = "walink"
    CSS = """
    #header { height: 1; background: $primary; color: $text; padding: 0 1; }
    #main { height: 1fr; }
    #menu { width: 1fr; height: auto; max-height: 100%; border: round $primary; margin: 1 2; }
    #log { width: 1fr; border: round $secondary; }
    ModalScreen { align: center middle; }
    .dialog { width: 72; max-width: 100%; height: auto; max-height: 100%; border: thick $primary;
              background: $surface; padding: 1 2; }
    .dialog.wide { width: 90; }
    .dialog.error { border: thick $error; }
    .question { width: 100%; margin-bottom: 1; text-style: bold; }
    .hint { color: $text-muted; margin-bottom: 1; }
    .result { margin-bottom: 1; }
    .buttons { height: auto; align: right middle; }
    .buttons Button { margin-left: 2; }
    #quick { height: auto; max-height: 8; margin-bottom: 1; }
    #devices { height: auto; max-height: 12; margin-bottom: 1; }
    #bar { margin-bottom: 1; }
    #step { margin-bottom: 1; }
    Input { margin-bottom: 1; }
    """
    BINDINGS = [
        Binding("m", "last", "Last message"),
        Binding("l", "link", "Link"),
        Binding("d", "delete", "Remove"),
        Binding("r", "devices", "Devices"),
        Binding("k", "keepalive", "Keepalive"),
        Binding("s", "status", "Status"),
        Binding("q", "quit", "Quit"),
        Binding("ctrl+c", "quit", "Quit", show=False, priority=True),
    ]

    def __init__(self, cfg: dict, label: str, serial: str | None = None, dry_run: bool = False,
                 debug: bool = False, actions: Any = walink_actions):
        super().__init__()
        self.cfg, self.label, self.serial, self.dry_run, self.debug_mode = cfg, label, serial, dry_run, debug
        self.actions = actions
        self.busy: str | None = None
        self._progress: ProgressScreen | None = None
        self._thread: threading.Thread | None = None
        self._quit_after = False
        self._pending_confirm: Callable[[bool], None] | None = None
        self._event_loop: asyncio.AbstractEventLoop | None = None
        self._main_thread = threading.get_ident()
        self._handler = _PaneHandler(self)

    # ---------- layout ----------
    def compose(self) -> ComposeResult:
        dry = "   [DRY RUN: nothing is changed]" if self.dry_run else ""
        yield Static(f"walink — WhatsApp Business linked devices   (key: {self.label}){dry}", id="header",
                     markup=False)
        with Horizontal(id="main"):
            items = [Option(f"{text}   [{key}]", id=name) for name, key, text in MENU
                     if self.debug_mode or name not in DEBUG_ONLY]
            yield OptionList(*items, id="menu")
            if self.debug_mode:
                yield RichLog(id="log", wrap=True, markup=False, highlight=False)
        yield Footer()

    def on_mount(self) -> None:
        self._event_loop = asyncio.get_running_loop()
        self._main_thread = threading.get_ident()
        self.query_one("#menu", OptionList).border_title = "What do you want to do?"
        if self.debug_mode:
            self.query_one("#log", RichLog).border_title = "Log (--debug)"
            log.addHandler(self._handler)
        if log.level == logging.NOTSET:
            log.setLevel(logging.INFO)
        self.query_one("#menu").focus()

    def on_unmount(self) -> None:
        log.removeHandler(self._handler)
        if self._pending_confirm:              # the UI is gone: an unanswered question means No
            self._pending_confirm(False)

    async def on_option_list_option_selected(self, event: OptionList.OptionSelected) -> None:
        if event.option_list.id == "menu":
            await self.run_action(event.option.id)      # a coroutine: without await nothing happens

    def write_log(self, line: str) -> None:
        self.query_one("#log", RichLog).write(line)

    # ---------- threads ----------
    def ui(self, fn: Callable, *args, **kwargs) -> Any:
        """Run `fn` on the UI thread and return its result; False if the UI is gone (e.g. after a hangup).
        Like App.call_from_thread, but it never waits forever: it gives up as soon as the app stops."""
        if threading.get_ident() == self._main_thread:
            return fn(*args, **kwargs)
        loop = self._event_loop
        if loop is None or loop.is_closed() or not self.is_running:
            return False

        async def call() -> Any:
            with self._context():
                result = fn(*args, **kwargs)
                return await result if inspect.isawaitable(result) else result
        try:
            future = asyncio.run_coroutine_threadsafe(call(), loop)
        except RuntimeError:
            return False
        while True:
            try:
                return future.result(timeout=0.2)
            except concurrent.futures.TimeoutError:
                if not self.is_running or loop.is_closed():
                    future.cancel()
                    return False
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

    def _report(self, text: str, fraction: float) -> None:
        progress = self._progress
        if progress is not None:
            self.ui(progress.update_step, text, fraction)

    def _action_context(self):
        return walink_actions.Context(cfg=self.cfg, label=self.label, confirm=self._confirm, serial=self.serial,
                                      dry_run=self.dry_run, progress=self._report)

    def check_action(self, action: str, parameters: tuple) -> bool | None:
        """Phone actions only from the menu: not while the phone works, not from inside a dialog;
        maintenance actions only with --debug."""
        if action in DEBUG_ONLY and not self.debug_mode:
            return False
        if action in PHONE_ACTIONS and (self.busy or isinstance(self.screen, ModalScreen)):
            return False
        return True

    def _start(self, name: str, title: str, fn: Callable, on_done: Callable | None = None) -> None:
        if self.busy:
            return
        self.busy = name
        self.refresh_bindings()
        self._progress = ProgressScreen(title)
        self.push_screen(self._progress)
        self._thread = threading.Thread(target=self._run_action, args=(fn, on_done), name=f"walink-{name}")
        self._thread.start()

    def _run_action(self, fn: Callable, on_done: Callable | None) -> None:
        outcome: tuple = ("error", "Something went wrong", "")
        try:
            outcome = ("ok", fn(self._action_context()))
        except Busy:
            outcome = ("error", "The phone is busy", "Someone else is using it right now. Try again in a minute.")
        except self.actions.Refused as e:
            outcome = ("refused", str(e))
        except self.actions.ActionFailed as e:
            where = f"\n\nDetails were saved in {e.evidence}" if e.evidence else ""
            outcome = ("error", "Could not finish", f"{e}{where}")
        except Exception as e:
            log.exception("unexpected error")
            outcome = ("error", "Something went wrong", str(e))
        finally:
            self.ui(self._finished, outcome, on_done)

    def _close_progress(self) -> None:
        """Pop the progress dialog and anything still open above it (e.g. an unanswered quit question)."""
        if self._progress is None:
            return
        while self.screen is not self._progress and self._progress in self.screen_stack:
            self.pop_screen()
        if self.screen is self._progress:
            self.pop_screen()
        self._progress = None

    def _finished(self, outcome: tuple, on_done: Callable | None) -> None:
        self.busy = None
        self._close_progress()
        self.refresh_bindings()
        if self._quit_after:
            self.exit()
            return
        if outcome[0] == "ok":
            if on_done:
                on_done(outcome[1])
        elif outcome[0] == "refused":
            if outcome[1].startswith("cancelled"):
                self.notify("Nothing was changed")
            else:
                self.push_screen(MessageScreen("Not done", outcome[1], error=True))
        else:
            self.push_screen(MessageScreen(outcome[1], outcome[2], error=True))

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

    def _done(self, title: str, text: str) -> None:
        self.push_screen(MessageScreen(title, text))

    # ---------- actions ----------
    def action_last(self) -> None:
        def go(chat) -> None:
            if chat:
                self._start("last", f"Reading the last message of '{chat}'",
                            lambda ctx: self.actions.last_message(ctx, chat),
                            lambda msg: self._done(f"Last message — {chat}", format_message(msg)))
        self.push_screen(LastScreen(self.actions.quick_chats(self.cfg)), go)

    def _with_devices(self, mode: str, then: Callable[[Any], None] | None) -> None:
        """Read the device list (progress dialog), then let the user pick (or just look)."""
        def picked(info: dict) -> None:
            self.push_screen(DevicePickScreen(info, mode), then)
        self._start("devices", "Reading the linked devices", self.actions.list_devices, picked)

    def action_devices(self) -> None:
        self._with_devices("view", None)

    def action_link(self) -> None:
        def chosen(row) -> None:
            if row is None:
                return
            replace = None if row == FREE_SLOT else row["name"]

            def go(result) -> None:
                if result:
                    code, name = result
                    title = f"Replacing '{replace}' with '{name}'" if replace else f"Linking '{name}'"
                    self._start("link", title,
                                lambda ctx: self.actions.link(ctx, code, replace=replace, name=name),
                                lambda res: self._done("Done", f"'{res['name']}' is linked ({res['status']})."))
            self.push_screen(LinkScreen(replace), go)
        self._with_devices("link", chosen)

    def action_delete(self) -> None:
        def chosen(row) -> None:
            if row is None:
                return
            name = row["name"]
            what = "[dry-run] would remove" if self.dry_run else "Removed"
            self._start("remove", f"Removing '{name}'", lambda ctx: self.actions.remove(ctx, name),
                        lambda _: self._done("Done", f"{what} '{name}'."))
        self._with_devices("remove", chosen)

    def action_keepalive(self) -> None:
        self._start("keepalive", "Keepalive", self.actions.keepalive,
                    lambda _: self._done("Done", "WhatsApp was opened and is active."))

    def action_status(self) -> None:
        self._start("status", "Reading the phone status", self.actions.status,
                    lambda info: self._done("Phone status", json.dumps(info, indent=2)))

    async def action_quit(self) -> None:
        if not self.busy:
            self.exit()
            return

        def answer(yes) -> None:
            if yes:
                self._quit_after = True
                self.notify("Quitting when the current action finishes")
        self.push_screen(ConfirmScreen(QUIT_QUESTION), answer)


def run(cfg: dict, label: str, serial: str | None = None, dry_run: bool = False, debug: bool = False) -> int:
    # Log lines on stderr would draw over the screen; the per-run file logs (and --debug's pane) take over.
    for handler in list(log.handlers):
        if type(handler) is logging.StreamHandler:
            log.removeHandler(handler)
    log.setLevel(logging.INFO)
    app = WalinkApp(cfg, label, serial=serial, dry_run=dry_run, debug=debug)
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
