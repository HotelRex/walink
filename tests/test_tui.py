"""The Textual TUI driven through Textual's pilot, on fake actions (no phone)."""
import asyncio
import html
import logging
import threading

from walink import actions as real_actions
from walink.lock import Busy
from walink.tui import FREE_SLOT, WalinkApp

INFO = {"count": 3, "max": 4, "devices": [
    {"name": "Hotel Rex", "status": "Active now", "protected": False},
    {"name": "Test", "status": "Last active", "protected": True},
    {"name": "Sara", "status": "Logged out", "protected": False}]}


class FakeActions:
    """Same names as walink.actions; link/remove ask ctx.confirm and report progress like the real ones."""
    Refused = real_actions.Refused
    ActionFailed = real_actions.ActionFailed
    CONFIRM_TEXT = real_actions.CONFIRM_TEXT
    quick_chats = staticmethod(real_actions.quick_chats)

    def __init__(self):
        self.calls, self.gate, self.list_error = [], None, None

    def _wait(self):
        if self.gate:
            self.gate.wait(5)

    def list_devices(self, ctx):
        self.calls.append(("list",))
        ctx.progress("Reading the linked devices", 0.6)
        if self.list_error:
            raise self.list_error
        return INFO

    def link(self, ctx, code, *, replace=None, name=None):
        self.calls.append(("link", code, replace, name))
        if replace and not ctx.confirm(self.CONFIRM_TEXT.format(name=replace)):
            self.calls.append(("declined",))
            raise self.Refused("cancelled: nothing was removed")
        ctx.progress("Entering the code", 0.5)
        self._wait()
        return {"name": name or replace, "status": "Active now"}

    def remove(self, ctx, name):
        self.calls.append(("remove", name))
        if not ctx.confirm(self.CONFIRM_TEXT.format(name=name)):
            raise self.Refused("cancelled: nothing was removed")

    def last_message(self, ctx, chat):
        self.calls.append(("last", chat))
        ctx.progress(f"Searching for the chat '{chat}'", 0.4)
        self._wait()
        return {"chat": chat, "text": "SECRET 123456", "day": "Today", "time": "1:00 PM",
                "direction": "incoming", "sender": None, "status": None, "kind": "text"}

    def keepalive(self, ctx):
        self.calls.append(("keepalive",))
        ctx.progress("Restarting WhatsApp", 0.1)
        self._wait()

    def status(self, ctx):
        return {"model": "fake"}

    def reset(self, ctx):
        pass


def make_app(fake=None, cfg=None, **kw):
    fake = fake or FakeActions()
    app = WalinkApp(cfg or {"quick_chats": ["PayPal", "Bank"]}, "pc", actions=fake, **kw)
    notes = []
    app.notify = lambda message, **k: notes.append(message)
    return app, fake, notes


def screen_name(app):
    return type(app.screen).__name__


async def wait_for(pilot, cond, what="condition"):
    for _ in range(300):
        await pilot.pause(0.01)
        if cond():
            return
    raise AssertionError(f"timed out waiting for {what}")


async def wait_screen(pilot, app, name):
    await wait_for(pilot, lambda: screen_name(app) == name, name)


def shot(app):
    return html.unescape(app.export_screenshot()).replace("\xa0", " ")


def run(coro):
    asyncio.run(coro)


def test_start_shows_menu_and_does_not_touch_the_phone():
    async def go():
        app, fake, _ = make_app()
        async with app.run_test(size=(100, 30)) as pilot:
            await pilot.pause(0.1)
            assert fake.calls == []
            text = shot(app)
            assert "Read the last message of a chat" in text and "What do you want to do?" in text
            assert not app.query("#log")                   # no logs without --debug
            for hidden in ("Show the linked devices", "Keepalive", "Phone status"):
                assert hidden not in text
            await pilot.press("r", "k", "s")
            await pilot.pause(0.1)
            assert fake.calls == [] and screen_name(app) == "Screen"
    run(go())


def test_debug_shows_log_pane_and_maintenance_items():
    async def go():
        app, _, _ = make_app(debug=True)
        async with app.run_test(size=(140, 30)) as pilot:
            await pilot.pause()
            assert app.query("#log")
            text = shot(app)
            for item in ("Show the linked devices", "Keepalive", "Phone status"):
                assert item in text
    run(go())


def test_last_message_needs_no_device_list_and_shows_progress():
    async def go():
        app, fake, _ = make_app()
        fake.gate = threading.Event()
        records = []
        handler = logging.Handler()
        handler.emit = lambda r: records.append(r.getMessage())
        logging.getLogger("walink").addHandler(handler)
        try:
            async with app.run_test(size=(100, 30)) as pilot:
                await pilot.press("m")
                await wait_screen(pilot, app, "LastScreen")
                await pilot.press("enter")                  # first quick pick: PayPal
                await wait_screen(pilot, app, "ProgressScreen")
                await wait_for(pilot, lambda: "Searching for the chat 'PayPal'" in shot(app), "step text")
                assert app.screen.query_one("#bar").progress == 40
                fake.gate.set()
                await wait_screen(pilot, app, "MessageScreen")
                assert "SECRET 123456" in app.screen.text
                assert ("list",) not in fake.calls
        finally:
            logging.getLogger("walink").removeHandler(handler)
        assert not any("SECRET" in r for r in records)
    run(go())


def test_typed_chat_name_with_unicode():
    async def go():
        app, fake, _ = make_app()
        async with app.run_test(size=(100, 30)) as pilot:
            await pilot.press("m")
            await wait_screen(pilot, app, "LastScreen")
            app.screen.query_one("#chat").value = "Zoë D'Amico"
            await pilot.click("#read")
            await wait_screen(pilot, app, "MessageScreen")
            assert ("last", "Zoë D'Amico") in fake.calls
    run(go())


def test_link_picker_shows_locked_and_free_slot_and_refuses_locked():
    async def go():
        app, fake, notes = make_app()
        async with app.run_test(size=(110, 32)) as pilot:
            await pilot.press("l")
            await wait_screen(pilot, app, "DevicePickScreen")
            assert app.screen.labels() == ["Hotel Rex", "[locked] Test", "Sara", FREE_SLOT]
            assert "[locked] Test" in shot(app)            # drawn as text, not eaten as markup
            await pilot.press("down", "enter")             # the locked row
            await pilot.pause()
            assert screen_name(app) == "DevicePickScreen" and any("protected" in n for n in notes)
    run(go())


def test_link_free_slot_with_pasted_code():
    async def go():
        app, fake, _ = make_app()
        async with app.run_test(size=(110, 32)) as pilot:
            await pilot.press("l")
            await wait_screen(pilot, app, "DevicePickScreen")
            await pilot.press("down", "down", "down", "enter")    # (free slot)
            await wait_screen(pilot, app, "LinkScreen")
            screen = app.screen
            screen.query_one("#code").value = " 47nh-1g3t "
            await pilot.pause()
            assert screen.query_one("#run").disabled              # free slot: a name is required
            screen.query_one("#name").value = "Desk 2"
            await pilot.pause()
            assert not screen.query_one("#run").disabled
            await pilot.click("#run")
            await wait_screen(pilot, app, "MessageScreen")
            assert ("link", "47NH1G3T", None, "Desk 2") in fake.calls
            assert "'Desk 2' is linked" in app.screen.text
    run(go())


def test_short_code_keeps_link_disabled():
    async def go():
        app, fake, _ = make_app()
        async with app.run_test(size=(110, 32)) as pilot:
            await pilot.press("l")
            await wait_screen(pilot, app, "DevicePickScreen")
            await pilot.press("enter")                           # Hotel Rex
            await wait_screen(pilot, app, "LinkScreen")
            assert app.screen.query_one("#name").value == "Hotel Rex"
            await pilot.press(*"47NH1G3")
            assert app.screen.query_one("#run").disabled
            await pilot.press("t")
            assert not app.screen.query_one("#run").disabled
    run(go())


def test_replace_confirm_default_no():
    async def go():
        app, fake, notes = make_app()
        async with app.run_test(size=(110, 32)) as pilot:
            await pilot.press("l")
            await wait_screen(pilot, app, "DevicePickScreen")
            await pilot.press("enter")
            await wait_screen(pilot, app, "LinkScreen")
            app.screen.query_one("#code").value = "ABCDEFGH"
            await pilot.pause()
            await pilot.click("#run")
            await wait_screen(pilot, app, "ConfirmScreen")
            assert app.screen.question == "Are you sure you want to delete Hotel Rex?"
            await pilot.press("enter")                           # focus starts on No
            await wait_for(pilot, lambda: app.busy is None, "action end")
            assert ("declined",) in fake.calls and "Nothing was changed" in notes
    run(go())


def test_remove_confirmed():
    async def go():
        app, fake, _ = make_app()
        async with app.run_test(size=(110, 32)) as pilot:
            await pilot.press("d")
            await wait_screen(pilot, app, "DevicePickScreen")
            assert FREE_SLOT not in app.screen.labels()
            await pilot.press("down", "down", "enter")           # Sara
            await wait_screen(pilot, app, "ConfirmScreen")
            await pilot.press("y")
            await wait_screen(pilot, app, "MessageScreen")
            assert ("remove", "Sara") in fake.calls and "Removed 'Sara'" in app.screen.text
    run(go())


def test_keys_disabled_while_busy_and_quit_waits():
    async def go():
        app, fake, _ = make_app(debug=True)
        fake.gate = threading.Event()
        exits = []
        async with app.run_test(size=(100, 30)) as pilot:
            app.exit = lambda *a, **k: exits.append(1)
            await pilot.press("k")
            await wait_screen(pilot, app, "ProgressScreen")
            await pilot.press("s")
            await pilot.pause(0.05)
            assert screen_name(app) == "ProgressScreen"           # status did not start
            await pilot.press("q")
            await wait_screen(pilot, app, "ConfirmScreen")
            assert app.screen.question == "Quit after the current action finishes?"
            await pilot.press("y")
            await pilot.pause()
            assert exits == []                                    # not while the phone works
            fake.gate.set()
            await wait_for(pilot, lambda: exits, "exit")
    run(go())


def test_hangup_waits_for_running_action():
    async def go():
        app, fake, _ = make_app(debug=True)
        fake.gate = threading.Event()
        exits = []
        async with app.run_test(size=(100, 30)) as pilot:
            app.exit = lambda *a, **k: exits.append(1)
            await pilot.press("k")
            await wait_screen(pilot, app, "ProgressScreen")
            app.hangup()
            await pilot.pause(0.05)
            assert exits == [] and app.busy == "keepalive"
            fake.gate.set()
            await wait_for(pilot, lambda: exits, "exit")
    run(go())


def test_hangup_during_confirm_answers_no():
    async def go():
        app, fake, _ = make_app()
        async with app.run_test(size=(110, 32)) as pilot:
            app.exit = lambda *a, **k: None
            await pilot.press("d")
            await wait_screen(pilot, app, "DevicePickScreen")
            await pilot.press("down", "down", "enter")
            await wait_screen(pilot, app, "ConfirmScreen")
            app.hangup()
            await wait_for(pilot, lambda: app.busy is None, "action end")
            assert ("remove", "Sara") in fake.calls and screen_name(app) != "MessageScreen"
    run(go())


def test_busy_phone_shows_a_clear_message():
    async def go():
        fake = FakeActions()
        fake.list_error = Busy("in use")
        app, _, _ = make_app(fake)
        async with app.run_test(size=(100, 30)) as pilot:
            await pilot.press("d")
            await wait_screen(pilot, app, "MessageScreen")
            assert app.screen.title_text == "The phone is busy"
    run(go())


def test_enter_on_menu_items_opens_them():
    async def go():
        app, fake, _ = make_app()
        async with app.run_test(size=(100, 30)) as pilot:
            await pilot.pause()
            await pilot.press("enter")                              # 1st item: last message
            await wait_screen(pilot, app, "LastScreen")
            await pilot.press("escape")
            await wait_screen(pilot, app, "Screen")
            await pilot.press("down", "enter")                      # 2nd item: link -> device picker
            await wait_screen(pilot, app, "DevicePickScreen")
            assert ("list",) in fake.calls
    run(go())


def test_enter_on_quit_item_exits():
    async def go():
        app, fake, _ = make_app()
        exits = []
        async with app.run_test(size=(100, 30)) as pilot:
            app.exit = lambda *a, **k: exits.append(1)
            await pilot.press("end", "enter")                       # last item: Quit
            await wait_for(pilot, lambda: exits, "exit")
    run(go())


def test_arrows_move_between_no_and_yes():
    async def go():
        app, fake, _ = make_app()
        async with app.run_test(size=(110, 32)) as pilot:
            await pilot.press("d")
            await wait_screen(pilot, app, "DevicePickScreen")
            await pilot.press("down", "down", "enter")           # Sara
            await wait_screen(pilot, app, "ConfirmScreen")
            confirm = app.screen
            assert app.focused.id == "no"
            await pilot.press("right")
            assert app.focused.id == "yes"
            await pilot.press("left")
            assert app.focused.id == "no"
            await pilot.press("down")
            assert app.focused.id == "yes"
            await pilot.press("up")
            assert app.focused.id == "no"
            await pilot.press("right", "right")                  # stays on Yes, no wrap-around
            assert app.focused.id == "yes"
            await pilot.press("enter")
            await wait_screen(pilot, app, "MessageScreen")
            assert ("remove", "Sara") in fake.calls and confirm.is_attached is False
    run(go())


def test_closing_the_app_during_a_question_answers_no():
    async def go():
        app, fake, _ = make_app()
        async with app.run_test(size=(110, 32)) as pilot:
            await pilot.press("d")
            await wait_screen(pilot, app, "DevicePickScreen")
            await pilot.press("down", "down", "enter")
            await wait_screen(pilot, app, "ConfirmScreen")
        # the app is closed with the question still open
        app._thread.join(5)
        assert not app._thread.is_alive()
    run(go())
