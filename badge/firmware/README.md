# Badge custom firmware — bring-up

Our own firmware for the HTN 2026 badge (ESP32-C3 + ST7789). It drives the
screen, the six LEDs, and the USB console with code we wrote — the proof of
full access. It uses the pin map recovered by disassembling the stock firmware
(see `../backup` and the memory notes).

**What it does:** eight vertical color bars + a white border on the 320×240
screen, a dim rainbow spinning across the six LEDs, and an "alive" heartbeat
printed to USB every 100 ms.

## Pin map it uses (recovered, not guessed)

| Signal | GPIO | | Signal | GPIO |
|---|---|---|---|---|
| LCD SCLK | 1 | | LCD RST | 4 |
| LCD MOSI | 10 | | LED data | 3 |
| LCD DC | 0 | | I2C SDA | 5 |
| LCD CS | 2 | | I2C SCL | 6 |

SPI2, 40 MHz, mode 0, RGB565. (I2C pins listed for reference; this firmware
doesn't touch the accelerometer/NFC yet.)

## Build & flash

1. Install ESP-IDF v5.3+ (v5.5 matches the stock build): https://docs.espressif.com/projects/esp-idf/en/stable/esp32c3/get-started/
2. Open the ESP-IDF terminal / run its export script, then:

   ```
   cd badge/firmware
   idf.py set-target esp32c3
   idf.py build
   idf.py -p COM5 flash monitor
   ```

   `flash` overwrites the stock firmware. `monitor` shows the heartbeat; Ctrl-] exits.

3. If the badge won't enter flashing mode on its own, hold **START** (GPIO9, the
   BOOT strap) while plugging in USB, then run `idf.py -p COM5 flash`.

## If the screen looks wrong

The pins are confirmed; only a few display-orientation options are conventions
that may need one flip. Edit `main/badge_bringup.c`:

- **Colors byte-swapped** (red↔blue-ish, wrong hues): set `SWAP_BYTES 0`.
- **Red and blue swapped specifically**: change `.rgb_ele_order` to `LCD_RGB_ELEMENT_ORDER_BGR`.
- **Image mirrored / upside down**: change the `esp_lcd_panel_mirror(panel, x, y)` args.
- **Whole image shifted / wrapped**: add `esp_lcd_panel_set_gap(panel, dx, dy)` after init.
- **Blank screen**: try removing `esp_lcd_panel_invert_color(... true)` or toggling it.

If nothing shows at all, the console still prints the heartbeat and each init
step over USB — read it with `idf.py monitor` to see which call failed.

## Zero-dependency build

The LEDs use the managed `espressif/led_strip` component (auto-downloaded).
To build with no external components, set `USE_LED 0` at the top of
`main/badge_bringup.c`.

## Get back to the stock badge

```
cd badge/tools
python restore.py          # rewrites the pristine 4 MB stock image
```

Fully reversible — this never touches anything a reflash can't undo.
