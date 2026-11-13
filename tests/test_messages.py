"""walink.messages on synthetic hierarchies shaped like the real WhatsApp Business 2.26.43.72 dumps."""
import xml.etree.ElementTree as ET
import pytest
from walink.messages import last_message, format_message

P = "com.whatsapp.w4b:id/"

def node(rid="", text="", desc="", top=0, kids=""):
    return (f'<node resource-id="{rid}" text="{text}" content-desc="{desc}" class="x" '
            f'bounds="[0,{top}][720,{top + 50}]">{kids}</node>')

def screen(*rows):
    return ET.fromstring("<hierarchy>" + node("android:id/list", top=161, kids="".join(rows)) + "</hierarchy>")

def text_row(top, body, time, status=None, quote=None, sender=None, day=None):
    k = ""
    if day: k += node(P + "conversation_row_date_divider", day, top=top)
    if sender: k += node(P + "name_in_group_tv", sender, top=top + 5)
    if quote: k += node(P + "quoted_message_holder", top=top + 8, kids=node(P + "quoted_text", quote, top=top + 9))
    k += node(P + "message_text", body, top=top + 10)
    k += node(P + "date", time, top=top + 20)
    if status: k += node(P + "status", desc=status, top=top + 20)
    return node(P + "conversation_row_text", top=top, kids=k)

def test_last_incoming_with_day():
    m = last_message(screen(text_row(200, "old", "9:00 AM", status="Read", day="Monday"),
                            text_row(400, "newest", "1:24 PM", day="Today")))
    assert (m["text"], m["day"], m["time"], m["direction"]) == ("newest", "Today", "1:24 PM", "incoming")

def test_outgoing_and_day_inherited_from_earlier_divider():
    m = last_message(screen(text_row(200, "a", "9:00 AM", day="Monday"), text_row(400, "b", "9:05 AM", status="Read")))
    assert (m["text"], m["day"], m["direction"], m["status"]) == ("b", "Monday", "outgoing", "Read")

def test_quote_is_not_the_body_and_group_sender():
    m = last_message(screen(text_row(200, "reply", "9:00 AM", quote="quoted words", sender="Ann")))
    assert (m["text"], m["sender"]) == ("reply", "Ann")
    assert format_message({"chat": "Group", **m}) == "[9:00 AM] Ann: reply"

def test_template_otp_message():
    row = node(P + "template_message", top=500, kids=node(P + "bottom_message", "123456 is your code", top=510)
               + node(P + "date", "1:20 PM", top=600))
    assert last_message(screen(text_row(200, "x", "1:00 PM"), row))["text"] == "123456 is your code"

def test_system_notice_after_messages_is_skipped():
    notice = node("", top=900, kids=node(P + "info", "Messages are end-to-end encrypted", top=900))
    assert last_message(screen(text_row(200, "real", "1:00 PM"), notice))["text"] == "real"

def test_non_text_last_message():
    media = node(P + "conversation_row_image", top=600, kids=node(P + "date", "2:00 PM", top=640))
    m = last_message(screen(text_row(200, "x", "1:00 PM"), media))
    assert m["kind"] == "non-text" and m["text"] is None
    assert format_message({"chat": "Bob", **m}) == "[2:00 PM] Bob: (non-text message)"

def test_no_list_or_no_messages():
    with pytest.raises(ValueError): last_message(ET.fromstring("<hierarchy/>"))
    with pytest.raises(ValueError): last_message(screen())
