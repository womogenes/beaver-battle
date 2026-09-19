idf_component_register(SRCS "main.c" "controller.c"
                       INCLUDE_DIRS "."
                       REQUIRES driver esp_wifi esp_event esp_netif nvs_flash esp_timer json lwip)
