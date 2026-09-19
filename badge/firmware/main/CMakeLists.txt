idf_component_register(
    SRCS "badge_bringup.c"
    INCLUDE_DIRS "."
    REQUIRES esp_lcd driver esp_driver_spi nvs_flash esp_wifi esp_event esp_netif lwip esp_timer
)
