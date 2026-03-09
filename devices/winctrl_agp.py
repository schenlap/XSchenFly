
from enum import Enum, IntEnum
import math
import hid

from threading import Thread, Event
from time import sleep
from datetime import datetime, timedelta, timezone

import xp_websocket

# XSchenFly device module for the WINCTRL AGP 32 / AGP.
#
# Primary reverse-engineering / implementation reference:
# https://github.com/verres1/winwing-xplane-plugin/tree/main/src/include/products/agp
#
# This module ports the most relevant AGP behavior into the same overall shape
# as XSchenFly's existing Python devices:
# - HID button parsing
# - ToLiss AGP button mappings
# - LED protocol / annunciator updates
# - 14-digit LCD segment updates (chrono / UTC-date / ET)
#
# Confirmed from the referenced AGP implementation:
# - Product identifier byte: 0x80
# - LED write packet: [0x02, 0x80, 0xBB, 0x00, 0x00, 0x03, 0x49, led, brightness, ...]
# - LCD packets are 64-byte reports with command 0x35 + commit packet 0x11
# - Input report 1 uses bytes 1..8 for a 64-bit low button bitfield and
#   bytes 9..12 for a 32-bit high button bitfield
#
# What may still need local adjustment in your XSchenFly environment:
# - exact VID/PID if your AGP variant differs
# - websocket behavior for array datarefs in your local xp_websocket build
# - captain/first officer terrain target preference


BUTTONS_CNT = 25
VALID_REPORT_LENGTHS = {14, 33, 64}
REPORT_ID = 1
TERRAIN_ND_PREFERENCE = "first_officer"  # "captain" or "first_officer"


class DEVICEMASK(IntEnum):
    NONE = 0
    AGP32 = 0x01


class BUTTON(Enum):
    SWITCH = 0
    TOGGLE = 1
    SEND_0 = 3
    SEND_1 = 4
    NONE = 99


class DREF_TYPE(Enum):
    DATA = 0
    CMD_SHORT = 1
    CMD_ON_OFF = 2
    ARRAY_0 = 10
    ARRAY_1 = 11
    ARRAY_2 = 12
    ARRAY_3 = 13
    ARRAY_4 = 14
    ARRAY_5 = 15
    ARRAY_6 = 16
    ARRAY_7 = 17
    DATA_MULTIPLE = 26
    SPECIAL = 98
    NONE = 99


class Button:
    def __init__(self, pin_nr, label, dataref=None, dreftype=DREF_TYPE.DATA, button_type=BUTTON.NONE, value=None):
        self.label = label
        self.pin_nr = pin_nr
        self.dataref = dataref
        self.dreftype = dreftype
        self.type = button_type
        self.value = value

    def __str__(self):
        return f"{self.label} -> {self.dataref} {self.type}"


class Led:
    def __init__(self, label, nr, dataref, dreftype=DREF_TYPE.NONE, eval=None):
        self.label = label
        self.nr = nr
        self.dataref = dataref
        self.dreftype = dreftype
        self.eval = eval

    def __str__(self):
        return f"{self.label} -> {self.dataref}"


class Leds(IntEnum):
    BACKLIGHT = 0
    LCD_BRIGHTNESS = 1
    OVERALL_LEDS_BRIGHTNESS = 2
    LDG_GEAR_UNLK_LEFT = 3
    LDG_GEAR_UNLK_CENTER = 4
    LDG_GEAR_UNLK_RIGHT = 5
    BRAKE_FAN_HOT = 6
    LDG_GEAR_ARROW_GREEN_LEFT = 7
    LDG_GEAR_ARROW_GREEN_CENTER = 8
    LDG_GEAR_ARROW_GREEN_RIGHT = 9
    BRAKE_FAN_ON = 10
    AUTOBRK_DECEL_LO = 11
    AUTOBRK_DECEL_MED = 12
    AUTOBRK_DECEL_HI = 13
    AUTOBRK_LO_ON = 14
    AUTOBRK_MED_ON = 15
    AUTOBRK_HI_ON = 16
    TERRAIN_ON = 17
    LDG_GEAR_LEVER_RED = 18


# Standard 7-segment masks: bits A..G in positions 0..6.
SEGMENT_MASKS = {
    " ": 0b0000000,
    "-": 0b1000000,
    "_": 0b0001000,
    "0": 0b0111111,
    "1": 0b0000110,
    "2": 0b1011011,
    "3": 0b1001111,
    "4": 0b1100110,
    "5": 0b1101101,
    "6": 0b1111101,
    "7": 0b0000111,
    "8": 0b1111111,
    "9": 0b1101111,
}


values_processed = Event()
xplane_connected = False
buttonlist = []
ledlist = []
buttons_press_event = [0] * BUTTONS_CNT
buttons_release_event = [0] * BUTTONS_CNT


def eval_data(value, eval_string):
    if not eval_string:
        return value
    expr = eval_string.replace("$", "value")
    return eval(expr)


def get_cached_value(name, default=0):
    if not hasattr(xp, "datacache"):
        return default
    return xp.datacache.get(name, default)


def set_cached_value(name, value):
    if hasattr(xp, "datacache"):
        xp.datacache[name] = value


def fix_string_length(value, expected_length):
    value = str(value)
    if len(value) < expected_length:
        value = (" " * (expected_length - len(value))) + value
    if len(value) > expected_length:
        value = value[-expected_length:]
    return value


def get_segment_mask(ch):
    return SEGMENT_MASKS.get(ch, SEGMENT_MASKS[" "])


def is_annun_test():
    return int(get_cached_value("AirbusFBW/AnnunMode", 0) or 0) == 2


def terrain_pref_is_captain():
    return TERRAIN_ND_PREFERENCE.strip().lower() == "captain"


def parse_segment(text, expected_length, out_digits, colon_mask, digit_offset):
    digits = ""
    local_colon_mask = 0

    for c in str(text):
        if c in [":", "."]:
            if digits:
                if expected_length >= 6:
                    if c == ":":
                        local_colon_mask |= (1 << (len(digits) - 1))
                    local_colon_mask |= (1 << len(digits))
                else:
                    if c == ":":
                        local_colon_mask |= (1 << len(digits))
                    local_colon_mask |= (1 << (len(digits) + 1))
        else:
            digits += c

    padding_amount = 0
    if len(digits) < expected_length:
        padding_amount = expected_length - len(digits)

    colon_mask |= (local_colon_mask << padding_amount) << digit_offset

    while len(digits) < expected_length:
        digits = " " + digits
    if len(digits) > expected_length:
        digits = digits[-expected_length:]

    out_digits += digits
    return out_digits, colon_mask


def format_agp_displays():
    chrono = ""
    chrono_seconds = float(get_cached_value("AirbusFBW/ClockChronoValue", 0) or 0)
    if chrono_seconds > 0:
        total_seconds = int(math.floor(chrono_seconds))
        mins = total_seconds // 60
        secs = total_seconds % 60
        chrono = f"{mins:02d}:{secs:02d}" #fix_string_length(mins, 2) + ":" + fix_string_length(secs, 2)

    button_anims = get_cached_value("AirbusFBW/ChronoButtonAnimations", [])
    date_button_pressed = isinstance(button_anims, list) and len(button_anims) > 2 and float(button_anims[2]) > 0

    if date_button_pressed:
        day_of_year = int(get_cached_value("sim/time/local_date_days", 0) or 0) + 1
        now = datetime.now()
        jan1 = datetime(now.year, 1, 1)
        date_value = jan1 + timedelta(days=max(day_of_year - 1, 0))
        utc = f"{date_value.month:02d}:{date_value.day:02d}:{date_value.year % 100:02d}"
    else:
        zulu_time = float(get_cached_value("sim/time/zulu_time_sec", 0) or 0)
        hours = int(zulu_time // 3600) % 24
        minutes = int(zulu_time // 60) % 60
        seconds = int(zulu_time) % 60
        utc = f"{hours:02d}:{minutes:02d}:{seconds:02d}"

    elapsed_time = ""
    if int(get_cached_value("AirbusFBW/ClockShowsET", 0) or 0):
        hours = int(get_cached_value("AirbusFBW/ClockETHours", 0) or 0)
        minutes = int(get_cached_value("AirbusFBW/ClockETMinutes", 0) or 0)
        elapsed_time = f"{hours:02d}:{minutes:02d}"

    if is_annun_test():
        chrono = "88:88"
        utc = "88:88:88"
        elapsed_time = "88:88"

    return chrono, utc, elapsed_time


def update_led_state():
    if not xplane_connected:
        return

    has_power = int(get_cached_value("sim/cockpit/electrical/avionics_on", 0) or 0) != 0
    panel_brightness = float(get_cached_value("AirbusFBW/PanelBrightnessLevel", 0) or 0)

    backlight_brightness = int(panel_brightness * 255) if has_power else 0
    display_manager.set_led(Leds.BACKLIGHT, backlight_brightness)
    display_manager.set_led(Leds.LCD_BRIGHTNESS, 255 if has_power else 0)
    display_manager.set_led(Leds.OVERALL_LEDS_BRIGHTNESS, 255 if has_power else 0)

    annun_test = is_annun_test()

    nose = int(get_cached_value("AirbusFBW/NoseGearInd", 0) or 0)
    left = int(get_cached_value("AirbusFBW/LeftGearInd", 0) or 0)
    right = int(get_cached_value("AirbusFBW/RightGearInd", 0) or 0)

    display_manager.set_led(Leds.LDG_GEAR_UNLK_CENTER, 1 if (nose & 1 or annun_test) else 0)
    display_manager.set_led(Leds.LDG_GEAR_ARROW_GREEN_CENTER, 1 if (nose & 2 or annun_test) else 0)
    display_manager.set_led(Leds.LDG_GEAR_UNLK_LEFT, 1 if (left & 1 or annun_test) else 0)
    display_manager.set_led(Leds.LDG_GEAR_ARROW_GREEN_LEFT, 1 if (left & 2 or annun_test) else 0)
    display_manager.set_led(Leds.LDG_GEAR_UNLK_RIGHT, 1 if (right & 1 or annun_test) else 0)
    display_manager.set_led(Leds.LDG_GEAR_ARROW_GREEN_RIGHT, 1 if (right & 2 or annun_test) else 0)

    if terrain_pref_is_captain():
        terrain = int(get_cached_value("AirbusFBW/TerrainSelectedND1", 0) or 0) != 0
    else:
        terrain = int(get_cached_value("AirbusFBW/TerrainSelectedND2", 0) or 0) != 0
    display_manager.set_led(Leds.TERRAIN_ON, 1 if (terrain or annun_test) else 0)

    abrk_lo = int(get_cached_value("AirbusFBW/AutoBrkLo", 0) or 0)
    abrk_med = int(get_cached_value("AirbusFBW/AutoBrkMed", 0) or 0)
    abrk_hi = int(get_cached_value("AirbusFBW/AutoBrkMax", 0) or 0)

    display_manager.set_led(Leds.AUTOBRK_LO_ON, 1 if (annun_test or abrk_lo >= 1) else 0)
    display_manager.set_led(Leds.AUTOBRK_MED_ON, 1 if (annun_test or abrk_med >= 1) else 0)
    display_manager.set_led(Leds.AUTOBRK_HI_ON, 1 if (annun_test or abrk_hi >= 1) else 0)
    display_manager.set_led(Leds.AUTOBRK_DECEL_LO, 1 if (annun_test or abrk_lo == 2) else 0)
    display_manager.set_led(Leds.AUTOBRK_DECEL_MED, 1 if (annun_test or abrk_med == 2) else 0)
    display_manager.set_led(Leds.AUTOBRK_DECEL_HI, 1 if (annun_test or abrk_hi == 2) else 0)

    brake_fan = int(get_cached_value("AirbusFBW/BrakeFan", 0) or 0) != 0
    display_manager.set_led(Leds.BRAKE_FAN_ON, 1 if (brake_fan or annun_test) else 0)

    ata32_raw = get_cached_value("AirbusFBW/OHPLightsATA32_Raw", [])
    brakes_hot = isinstance(ata32_raw, list) and len(ata32_raw) > 11 and float(ata32_raw[11]) > 0
    display_manager.set_led(Leds.BRAKE_FAN_HOT, 1 if (brakes_hot or annun_test) else 0)

    display_manager.set_led(Leds.LDG_GEAR_LEVER_RED, 255 if annun_test else 0)


def update_lcd():
    if not xplane_connected:
        return
    chrono, utc, elapsed_time = format_agp_displays()
    display_manager.set_lcd_text(chrono, utc, elapsed_time)


def agp_button_event():
    global xp
    for b in buttonlist:
        if not any(buttons_press_event) and not any(buttons_release_event):
            break
        if b.pin_nr is None:
            continue

        if buttons_press_event[b.pin_nr]:
            buttons_press_event[b.pin_nr] = 0
            print(f"[AGP32] button {b.label} pressed")

            if b.dreftype == DREF_TYPE.CMD_SHORT and b.dataref:
                xp.command_activate_duration(xp.buttonref_ids[b], 0.15)
            elif b.dreftype == DREF_TYPE.DATA and b.dataref is not None and b.value is not None:
                xp.dataref_set_value(xp.buttonref_ids[b], b.value)
            elif b.dreftype == DREF_TYPE.SPECIAL:
                if b.label == "TERR ON ND":
                    target = "AirbusFBW/TerrainSelectedND1" if terrain_pref_is_captain() else "AirbusFBW/TerrainSelectedND2"
                    current = int(get_cached_value(target, 0) or 0)
                    xp.dataref_set_value(xp.buttonref_ids[b], 0 if current else 1)
                elif b.label == "GEAR UP":
                    xp.command_activate_duration(xp.buttonref_ids[b], 0.15)
                    xp.dataref_set_value(xp.extra_buttonref_ids[b], 0)
                elif b.label == "GEAR DOWN":
                    xp.command_activate_duration(xp.buttonref_ids[b], 0.15)
                    xp.dataref_set_value(xp.extra_buttonref_ids[b], 1)

        if buttons_release_event[b.pin_nr]:
            buttons_release_event[b.pin_nr] = 0
            print(f"[AGP32] button {b.label} released")


class DisplayManager:
    IDENTIFIER_BYTE = 0x80

    def __init__(self, device):
        self.device = device
        self.packet_number = 1

    def startupscreen(self, version=None, new_version=None):
        self.set_led(Leds.BACKLIGHT, 128)
        self.set_led(Leds.LCD_BRIGHTNESS, 128)
        self.set_led(Leds.OVERALL_LEDS_BRIGHTNESS, 255)
        self.set_all_leds_enabled(False)
        self.set_lcd_text("", "", "")

    def set_all_leds_enabled(self, enabled):
        for led in range(int(Leds.LDG_GEAR_UNLK_LEFT), int(Leds.LDG_GEAR_LEVER_RED) + 1):
            self.set_led(led, 1 if enabled else 0)

    def set_led(self, led: int, brightness: int):
        brightness = max(0, min(int(brightness), 255))
        data = [0x02, self.IDENTIFIER_BYTE, 0xBB, 0x00, 0x00, 0x03, 0x49, int(led), brightness, 0, 0, 0, 0, 0]
        #data.extend([0] * (64 - len(data)))
        try:
            self.device.write(bytes(data))
        except Exception as exc:
            print(f"[AGP32] LED write failed for {led}: {exc}")

    def set_lcd_text(self, chrono: str, utc_time: str, elapsed_time: str):
        packet = [
            0xF0, 0x00, self.packet_number, 0x35, self.IDENTIFIER_BYTE, 0xBB, 0x00, 0x00,
            0x02, 0x01, 0x00, 0x00, 0xFF, 0xFF, 0x00, 0x00, 0x00, 0x24, 0x00, 0x00,
            0x00, 0x00, 0x00, 0x00, 0x00
        ]
        packet.extend([0x00] * (64 - len(packet)))

        row_offsets = [25, 29, 33, 37, 41, 45, 49, 53]
        all_digits = ""
        colon_mask = 0

        all_digits, colon_mask = parse_segment(chrono, 4, all_digits, colon_mask, 0)
        all_digits, colon_mask = parse_segment(utc_time, 6, all_digits, colon_mask, 4)
        all_digits, colon_mask = parse_segment(elapsed_time, 4, all_digits, colon_mask, 10)

        while len(all_digits) < 14:
            all_digits += " "

        for digit_index in range(14):
            c = all_digits[digit_index]
            char_mask = get_segment_mask(c)

            for seg_index in range(7):
                if char_mask & (1 << seg_index):
                    byte_offset = row_offsets[seg_index] + (digit_index // 8)
                    bit_pos = digit_index % 8
                    packet[byte_offset] |= (1 << bit_pos)

            if colon_mask & (1 << digit_index):
                byte_offset = row_offsets[7] + (digit_index // 8)
                bit_pos = digit_index % 8
                packet[byte_offset] |= (1 << bit_pos)

        try:
            self.device.write(bytes(packet))
            commit_packet = [
                0xF0, 0x00, self.packet_number, 0x11, self.IDENTIFIER_BYTE, 0xBB, 0x00, 0x00,
                0x03, 0x01, 0x00, 0x00, 0xFF, 0xFF, 0x00, 0x00
            ]
            commit_packet.extend([0x00] * (64 - len(commit_packet)))
            self.device.write(bytes(commit_packet))
            self.packet_number = 1 if self.packet_number == 255 else self.packet_number + 1
        except Exception as exc:
            print(f"[AGP32] LCD write failed: {exc}")


def xplane_ws_listener(data, led_dataref_ids):
    if data.get("type") != "dataref_update_values":
        if data.get("type") == "result" and data.get("success") is not True:
            print(f"[AGP32] send failed for {data}")
        return

    changed = False
    for ref_id_str, value in data["data"].items():
        ref_id = int(ref_id_str)
        if ref_id not in led_dataref_ids:
            continue

        names = led_dataref_ids[ref_id]
        if not isinstance(names, list):
            names = [names]

        for name in names:
            set_cached_value(name, value)
        changed = True

    if changed:
        update_led_state()
        update_lcd()


class UsbManager:
    def __init__(self):
        self.device = None
        self.device_config = 0

    def connect_device(self, vid: int, pid: int):
        try:
            self.device = hid.device()
            self.device.open(vid, pid)
        except AttributeError:
            self.device = hid.Device(vid=vid, pid=pid)

        if self.device is None:
            raise RuntimeError("[AGP32] Device not found")

        try:
            self.device.set_nonblocking(False)
        except Exception:
            pass

        print("[AGP32] Device connected.")

    def find_device(self):
        devlist = [
            {"vid": 0x4098, "pid": 0xBB80, "name": "WINCTRL AGP", "mask": DEVICEMASK.AGP32},
        ]
        for d in devlist:
            print(f"[AGP32] now searching for {d['name']} ... ", end="")
            for dev in hid.enumerate():
                if dev["vendor_id"] == d["vid"] and dev["product_id"] == d["pid"]:
                    print("found")
                    self.device_config |= d["mask"]
                    return d["vid"], d["pid"], self.device_config
            print("not found")
        return None, None, 0


def create_button_list_agp32():
    mappings = [
        (0, "BRAKE_FAN_ON", "AirbusFBW/BrakeFan", DREF_TYPE.DATA, 1),
        (1, "BRAKE_FAN_OFF", "AirbusFBW/BrakeFan", DREF_TYPE.DATA, 0),
        (2, "AUTOBRK_LO", "AirbusFBW/AbrkLo", DREF_TYPE.CMD_SHORT, None),
        (3, "AUTOBRK_MED", "AirbusFBW/AbrkMed", DREF_TYPE.CMD_SHORT, None),
        (4, "AUTOBRK_MAX", "AirbusFBW/AbrkMax", DREF_TYPE.CMD_SHORT, None),
        (5, "ANTISKID_ON", "AirbusFBW/NWSnAntiSkid", DREF_TYPE.DATA, 1),
        (6, "ANTISKID_OFF", "AirbusFBW/NWSnAntiSkid", DREF_TYPE.DATA, 0),
        (7, "RST_LEFT", None, DREF_TYPE.NONE, None),
        (8, "RST_PRESS", "toliss_airbus/chrono/ChronoResetPush", DREF_TYPE.CMD_SHORT, None),
        (9, "RST_RIGHT", None, DREF_TYPE.NONE, None),
        (10, "CHR_LEFT", None, DREF_TYPE.NONE, None),
        (11, "CHR_PRESS", "toliss_airbus/chrono/ChronoStartStopPush", DREF_TYPE.CMD_SHORT, None),
        (12, "CHR_RIGHT", None, DREF_TYPE.NONE, None),
        (13, "DATE_LEFT", None, DREF_TYPE.NONE, None),
        (14, "DATE_PRESS", "toliss_airbus/chrono/datePush", DREF_TYPE.CMD_SHORT, None),
        (15, "DATE_RIGHT", None, DREF_TYPE.NONE, None),
        (16, "UTC_GPS", "ckpt/clock/gpsKnob/anim", DREF_TYPE.DATA, 0),
        (17, "UTC_INT", "ckpt/clock/gpsKnob/anim", DREF_TYPE.DATA, 1),
        (18, "UTC_SET", "ckpt/clock/gpsKnob/anim", DREF_TYPE.DATA, 2),
        (19, "ET_RUN", "AirbusFBW/ClockETSwitch", DREF_TYPE.DATA, 0),
        (20, "ET_STP", "AirbusFBW/ClockETSwitch", DREF_TYPE.DATA, 1),
        (21, "ET_RST", "AirbusFBW/ClockETSwitch", DREF_TYPE.DATA, 2),
        (22, "TERR ON ND", "AirbusFBW/TerrainSelectedND1" if terrain_pref_is_captain() else "AirbusFBW/TerrainSelectedND2", DREF_TYPE.SPECIAL, None),
        (23, "GEAR UP", "sim/flight_controls/landing_gear_up", DREF_TYPE.SPECIAL, 0),
        (24, "GEAR DOWN", "sim/flight_controls/landing_gear_down", DREF_TYPE.SPECIAL, 1),
    ]

    for pin_nr, label, target, dreftype, value in mappings:
        buttonlist.append(Button(pin_nr, label, target, dreftype, BUTTON.SWITCH, value))


def xplane_get_dataref_ids(xp):
    watched_datarefs = [
        "AirbusFBW/PanelBrightnessLevel",
        "sim/cockpit/electrical/avionics_on",
        "AirbusFBW/AnnunMode",
        "AirbusFBW/NoseGearInd",
        "AirbusFBW/LeftGearInd",
        "AirbusFBW/RightGearInd",
        "AirbusFBW/TerrainSelectedND1",
        "AirbusFBW/TerrainSelectedND2",
        "AirbusFBW/AutoBrkLo",
        "AirbusFBW/AutoBrkMed",
        "AirbusFBW/AutoBrkMax",
        "AirbusFBW/BrakeFan",
        "AirbusFBW/OHPLightsATA32_Raw",
        "AirbusFBW/ClockChronoValue",
        "AirbusFBW/ChronoButtonAnimations",
        "sim/time/local_date_days",
        "sim/time/zulu_time_sec",
        "AirbusFBW/ClockShowsET",
        "AirbusFBW/ClockETHours",
        "AirbusFBW/ClockETMinutes",
    ]

    print("[AGP32] getting dataref ids ... ", end="")
    for dataref in watched_datarefs:
        ref_id = xp.dataref_id_fetch(dataref)
        xp.datacache[dataref] = 0
        if ref_id not in xp.led_dataref_ids:
            xp.led_dataref_ids[ref_id] = [dataref]
        elif dataref not in xp.led_dataref_ids[ref_id]:
            xp.led_dataref_ids[ref_id].append(dataref)
    print("done")

    print("[AGP32] getting button cmd/dataref ids ... ", end="")
    xp.extra_buttonref_ids = {}
    for button in buttonlist:
        if button.dataref is None:
            continue
        if button.dreftype == DREF_TYPE.CMD_SHORT:
            ref_id = xp.command_id_fetch(button.dataref)
            xp.buttonref_ids[button] = ref_id
        elif button.dreftype == DREF_TYPE.DATA:
            ref_id = xp.dataref_id_fetch(button.dataref)
            xp.buttonref_ids[button] = ref_id
            xp.datacache[button.dataref] = 0
        elif button.dreftype == DREF_TYPE.SPECIAL:
            if button.label.startswith("GEAR "):
                ref_id = xp.command_id_fetch(button.dataref)
                xp.buttonref_ids[button] = ref_id
                extra_ref_id = xp.dataref_id_fetch("ckpt/gearHandle")
                xp.extra_buttonref_ids[button] = extra_ref_id
                xp.datacache["ckpt/gearHandle"] = 0
            else:
                ref_id = xp.dataref_id_fetch(button.dataref)
                xp.buttonref_ids[button] = ref_id
                xp.datacache[button.dataref] = 0
    print("done")


def agp32_create_events(xp, usb_mgr, display_mgr):
    buttons_last_lo = 0
    buttons_last_hi = 0

    while True:
        if not xplane_connected:
            sleep(1)
            continue

        values_processed.set()
        sleep(0.01)
        try:
            data_in = usb_mgr.device.read(64)
        except Exception as error:
            print(f"[AGP32] continue after usb-in error: {error}")
            sleep(0.5)
            continue

        if len(data_in) not in VALID_REPORT_LENGTHS:
            if len(data_in) != 0:
                print(f"[AGP32] rx data count {len(data_in)} not yet supported")
            continue

        if len(data_in) < 13:
            continue

        report_id = data_in[0]
        if report_id != REPORT_ID:
            continue

        buttons_lo = 0
        buttons_hi = 0

        for i in range(8):
            buttons_lo |= int(data_in[i + 1]) << (8 * i)
        for i in range(4):
            buttons_hi |= int(data_in[i + 9]) << (8 * i)

        if buttons_lo == buttons_last_lo and buttons_hi == buttons_last_hi:
            continue

        for i in range(BUTTONS_CNT):
            if i < 64:
                pressed_now = (buttons_lo >> i) & 1
                pressed_before = (buttons_last_lo >> i) & 1
            else:
                pressed_now = (buttons_hi >> (i - 64)) & 1
                pressed_before = (buttons_last_hi >> (i - 64)) & 1

            if pressed_now != pressed_before:
                if pressed_now:
                    buttons_press_event[i] = 1
                else:
                    buttons_release_event[i] = 1

        agp_button_event()
        buttons_last_lo = buttons_lo
        buttons_last_hi = buttons_hi


class device:
    def __init__(self, UDP_IP=None, UDP_PORT=None):
        self.usb_mgr = None
        self.cyclic = Event()
        self.xp = xp_websocket.XP_Websocket()

    def connected(self):
        global xplane_connected
        global xp
        print("[AGP32] X-Plane connected")
        xplane_get_dataref_ids(self.xp)
        print("[AGP32] subscribe datarefs ... ", end="")
        t = Thread(target=self.xp.datarefs_subscribe, args=(self.xp.led_dataref_ids, xplane_ws_listener))
        t.start()
        print("done")
        xplane_connected = True
        xp = self.xp
        update_led_state()
        update_lcd()

    def disconnected(self):
        global xplane_connected
        xplane_connected = False
        print("[AGP32] X-Plane disconnected")

    def init_device(self, version: str = None, new_version: str = None):
        global display_manager

        self.version = version
        self.new_version = new_version

        self.usb_mgr = UsbManager()
        vid, pid, _device_config = self.usb_mgr.find_device()
        if pid is None:
            print("[AGP32] No compatible WINCTRL device found, quit")
            return

        self.usb_mgr.connect_device(vid=vid, pid=pid)
        self.display_mgr = DisplayManager(self.usb_mgr.device)
        self.display_mgr.startupscreen(self.version, self.new_version)
        display_manager = self.display_mgr

        create_button_list_agp32()

        usb_event_thread = Thread(target=agp32_create_events, args=[self.xp, self.usb_mgr, self.display_mgr])
        usb_event_thread.start()
