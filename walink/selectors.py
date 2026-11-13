"""ALL UI selectors live here. When WhatsApp changes its UI, edit only this file.
Priority: resource-id, then content-desc, then text. Values are uiautomator2 kwargs.
Verified on WhatsApp Business 2.26.43.72, English UI, Huawei Android 10."""
PKG = "com.whatsapp.w4b"


def _rid(name: str) -> str:
    return f"{PKG}:id/{name}"


def _id(name: str) -> dict:
    return {"resourceId": _rid(name)}


def code_box(i: int) -> dict:
    """The i-th (1-based) box of the 8-character link code."""
    return {"description": f"Enter 8-character code, {i} of 8"}


# Activities (used to know which screen we are on)
ACT_HOME = "com.whatsapp.home.ui.HomeActivity"
ACT_LAUNCHER = "com.whatsapp.Main"                  # launcher entry (resolve-activity --brief)
ACT_LINKED = "com.whatsapp.smbmultideviceagents.view.activity.BizAgentDevicesActivity"
ACT_EDIT = "com.whatsapp.smbmultideviceagents.view.activity.AgentDeviceInfoActivity"
ACT_CODE = "com.whatsapp.companiondevice.LinkedDevicesEnterCodeActivity"
ACT_NAME = "com.whatsapp.smbmultideviceagents.view.activity.SetDeviceNameActivity"

OVERFLOW = _id("menuitem_overflow")
MENU_LINKED_DEVICES = _id("menuitem_scan_qr")       # id is misleading; text is "Linked devices"
HEADER_COUNT = _id("header_text")                   # "3 of 4 devices linked."
LINK_BUTTON = _id("link_device_button")
ROW_NAME_ID, ROW_STATUS_ID = _rid("name"), _rid("status")
EDIT_DEVICE_NAME = _id("device_name")
EDIT_MENU_REMOVE = {"text": "Remove device"}
DIALOG_BUTTON_OK = {"resourceId": "android:id/button1"}   # OK / Remove / confirm
DIALOG_TITLE = _id("alertTitle")                    # app namespace, not android:
DIALOG_MESSAGE = {"resourceId": "android:id/message"}   # e.g. "To link more devices..." when 4/4
SCANNER_PHONE_NUMBER = _id("bottom_banner")         # "Link with phone number instead"
NAME_FIELD = {"className": "android.widget.EditText"}
NAME_SAVE = _id("save_device_name_btn")

# Dialog texts
TXT_REMOVE_TITLE = "Remove device?"

# Chats ("last" command). Verified on the same build.
ACT_CHAT = "com.whatsapp.Conversation"
SEARCH_BAR = _id("search_bar_inner_layout")         # chat-list search pill
SEARCH_INPUT = _id("search_input")                  # its text is prefixed with U+200B
CHAT_ROW_NAME_ID = _rid("conversations_row_contact_name")   # chat list + search results
CHAT_HEADER_NAME = _id("conversation_contact_name") # title inside an open chat
CHAT_SCROLL_BOTTOM = _id("scroll_bottom")           # "Go to most recent message" (chat opened at first unread)
MSG_LIST_ID = "android:id/list"                     # ListView of message rows (android namespace)
MSG_BODY_IDS = (_rid("message_text"),               # normal text bubble
                _rid("bottom_message"),             # business/template message (OTP codes)
                _rid("caption"))                    # media caption
MSG_DATE_ID = _rid("date")                          # every real message has one
MSG_STATUS_ID = _rid("status")                      # ticks: only on outgoing messages ("Read", "Delivered"...)
MSG_SENDER_ID = _rid("name_in_group_tv")            # sender in group chats
MSG_QUOTE_ID = _rid("quoted_message_holder")        # quoted reply: never the message body
MSG_DAY_ID = _rid("conversation_row_date_divider")  # "Today", "Monday", "May 4, 2025"
