#include <furi.h>
#include <gui/gui.h>
#include <input/input.h>

#include <bt/bt_service/bt.h>
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
    canvas_draw_str(canvas, 2, 28, app->ble_ok ? "BLE: Serial active" : "BLE: init failed");
    canvas_draw_str(canvas, 2, 60, "Back to exit");
}

static void input_callback(InputEvent* event, void* ctx) {
    CrtRemoteApp* app = ctx;
    furi_message_queue_put(app->input_queue, event, FuriWaitForever);
}

static bool ble_serial_start(CrtRemoteApp* app) {
    app->bt = furi_record_open(RECORD_BT);
    app->profile = bt_profile_start(app->bt, ble_profile_serial, NULL);
    if(app->profile == NULL) {
        FURI_LOG_E(TAG, "bt_profile_start failed");
        app->ble_ok = false;
        return false;
    }
    FURI_LOG_I(TAG, "BLE Serial profile active");
    app->ble_ok = true;
    return true;
}

static void ble_serial_stop(CrtRemoteApp* app) {
    if(app->bt != NULL) {
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
            if(event.type == InputTypeShort && event.key == InputKeyBack) {
                running = false;
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
