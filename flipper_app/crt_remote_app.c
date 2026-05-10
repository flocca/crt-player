#include <furi.h>
#include <gui/gui.h>
#include <input/input.h>

#include <bt/bt_service/bt.h>
#include <furi_hal_bt.h>
#include <profiles/serial_profile.h>

#define TAG "crt_remote"

typedef struct {
    FuriMessageQueue* input_queue;
    ViewPort* view_port;
    Gui* gui;
    Bt* bt;
    FuriHalBleProfileBase* profile;
    bool ble_ok;
} CrtRemoteApp;

static void draw_callback(Canvas* canvas, void* ctx) {
    CrtRemoteApp* app = ctx;
    canvas_clear(canvas);
    canvas_set_font(canvas, FontPrimary);
    canvas_draw_str(canvas, 2, 12, "CRT Remote");
    canvas_set_font(canvas, FontSecondary);
    canvas_draw_str(canvas, 2, 28, app->ble_ok ? "BLE: Serial active" : "BLE: starting...");
    canvas_draw_str(canvas, 2, 44, "Up/Dn/OK/L/R/Hold");
    canvas_draw_str(canvas, 2, 60, "Back to exit");
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
    furi_delay_ms(200);
    app->profile = bt_profile_start(app->bt, ble_profile_serial, NULL);
    if(app->profile == NULL) {
        FURI_LOG_E(TAG, "bt_profile_start failed");
        app->ble_ok = false;
        return false;
    }
    ble_profile_serial_set_rpc_active(app->profile, false);
    furi_hal_bt_start_advertising();
    FURI_LOG_I(TAG, "BLE Serial active, advertising, RPC disabled");
    app->ble_ok = true;
    return true;
}

static void ble_serial_stop(CrtRemoteApp* app) {
    if(app->bt != NULL) {
        bt_disconnect(app->bt);
        furi_delay_ms(200);
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
                switch(event.key) {
                    case InputKeyUp:    ble_serial_send_byte(&app, 0x01); break;
                    case InputKeyDown:  ble_serial_send_byte(&app, 0x02); break;
                    case InputKeyOk:    ble_serial_send_byte(&app, 0x03); break;
                    case InputKeyRight: ble_serial_send_byte(&app, 0x05); break;
                    case InputKeyLeft:  ble_serial_send_byte(&app, 0x06); break;
                    case InputKeyBack:  running = false; break;
                    default: break;
                }
            } else if(event.type == InputTypeLong) {
                switch(event.key) {
                    case InputKeyBack: ble_serial_send_byte(&app, 0x04); break;
                    case InputKeyOk:   ble_serial_send_byte(&app, 0x07); break;
                    default: break;
                }
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
