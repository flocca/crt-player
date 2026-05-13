#include <furi.h>
#include <gui/gui.h>
#include <input/input.h>

#include <bt/bt_service/bt.h>
#include <furi_hal_bt.h>
#include <storage/storage.h>

// Local fork of the Flipper Serial profile (from Momentum FW, via
// EmmerichFrog/home_remote_public). Diverges from the stock profile in two
// crucial ways: it uses a custom MAC (default ^ mac_xor) and a custom
// advertise name. With a non-default MAC the firmware's BtSrv RPC handler
// — which is hooked to the default profile/MAC — never opens an RPC
// connection on top of our session, so our TX bytes flow cleanly to the
// central without spurious preamble or premature disconnects.
#include "libs/serial_profile.h"

#define TAG "crt_remote"

// Command codes sent over BLE Serial TX. Must match the bridge's parse_command
// table in lodge-tools/services/crt-flipper-bridge/bridge.py.
#define CMD_NEXT       0x01
#define CMD_PREV       0x02
#define CMD_TOGGLE     0x03
#define CMD_STOP       0x04
#define CMD_LOOP       0x05
#define CMD_SYNC       0x06
#define CMD_CALIBRATE        0x07
#define CMD_SEEK_BACK_15     0x08
#define CMD_SEEK_FORWARD_30  0x09
#define CMD_DELETE           0x0A

typedef enum {
    BleStateStarting = 0,
    BleStateActive,
    BleStateFailed,
} BleState;

typedef enum {
    SceneHome = 0,
    SceneExtraMenu,
} Scene;

typedef struct {
    const char* label;
    uint8_t cmd_byte;
} MenuItem;

static const MenuItem MENU_ITEMS[] = {
    {"Stop",          CMD_STOP},
    {"Elimina video", CMD_DELETE},
    {"Calibrate",     CMD_CALIBRATE},
    {"Toggle loop",   CMD_LOOP},
    {"Sync now",      CMD_SYNC},
};
#define MENU_ITEMS_COUNT (sizeof(MENU_ITEMS) / sizeof(MENU_ITEMS[0]))

typedef struct {
    FuriMessageQueue* input_queue;
    ViewPort* view_port;
    Gui* gui;
    Bt* bt;
    FuriHalBleProfileBase* profile;
    BleState ble_state;
    Scene scene;
    uint8_t menu_index;
} CrtRemoteApp;

static void draw_home(Canvas* canvas, CrtRemoteApp* app) {
    canvas_set_font(canvas, FontPrimary);
    canvas_draw_str_aligned(canvas, 32, 10, AlignCenter, AlignTop, "CRT Remote");

    canvas_set_font(canvas, FontSecondary);
    const char* state_line;
    switch(app->ble_state) {
        case BleStateActive:   state_line = "BLE: active";   break;
        case BleStateFailed:   state_line = "BLE: failed";   break;
        case BleStateStarting:
        default:               state_line = "BLE: starting"; break;
    }
    canvas_draw_str_aligned(canvas, 32, 22, AlignCenter, AlignTop, state_line);

    canvas_draw_str(canvas, 4, 42, "< -15s");
    canvas_draw_str(canvas, 4, 54, "> +30s");
    canvas_draw_str(canvas, 4, 66, "^ prev");
    canvas_draw_str(canvas, 4, 78, "v next");

    canvas_draw_str_aligned(canvas, 32, 100, AlignCenter, AlignTop, "OK = play/pause");
    canvas_draw_str_aligned(canvas, 32, 115, AlignCenter, AlignTop, "hold OK: extras");
}

static void draw_extra_menu(Canvas* canvas, CrtRemoteApp* app) {
    canvas_set_font(canvas, FontPrimary);
    canvas_draw_str_aligned(canvas, 32, 10, AlignCenter, AlignTop, "Comandi");

    canvas_set_font(canvas, FontSecondary);
    const int y_base = 30;
    const int y_step = 12;
    for(size_t i = 0; i < MENU_ITEMS_COUNT; i++) {
        char buf[32];
        snprintf(buf, sizeof(buf), "%s %s",
                 (i == app->menu_index) ? ">" : " ",
                 MENU_ITEMS[i].label);
        canvas_draw_str(canvas, 4, y_base + (int)i * y_step, buf);
    }

    canvas_draw_str_aligned(canvas, 32, 110, AlignCenter, AlignTop, "OK conferma");
    canvas_draw_str_aligned(canvas, 32, 120, AlignCenter, AlignTop, "Back annulla");
}

static void draw_callback(Canvas* canvas, void* ctx) {
    CrtRemoteApp* app = ctx;
    canvas_clear(canvas);
    if(app->scene == SceneExtraMenu) {
        draw_extra_menu(canvas, app);
    } else {
        draw_home(canvas, app);
    }
}

static void input_callback(InputEvent* event, void* ctx) {
    CrtRemoteApp* app = ctx;
    furi_message_queue_put(app->input_queue, event, FuriWaitForever);
}

static void ble_serial_send_byte(CrtRemoteApp* app, uint8_t byte_val) {
    if(app->profile == NULL || !furi_hal_bt_is_active()) {
        FURI_LOG_W(TAG, "TX 0x%02x skipped (BT not ready)", byte_val);
        return;
    }
    uint8_t buf[1] = {byte_val};
    bool ok = ble_profile_serial_tx(app->profile, buf, 1);
    FURI_LOG_I(TAG, "TX 0x%02x: %s", byte_val, ok ? "ok" : "fail");
}

static bool ble_serial_start(CrtRemoteApp* app) {
    app->bt = furi_record_open(RECORD_BT);
    bt_disconnect(app->bt);
    // Use a separate keys storage so our bond doesn't collide with the
    // system Flipper bond (qFlipper Mac/iOS).
    bt_keys_storage_set_storage_path(app->bt, APP_DATA_PATH(".bt_serial.keys"));

    BleProfileSerialParams params = {
        .device_name_prefix = "CRTRem",  // <8 chars per the SDK comment
        .mac_xor = 0x0042,               // arbitrary — picks a different MAC
    };
    app->profile = bt_profile_start(app->bt, ble_profile_serial, &params);
    if(app->profile == NULL) {
        FURI_LOG_E(TAG, "bt_profile_start failed");
        // Restore default keys storage so subsequent BT use (system Flipper,
        // qFlipper) doesn't pair against our custom store.
        bt_keys_storage_set_default_path(app->bt);
        furi_record_close(RECORD_BT);
        app->bt = NULL;
        app->ble_state = BleStateFailed;
        return false;
    }
    furi_hal_bt_start_advertising();
    FURI_LOG_I(TAG, "BLE Serial active (forked profile, custom MAC)");
    app->ble_state = BleStateActive;
    return true;
}

static void ble_serial_stop(CrtRemoteApp* app) {
    if(app->bt != NULL) {
        bt_disconnect(app->bt);
        furi_delay_ms(200);
        bt_keys_storage_set_default_path(app->bt);
        bt_profile_restore_default(app->bt);
        furi_record_close(RECORD_BT);
        app->bt = NULL;
        app->profile = NULL;
    }
}

int32_t crt_remote_app(void* p) {
    UNUSED(p);
    CrtRemoteApp app = {0};
    app.input_queue = furi_message_queue_alloc(8, sizeof(InputEvent));

    app.view_port = view_port_alloc();
    view_port_set_orientation(app.view_port, ViewPortOrientationVertical);
    view_port_draw_callback_set(app.view_port, draw_callback, &app);
    view_port_input_callback_set(app.view_port, input_callback, &app);

    app.gui = furi_record_open(RECORD_GUI);
    gui_add_view_port(app.gui, app.view_port, GuiLayerFullscreen);

    ble_serial_start(&app);
    view_port_update(app.view_port);

    InputEvent event;
    bool running = true;
    while(running) {
        if(furi_message_queue_get(app.input_queue, &event, FuriWaitForever) == FuriStatusOk) {
            if(event.type == InputTypeShort) {
                if(app.scene == SceneHome) {
                    switch(event.key) {
                        case InputKeyUp:    ble_serial_send_byte(&app, CMD_SEEK_BACK_15);    break;
                        case InputKeyDown:  ble_serial_send_byte(&app, CMD_SEEK_FORWARD_30); break;
                        case InputKeyLeft:  ble_serial_send_byte(&app, CMD_NEXT);            break;
                        case InputKeyRight: ble_serial_send_byte(&app, CMD_PREV);            break;
                        case InputKeyOk:    ble_serial_send_byte(&app, CMD_TOGGLE);          break;
                        case InputKeyBack:  running = false;                                 break;
                        default: break;
                    }
                } else { // SceneExtraMenu
                    switch(event.key) {
                        case InputKeyRight: // user "Up"
                            if(app.menu_index > 0) app.menu_index--;
                            view_port_update(app.view_port);
                            break;
                        case InputKeyLeft: // user "Down"
                            if((size_t)(app.menu_index + 1) < MENU_ITEMS_COUNT) app.menu_index++;
                            view_port_update(app.view_port);
                            break;
                        case InputKeyOk:
                            ble_serial_send_byte(&app, MENU_ITEMS[app.menu_index].cmd_byte);
                            app.scene = SceneHome;
                            view_port_update(app.view_port);
                            break;
                        case InputKeyBack:
                            app.scene = SceneHome;
                            view_port_update(app.view_port);
                            break;
                        default: break;
                    }
                }
            } else if(event.type == InputTypeLong) {
                if(app.scene == SceneHome && event.key == InputKeyOk) {
                    app.scene = SceneExtraMenu;
                    app.menu_index = 0;
                    view_port_update(app.view_port);
                }
                // Long-press Back in either scene is intentionally unbound (was STOP in v1).
            }
        }
    }

    ble_serial_stop(&app);

    gui_remove_view_port(app.gui, app.view_port);
    view_port_free(app.view_port);
    furi_record_close(RECORD_GUI);
    furi_message_queue_free(app.input_queue);
    return 0;
}
