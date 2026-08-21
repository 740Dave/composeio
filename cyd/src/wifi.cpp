/*
 * wifi.cpp — WiFi station mode + mDNS for CYD
 *
 * Uses ESP-IDF WiFi APIs (no Arduino).
 * Connects to the SSID/password from wifi_config.h.
 * Registers mDNS hostname "cyd" so the device is reachable as cyd.local.
 */

#include "wifi.h"
#include "wifi_config.h"

#include <cstring>
#include "freertos/FreeRTOS.h"
#include "freertos/event_groups.h"
#include "esp_wifi.h"
#include "esp_netif.h"
#include "esp_event.h"
#include "esp_log.h"
#include "nvs_flash.h"
// mDNS removed — requires IDF component registry in ESP-IDF v5.x
// Device accessible via IP address (logged on connect)

static const char* TAG = "wifi";

/* Event group bits */
#define WIFI_CONNECTED_BIT  BIT0
#define WIFI_FAIL_BIT       BIT1

static EventGroupHandle_t s_wifi_event_group = nullptr;
static bool s_connected = false;
static char s_ip_str[16] = "0.0.0.0";
static int s_retry_count = 0;
static constexpr int kMaxRetries = 10;

/* ------------------------------------------------------------------ */
/* Event handler                                                       */
/* ------------------------------------------------------------------ */

static void wifi_event_handler(void* arg, esp_event_base_t event_base,
                               int32_t event_id, void* event_data)
{
    if (event_base == WIFI_EVENT && event_id == WIFI_EVENT_STA_START) {
        esp_wifi_connect();
    } else if (event_base == WIFI_EVENT && event_id == WIFI_EVENT_STA_DISCONNECTED) {
        s_connected = false;
        if (s_retry_count < kMaxRetries) {
            esp_wifi_connect();
            s_retry_count++;
            ESP_LOGI(TAG, "Retrying connection (%d/%d)...", s_retry_count, kMaxRetries);
        } else {
            xEventGroupSetBits(s_wifi_event_group, WIFI_FAIL_BIT);
            ESP_LOGE(TAG, "Failed to connect after %d retries", kMaxRetries);
        }
    } else if (event_base == IP_EVENT && event_id == IP_EVENT_STA_GOT_IP) {
        auto* event = static_cast<ip_event_got_ip_t*>(event_data);
        snprintf(s_ip_str, sizeof(s_ip_str), IPSTR, IP2STR(&event->ip_info.ip));
        ESP_LOGI(TAG, "Connected — IP: %s", s_ip_str);
        s_retry_count = 0;
        s_connected = true;
        xEventGroupSetBits(s_wifi_event_group, WIFI_CONNECTED_BIT);
    }
}

/* mDNS removed — component not bundled in ESP-IDF v5.5.
 * Device is accessible via the IP address logged on connect.
 * To add mDNS later: `idf.py add-dependency "espressif/mdns"` */

/* ------------------------------------------------------------------ */
/* Public API                                                          */
/* ------------------------------------------------------------------ */

void wifi_init(void)
{
    /* Idempotent: config-driven boot brings WiFi up early; the capture
     * pipeline (and any later caller) must be able to call this again
     * safely. */
    static bool s_initialized = false;
    if (s_initialized) {
        if (!s_connected && s_wifi_event_group) {
            ESP_LOGI(TAG, "WiFi already initialized; waiting for reconnect...");
            xEventGroupWaitBits(s_wifi_event_group,
                                WIFI_CONNECTED_BIT, pdFALSE, pdFALSE,
                                pdMS_TO_TICKS(10000));
        }
        return;
    }
    s_initialized = true;

    /* NVS is required by the WiFi driver */
    esp_err_t ret = nvs_flash_init();
    if (ret == ESP_ERR_NVS_NO_FREE_PAGES || ret == ESP_ERR_NVS_NEW_VERSION_FOUND) {
        ESP_ERROR_CHECK(nvs_flash_erase());
        ret = nvs_flash_init();
    }
    ESP_ERROR_CHECK(ret);

    s_wifi_event_group = xEventGroupCreate();

    /* Network interface */
    ESP_ERROR_CHECK(esp_netif_init());
    ESP_ERROR_CHECK(esp_event_loop_create_default());
    esp_netif_create_default_wifi_sta();

    /* WiFi driver init — reduce buffer counts for CYD (no PSRAM).
     * Default: 10 static RX, 32 dynamic RX, 32 dynamic TX.
     * Reduced: 4/8/8 — saves ~40KB heap for framebuffer + rendering. */
    wifi_init_config_t cfg = WIFI_INIT_CONFIG_DEFAULT();
    cfg.static_rx_buf_num = 4;
    cfg.dynamic_rx_buf_num = 8;
    cfg.dynamic_tx_buf_num = 8;
    cfg.rx_ba_win = 4;
    cfg.cache_tx_buf_num = 0;
    ESP_ERROR_CHECK(esp_wifi_init(&cfg));

    /* Register event handlers */
    esp_event_handler_instance_t inst_any_id;
    esp_event_handler_instance_t inst_got_ip;
    ESP_ERROR_CHECK(esp_event_handler_instance_register(
        WIFI_EVENT, ESP_EVENT_ANY_ID, &wifi_event_handler, nullptr, &inst_any_id));
    ESP_ERROR_CHECK(esp_event_handler_instance_register(
        IP_EVENT, IP_EVENT_STA_GOT_IP, &wifi_event_handler, nullptr, &inst_got_ip));

    /* Configure station */
    wifi_config_t wifi_config = {};
    static_assert(sizeof(WIFI_SSID) <= sizeof(wifi_config.sta.ssid), "SSID too long");
    static_assert(sizeof(WIFI_PASS) <= sizeof(wifi_config.sta.password), "Password too long");
    memcpy(wifi_config.sta.ssid, WIFI_SSID, sizeof(WIFI_SSID));
    memcpy(wifi_config.sta.password, WIFI_PASS, sizeof(WIFI_PASS));
    wifi_config.sta.threshold.authmode = WIFI_AUTH_WPA2_PSK;

    ESP_ERROR_CHECK(esp_wifi_set_mode(WIFI_MODE_STA));
    ESP_ERROR_CHECK(esp_wifi_set_config(WIFI_IF_STA, &wifi_config));
    ESP_ERROR_CHECK(esp_wifi_start());

    ESP_LOGI(TAG, "WiFi station started — connecting to \"%s\"...", WIFI_SSID);

    /* Wait for connection (up to ~10s) */
    EventBits_t bits = xEventGroupWaitBits(
        s_wifi_event_group,
        WIFI_CONNECTED_BIT | WIFI_FAIL_BIT,
        pdFALSE, pdFALSE,
        pdMS_TO_TICKS(10000));

    if (bits & WIFI_CONNECTED_BIT) {
        ESP_LOGI(TAG, "WiFi connected successfully");
    } else {
        ESP_LOGW(TAG, "WiFi connection timed out — will keep retrying in background");
    }

    /* mDNS removed — use IP address from log */
}

bool wifi_is_connected(void)
{
    return s_connected;
}

const char* wifi_get_ip(void)
{
    return s_ip_str;
}
