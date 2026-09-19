// HTN 2026 badge - MJPEG screen-streaming receiver.
// The badge is its own Wi-Fi hotspot + TCP server. A laptop joins the hotspot
// and streams length-prefixed JPEG frames; the badge decodes each with the
// ESP32-C3 ROM tjpgd decoder (jd_prepare/jd_decomp - free, no flash cost) and
// blits the decoded blocks to the ST7789. Compression is what makes the Wi-Fi
// pipe fast enough: raw RGB565 was ~1 fps (150 KB/frame); JPEG is ~10-20 KB.
//
// Wire format (per frame): uint32 little-endian length, then that many JPEG bytes.
// Hotspot: SSID "BadgeCraft"  password "minecraft"   badge IP 192.168.4.1:3333
// Restore stock: cd badge/tools && python restore.py

#include <string.h>
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include "esp_log.h"
#include "esp_timer.h"
#include "esp_heap_caps.h"
#include "nvs_flash.h"
#include "esp_event.h"
#include "esp_netif.h"
#include "esp_wifi.h"
#include "esp_mac.h"
#include "lwip/sockets.h"
#include "driver/spi_master.h"
#include "driver/gpio.h"
#include "esp_rom_sys.h"
#include "esp_lcd_panel_io.h"
#include "esp_lcd_panel_vendor.h"
#include "esp_lcd_panel_ops.h"
#include "esp32c3/rom/tjpgd.h"
#include "led_strip.h"

#define PIN_SCLK 1
#define PIN_MOSI 10
#define PIN_LCD_DC 0
#define PIN_LCD_CS 2
#define PIN_LCD_RST 4
#define LCD_W 320
#define LCD_H 240
#define LCD_HZ (40 * 1000 * 1000)
#define BAND_H 16                       // tallest JPEG MCU is 16 px
#define PORT 3333
#define JPEG_BUF_SZ (64 * 1024)         // one compressed frame; busy scenes spike bigger
#define WORK_SZ 4096                    // tjpgd work pool (needs >= ~3100)
#define FB_MAX_W 160                    // largest downscaled source we upscale from
#define FB_MAX_H 120

static const char *TAG = "badgecraft";
static esp_lcd_panel_handle_t panel;

static uint8_t  jpeg_buf[JPEG_BUF_SZ];          // received compressed frame
static uint32_t work_pool[WORK_SZ / 4];         // tjpgd scratch (word-aligned)
static uint8_t  band[2][LCD_W * BAND_H * 2];    // ping-pong RGB565 output bands
static uint8_t  fb[FB_MAX_W * FB_MAX_H * 2];    // decoded small frame (RGB565-BE), for upscaling

// When set, out_func writes into fb[] (for later upscale) instead of blitting bands.
static int to_fb = 0, fb_w = 0, fb_h = 0;

// --- JPEG input source: hand tjpgd bytes from the in-memory frame ------------
typedef struct { const uint8_t *buf; size_t len, pos; } jsrc_t;

static size_t in_func(JDEC *jd, uint8_t *out, size_t n) {
    jsrc_t *s = (jsrc_t *)jd->device;
    size_t avail = s->len - s->pos;
    if (n > avail) n = avail;
    if (out) memcpy(out, s->buf + s->pos, n);
    s->pos += n;
    return n;
}

// --- JPEG output sink: assemble decoded blocks into a band, blit per band -----
static int band_cur = 0, band_top = -1, band_ht = 0;

static void band_flush(void) {
    if (band_ht > 0) {
        esp_lcd_panel_draw_bitmap(panel, 0, band_top, LCD_W, band_top + band_ht, band[band_cur]);
        band_cur ^= 1;   // ping-pong; trans_queue_depth 2 keeps the DMA race-free
    }
}

static UINT out_func(JDEC *jd, void *bitmap, JRECT *r) {
    int w = r->right - r->left + 1;
    int h = r->bottom - r->top + 1;
    const uint8_t *src = (const uint8_t *)bitmap;   // RGB888 (ROM tjpgd JD_FORMAT=0)
    if (to_fb) {
        for (int row = 0; row < h; row++) {
            uint8_t *dst = &fb[((r->top + row) * fb_w + r->left) * 2];
            for (int col = 0; col < w; col++) {
                uint8_t rr = *src++, gg = *src++, bb = *src++;
                uint16_t v = ((rr & 0xF8) << 8) | ((gg & 0xFC) << 3) | (bb >> 3);
                *dst++ = v >> 8; *dst++ = v & 0xFF;
            }
        }
        return 1;
    }
    if (r->top != band_top) { band_flush(); band_top = r->top; }
    band_ht = h;
    for (int row = 0; row < h; row++) {
        uint8_t *dst = &band[band_cur][(row * LCD_W + r->left) * 2];
        for (int col = 0; col < w; col++) {
            uint8_t rr = *src++, gg = *src++, bb = *src++;
            uint16_t v = ((rr & 0xF8) << 8) | ((gg & 0xFC) << 3) | (bb >> 3);
            *dst++ = v >> 8;            // big-endian for the ST7789
            *dst++ = v & 0xFF;
        }
    }
    return 1;
}

// Nearest-neighbour upscale the decoded fb[] (fb_w x fb_h) to fill 320x240,
// blitting in ping-pong bands so the SPI DMA overlaps the row expansion.
static void upscale_blit(void) {
    for (int y = 0; y < LCD_H; y += BAND_H) {
        uint8_t *out = band[band_cur];
        for (int row = 0; row < BAND_H; row++) {
            int sy = (y + row) * fb_h / LCD_H;
            const uint16_t *srow = (const uint16_t *)&fb[sy * fb_w * 2];
            uint16_t *orow = (uint16_t *)&out[row * LCD_W * 2];
            for (int x = 0; x < LCD_W; x++)
                orow[x] = srow[x * fb_w / LCD_W];   // bytes already big-endian
        }
        esp_lcd_panel_draw_bitmap(panel, 0, y, LCD_W, y + BAND_H, out);
        band_cur ^= 1;
    }
}

static bool draw_jpeg(const uint8_t *buf, size_t len) {
    jsrc_t src = { .buf = buf, .len = len, .pos = 0 };
    JDEC jd;
    if (jd_prepare(&jd, in_func, work_pool, WORK_SZ, &src) != JDR_OK) return false;
    band_cur = 0; band_top = -1; band_ht = 0;
    to_fb = (jd.width <= FB_MAX_W && jd.height <= FB_MAX_H && jd.width < LCD_W);
    if (to_fb) { fb_w = jd.width; fb_h = jd.height; }
    JRESULT rc = jd_decomp(&jd, out_func, 0);
    if (to_fb) upscale_blit();
    else       band_flush();            // full-res: last band
    return rc == JDR_OK;
}

#define PIN_LED 3
#define NUM_LEDS 6

// Blank the six WS2812s at boot (they hold their last color across a reflash, and
// the stock firmware left them lit) and free the RMT channel - no runtime LED use.
static void leds_off(void) {
    led_strip_handle_t strip;
    led_strip_config_t sc = {
        .strip_gpio_num = PIN_LED, .max_leds = NUM_LEDS,
        .led_pixel_format = LED_PIXEL_FORMAT_GRB,
        .led_model = LED_MODEL_WS2812,
    };
    led_strip_rmt_config_t rc = { .clk_src = RMT_CLK_SRC_DEFAULT, .resolution_hz = 10 * 1000 * 1000 };
    if (led_strip_new_rmt_device(&sc, &rc, &strip) == ESP_OK) {
        led_strip_clear(strip);   // all off + latch
        led_strip_del(strip);
        ESP_LOGI(TAG, "LEDs cleared");
    }
}

static void lcd_init(void) {
    spi_bus_config_t buscfg = {
        .mosi_io_num = PIN_MOSI, .miso_io_num = -1, .sclk_io_num = PIN_SCLK,
        .quadwp_io_num = -1, .quadhd_io_num = -1,
        .max_transfer_sz = LCD_W * BAND_H * 2 + 64,
    };
    ESP_ERROR_CHECK(spi_bus_initialize(SPI2_HOST, &buscfg, SPI_DMA_CH_AUTO));
    esp_lcd_panel_io_handle_t io = NULL;
    esp_lcd_panel_io_spi_config_t io_config = {
        .cs_gpio_num = PIN_LCD_CS, .dc_gpio_num = PIN_LCD_DC, .spi_mode = 0,
        .pclk_hz = LCD_HZ, .trans_queue_depth = 2,
        .lcd_cmd_bits = 8, .lcd_param_bits = 8,
    };
    ESP_ERROR_CHECK(esp_lcd_new_panel_io_spi((esp_lcd_spi_bus_handle_t)SPI2_HOST, &io_config, &io));
    esp_lcd_panel_dev_config_t panel_config = {
        .reset_gpio_num = PIN_LCD_RST, .rgb_ele_order = LCD_RGB_ELEMENT_ORDER_RGB,
        .bits_per_pixel = 16,
    };
    ESP_ERROR_CHECK(esp_lcd_new_panel_st7789(io, &panel_config, &panel));
    ESP_ERROR_CHECK(esp_lcd_panel_reset(panel));
    ESP_ERROR_CHECK(esp_lcd_panel_init(panel));
    ESP_ERROR_CHECK(esp_lcd_panel_invert_color(panel, true));
    ESP_ERROR_CHECK(esp_lcd_panel_swap_xy(panel, true));
    ESP_ERROR_CHECK(esp_lcd_panel_mirror(panel, true, false));
    ESP_ERROR_CHECK(esp_lcd_panel_disp_on_off(panel, true));
}

static void wifi_softap(void) {
    ESP_ERROR_CHECK(nvs_flash_init());
    ESP_ERROR_CHECK(esp_netif_init());
    ESP_ERROR_CHECK(esp_event_loop_create_default());
    esp_netif_create_default_wifi_ap();
    wifi_init_config_t cfg = WIFI_INIT_CONFIG_DEFAULT();
    ESP_ERROR_CHECK(esp_wifi_init(&cfg));
    wifi_config_t ap = {0};
    strcpy((char *)ap.ap.ssid, "BadgeCraft");
    ap.ap.ssid_len = strlen("BadgeCraft");
    strcpy((char *)ap.ap.password, "minecraft");
    ap.ap.channel = 6;
    ap.ap.max_connection = 2;
    ap.ap.authmode = WIFI_AUTH_WPA2_PSK;
    ESP_ERROR_CHECK(esp_wifi_set_mode(WIFI_MODE_AP));
    ESP_ERROR_CHECK(esp_wifi_set_config(WIFI_IF_AP, &ap));
    ESP_ERROR_CHECK(esp_wifi_start());
    // Fresh batteries restore the current headroom, so use a moderate TX power for
    // a solid link (still below max to keep spikes reasonable). 44 = 11 dBm.
    esp_wifi_set_max_tx_power(44);
    ESP_LOGI(TAG, "hotspot up: SSID=BadgeCraft pass=minecraft  ->  connect, then stream to 192.168.4.1:%d", PORT);
}

// --- Buttons: 74HC165 shift register (pins recovered on real hardware) --------
// GPIO20 = SH/LD (load, idle high), GPIO21 = CLK (idle low), GPIO7 = QH (serial
// data, active-low). Raw bit -> button map confirmed by pressing each button:
//   UP=0x02  DOWN=0x10  LEFT=0x08  RIGHT=0x04  A=0x80  B=0x40
#define PIN_165_LD   20
#define PIN_165_CLK  21
#define PIN_165_DATA 7
#define PIN_START    9        // START button: direct GPIO (BOOT strap), active-low

static uint8_t shift165(void) {
    uint8_t v = 0;
    gpio_set_level(PIN_165_LD, 0); esp_rom_delay_us(5);   // latch parallel inputs
    gpio_set_level(PIN_165_LD, 1); esp_rom_delay_us(5);
    for (int i = 0; i < 8; i++) {
        v = (v << 1) | gpio_get_level(PIN_165_DATA);
        gpio_set_level(PIN_165_CLK, 1); esp_rom_delay_us(5);
        gpio_set_level(PIN_165_CLK, 0); esp_rom_delay_us(5);
    }
    return v;
}

static void buttons_init(void) {
    gpio_config_t out = { .pin_bit_mask = (1ULL << PIN_165_LD) | (1ULL << PIN_165_CLK),
                          .mode = GPIO_MODE_OUTPUT };
    gpio_config(&out);
    gpio_config_t in = { .pin_bit_mask = (1ULL << PIN_165_DATA) | (1ULL << PIN_START),
                         .mode = GPIO_MODE_INPUT, .pull_up_en = 1 };
    gpio_config(&in);
    gpio_set_level(PIN_165_LD, 1); gpio_set_level(PIN_165_CLK, 0);
}

// Pack into the bitmask the laptop expects: UP=0 DOWN=1 LEFT=2 RIGHT=3 A=4 B=5 START=6.
static uint8_t read_buttons(void) {
    uint8_t raw = shift165();            // active-low (pressed = 0)
    uint8_t b = 0;
    if (!(raw & 0x02)) b |= 1 << 0;      // UP
    if (!(raw & 0x10)) b |= 1 << 1;      // DOWN
    if (!(raw & 0x08)) b |= 1 << 2;      // LEFT
    if (!(raw & 0x04)) b |= 1 << 3;      // RIGHT
    if (!(raw & 0x80)) b |= 1 << 4;      // A
    if (!(raw & 0x40)) b |= 1 << 5;      // B
    if (!gpio_get_level(PIN_START)) b |= 1 << 6;   // START = jump
    if (!(raw & 0x20)) b |= 1 << 7;      // HOME (modifier: D-pad becomes look)
    return b;
}

// --- Input channel: badge -> laptop -------------------------------------------
// Poll the buttons ~30x/s and send a 3-byte packet. Sent every tick (not just on
// change) so the laptop can apply continuous HOME+D-pad mouse-look; it's only
// ~90 B/s. TCP is full-duplex so this coexists with the frame recv loop.
// Packet: 0xAA, buttons, 0x55.
static volatile int input_sock = -1;

static void input_task(void *arg) {
    while (1) {
        int s = input_sock;
        if (s >= 0) {
            uint8_t pkt[3] = { 0xAA, read_buttons(), 0x55 };
            send(s, pkt, sizeof(pkt), 0);          // errors are harmless; recv loop owns teardown
        }
        vTaskDelay(pdMS_TO_TICKS(33));
    }
}

static int recv_all(int s, uint8_t *buf, int len) {
    int got = 0;
    while (got < len) {
        int r = recv(s, buf + got, len - got, 0);
        if (r <= 0) return r;
        got += r;
    }
    return got;
}

// Read and throw away n bytes (for an oversized frame we can't buffer) so the
// stream stays in sync instead of us dropping the connection.
static int recv_discard(int s, int n) {
    while (n > 0) {
        int chunk = n < (int)sizeof(jpeg_buf) ? n : (int)sizeof(jpeg_buf);
        int r = recv_all(s, jpeg_buf, chunk);
        if (r <= 0) return r;
        n -= chunk;
    }
    return 1;
}

static void stream_server(void) {
    int srv = socket(AF_INET, SOCK_STREAM, 0);
    struct sockaddr_in a = { .sin_family = AF_INET, .sin_port = htons(PORT), .sin_addr.s_addr = INADDR_ANY };
    int yes = 1;
    setsockopt(srv, SOL_SOCKET, SO_REUSEADDR, &yes, sizeof(yes));
    bind(srv, (struct sockaddr *)&a, sizeof(a));
    listen(srv, 1);
    while (1) {
        ESP_LOGI(TAG, "waiting for a laptop to connect...");
        int c = accept(srv, NULL, NULL);
        if (c < 0) continue;
        int one = 1;
        setsockopt(c, IPPROTO_TCP, TCP_NODELAY, &one, sizeof(one));
        struct timeval tv = { .tv_sec = 5, .tv_usec = 0 };   // drop a dead client, re-accept
        setsockopt(c, SOL_SOCKET, SO_RCVTIMEO, &tv, sizeof(tv));
        input_sock = c;                       // start feeding tilt/buttons back
        ESP_LOGI(TAG, "client connected - streaming MJPEG");
        int frames = 0, decoded = 0;
        int64_t recv_us = 0, draw_us = 0;
        int64_t t0 = esp_timer_get_time();
        while (1) {
            uint8_t hdr[4];
            int64_t ta = esp_timer_get_time();
            if (recv_all(c, hdr, 4) <= 0) break;
            uint32_t len = hdr[0] | (hdr[1] << 8) | (hdr[2] << 16) | ((uint32_t)hdr[3] << 24);
            if (len == 0) continue;
            if (len > JPEG_BUF_SZ) {              // too big to buffer: skip it, keep streaming
                ESP_LOGW(TAG, "skipping oversized frame: %u bytes", (unsigned)len);
                if (recv_discard(c, len) <= 0) break;
                continue;
            }
            if (recv_all(c, jpeg_buf, len) <= 0) break;
            int64_t tb = esp_timer_get_time();
            if (draw_jpeg(jpeg_buf, len)) decoded++;
            int64_t tc = esp_timer_get_time();
            frames++;
            recv_us += tb - ta;
            draw_us += tc - tb;
            int64_t now = esp_timer_get_time();
            if (now - t0 >= 1000000) {
                ESP_LOGI(TAG, "%d fps | recv %lld ms | draw %lld ms  (avg per frame)",
                         frames, recv_us / 1000 / frames, draw_us / 1000 / frames);
                frames = 0; decoded = 0; recv_us = 0; draw_us = 0; t0 = now;
            }
        }
        input_sock = -1;                      // stop input before closing the fd
        close(c);
        ESP_LOGI(TAG, "client gone");
    }
}

void app_main(void) {
    ESP_LOGI(TAG, "HTN badge - MJPEG screen stream receiver");
    leds_off();
    lcd_init();
    wifi_softap();
    ESP_LOGI(TAG, "free heap: %u bytes", (unsigned)esp_get_free_heap_size());
    buttons_init();
    xTaskCreate(input_task, "input", 3072, NULL, 4, NULL);
    stream_server();
}
