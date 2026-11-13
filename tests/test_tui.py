"""The Textual TUI driven through Textual's pilot, on fake actions (no phone)."""
import asyncio
import logging
import threading

from walink import actions as real_actions
from walink.lock import Busy
from walink.tui import WalinkApp, FREE_SLOT

INFO = {"count": 3, "max": 4, "devices": [
    {"name": "Hotel Rex", "status": "Active now", "protected": False},
    {"name": "Test", "status": "Last active", "protected": True},
    {"name": "Sara", "status": "Logged out", "protected": False}]}


class FakeActions:
    """Same names as walink.actions; link/remove ask ctx.confirm like the real ones."""
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
        if self.list_error:
            raise self.list_error
        return INFO

    def link(self, ctx, code, *, replace=None, name=None):
        self.calls.append(("link", code, replace, name))
        if replace and not ctx.confirm(self.CONFIRM_TEXT.format(name=replace)):
            self.calls.append(("declined",))
            raise self.Refused("cancelled: nothing was removed")
        self._wait()
        return {"name": name or replace, "status": "Active now"}

    def remove(self, ctx, name):
        self.calls.append(("remove", name))
        if not ctx.confirm(self.CONFIRM_TEXT.format(name=name)):
            raise self.Refused("cancelled")

    def last_message(self, ctx, chat):
        self.calls.append(("last", chat))
        return {"chat": chat, "text": "SECRET 123456", "day": "Today", "time": "1:00 PM",
                "direction": "incoming", "sender": None, "status": None, "kind": "text"}

    def keepalive(self, ctx):
        self.calls.append(("keepalive",))
        self._wait()

    def status(self, ctx):
        return {"model": "fake"}

    def reset(self, ctx):
        pass


def make_app(fake=None, cfg=None):
    fake = fake or FakeActions()
    app = WalinkApp(cfg or {"quick_chats": ["PayPal", "Bank"]}, "pc", actions=fake)
    notes = []
    app.notify = lambda message, **kw: notes.append(message)
    return app, fake, notes


async def settle(pilot, app):
    for _ in range(200):
        await pilot.pause(0.01)
        if app.busy is None:
            return
    raise AssertionError("action did not finish")


def run(coro):
    asyncio.run(coro)


def test_list_shows_locked_and_free_slot():
    async def go():
        app, fake, _ = make_app()
        async with app.run_test(size=(100, 30)) as pilot:
            await settle(pilot, app)
            assert app.row_labels() == ["Hotel Rex", "[locked] Test", "Sara", FREE_SLOT]
            screen = app.export_screenshot().replace("&#160;", " ")
            assert "[locked] Test" in screen                  # drawn as text, not eaten as markup
    run(go())


def test_code_input_normalizes_and_gates_run():
    async def go():
        app, fake, _ = make_app()
        async with app.run_test(size=(100, 30)) as pilot:
            await settle(pilot, app)
            await pilot.press("l")                          # cursor on Hotel Rex -> replace
            screen = app.screen
            await pilot.click("#code")
            await pilot.press(*"47NH1G3")
            assert screen.query_one("#run").disabled
            await pilot.press("t")                          # 8th char, lowercase is fine
            assert not screen.query_one("#run").disabled
    run(go())


def test_link_with_pasted_code():
    async def go():
        app, fake, _ = make_app()
        async with app.run_test(size=(100, 30)) as pilot:
            await settle(pilot, app)
            await pilot.press("down", "down", "down", "l")  # (free slot)
            screen = app.screen
            screen.query_one("#code").value = " 47nh-1g3t "
            assert screen.query_one("#run").disabled         # free slot: a name is required
            screen.query_one("#name").value = "Desk 2"
            await pilot.pause()
            assert not screen.query_one("#run").disabled
            await pilot.click("#run")
            await settle(pilot, app)
            assert ("link", "47NH1G3T", None, "Desk 2") in fake.calls
    run(go())


def test_replace_prefills_name_and_confirm_default_no():
    async def go():
        app, fake, _ = make_app()
        async with app.run_test(size=(100, 30)) as pilot:
            await settle(pilot, app)
            await pilot.press("l")
            screen = app.screen
            assert screen.query_one("#name").value == "Hotel Rex"
            screen.query_one("#code").value = "ABCDEFGH"
            await pilot.pause()
            await pilot.click("#run")
            for _ in range(100):
                await pilot.pause(0.01)
                if type(app.screen).__name__ == "ConfirmScreen":
                    break
            assert app.screen.question == "Are you sure you want to delete Hotel Rex?"
            await pilot.press("enter")                      # focus starts on No
            await settle(pilot, app)
            assert ("declined",) in fake.calls
    run(go())


def test_delete_locked_row_does_nothing():
    async def go():
        app, fake, notes = make_app()
        async with app.run_test(size=(100, 30)) as pilot:
            await settle(pilot, app)
            await pilot.press("down", "d", "l")
            await pilot.pause()
            assert not any(c[0] in ("remove", "link") for c in fake.calls)
            assert any("protected" in n for n in notes)
    run(go())


def test_delete_confirmed():
    async def go():
        app, fake, _ = make_app()
        async with app.run_test(size=(100, 30)) as pilot:
            await settle(pilot, app)
            await pilot.press("down", "down", "d")
            for _ in range(100):
                await pilot.pause(0.01)
                if type(app.screen).__name__ == "ConfirmScreen":
                    break
            await pilot.press("y")
            await settle(pilot, app)
            assert ("remove", "Sara") in fake.calls
            assert "Sara" not in app.row_labels()
    run(go())


def test_keys_disabled_while_busy_and_quit_waits():
    async def go():
        app, fake, _ = make_app()
        fake.gate = threading.Event()
        exits = []
        async with app.run_test(size=(100, 30)) as pilot:
            await settle(pilot, app)
            app.exit = lambda *a, **k: exits.append(1)
            await pilot.press("k")
            await pilot.pause(0.05)
            assert app.busy == "keepalive"
            await pilot.press("s")
            await pilot.pause(0.05)
            assert type(app.screen).__name__ != "MessageScreen"     # status did not run
            await pilot.press("q")
            await pilot.pause()
            assert app.screen.question == "Quit after the current action finishes?"
            await pilot.press("y")
            await pilot.pause()
            assert exits == []                                      # not while the phone works
            fake.gate.set()
            await settle(pilot, app)
            await pilot.pause(0.05)
            assert exits
    run(go())


def test_hangup_waits_for_running_action():
    async def go():
        app, fake, _ = make_app()
        fake.gate = threading.Event()
        exits = []
        async with app.run_test(size=(100, 30)) as pilot:
            await settle(pilot, app)
            app.exit = lambda *a, **k: exits.append(1)
            await pilot.press("k")
            await pilot.pause(0.05)
            app.hangup()
            await pilot.pause(0.05)
            assert exits == [] and app.busy == "keepalive"
            fake.gate.set()
            await settle(pilot, app)
            await pilot.pause(0.05)
            assert exits
    run(go())


def test_hangup_during_confirm_answers_no():
    async def go():
        app, fake, _ = make_app()
        async with app.run_test(size=(100, 30)) as pilot:
            await settle(pilot, app)
            app.exit = lambda *a, **k: None
            await pilot.press("down", "down", "d")
            for _ in range(100):
                await pilot.pause(0.01)
                if type(app.screen).__name__ == "ConfirmScreen":
                    break
            app.hangup()
            await settle(pilot, app)
            assert "Sara" in app.row_labels()                       # refused: still there
    run(go())


def test_last_message_quick_pick_and_unicode():
    async def go():
        app, fake, _ = make_app()
        records = []
        handler = logging.Handler()
        handler.emit = lambda r: records.append(r.getMessage())
        logging.getLogger("walink").addHandler(handler)
        try:
            async with app.run_test(size=(100, 30)) as pilot:
                await settle(pilot, app)
                await pilot.press("m")
                await pilot.pause()
                await pilot.press("enter")                          # first quick pick: PayPal
                await settle(pilot, app)
                assert ("last", "PayPal") in fake.calls
                assert "SECRET 123456" in app.screen.text
                await pilot.press("escape")
                await pilot.press("m")
                await pilot.pause()
                app.screen.query_one("#chat").value = "Zoë D'Amico"
                await pilot.click("#read")
                await settle(pilot, app)
                assert ("last", "Zoë D'Amico") in fake.calls
        finally:
            logging.getLogger("walink").removeHandler(handler)
        assert not any("SECRET" in r for r in records)
    run(go())


def test_busy_shows_notification():
    async def go():
        fake = FakeActions()
        fake.list_error = Busy("in use")
        app, _, notes = make_app(fake)
        async with app.run_test(size=(100, 30)) as pilot:
            await settle(pilot, app)
            assert "Phone busy, try again" in notes
    run(go())
