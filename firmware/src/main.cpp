// Claude Hardware Buddy — M5Stack Core Basic firmware
//
// Phase 1: BLE peripheral (Nordic UART Service) + 320x240 live status display.
// Receives compact status JSON from the Python bridge and renders session counts.
//
// Phase 2: 3-button input for permission approval (A=allow, C=deny, B=auto-approve
// toggle), permission-takeover screen, TX-notify decision path back to bridge,
// and auto-approve mode with banner + beep.
//
// Privacy: the bridge sends ONLY counts, a short status string, and tool call
// metadata (command / path / URL). File contents, diff bodies, and conversation
// text are NEVER sent to this device.

#include <Arduino.h>
#include <BLEDevice.h>
#include <BLEServer.h>
#include <BLEUtils.h>
#include <BLE2902.h>
#include <BLESecurity.h>
#include <M5Unified.h>
#include <ArduinoJson.h>

// Nordic UART Service — must match the bridge / REFERENCE.md exactly.
#define NUS_SERVICE "6e400001-b5a3-f393-e0a9-e50e24dcca9e"
#define NUS_RX      "6e400002-b5a3-f393-e0a9-e50e24dcca9e"  // central writes here
#define NUS_TX      "6e400003-b5a3-f393-e0a9-e50e24dcca9e"  // device notifies here

static BLECharacteristic* txChar = nullptr;
static volatile bool centralConnected = false;

// Pending permission prompt state (empty id == no prompt).
static char promptId[48]     = {0};
static char promptTool[24]   = {0};
static char promptDetail[200] = {0};
static bool autoApprove      = false;

// Renders the live status screen. Phase 1 shows counts only — no message
// text or transcript content ever reaches the device.
static void renderStatus(int running, int waiting, int total,
                         const char* msg) {
  M5.Display.fillScreen(TFT_BLACK);
  M5.Display.setTextColor(TFT_WHITE, TFT_BLACK);

  M5.Display.setTextSize(2);
  M5.Display.setCursor(8, 8);
  M5.Display.print("Claude Buddy");

  M5.Display.setTextSize(6);
  M5.Display.setTextColor(TFT_GREEN, TFT_BLACK);
  M5.Display.setCursor(8, 56);
  M5.Display.printf("%d", running);
  M5.Display.setTextSize(2);
  M5.Display.setCursor(8, 120);
  M5.Display.setTextColor(TFT_GREEN, TFT_BLACK);
  M5.Display.print("running");

  M5.Display.setTextSize(6);
  M5.Display.setTextColor(TFT_ORANGE, TFT_BLACK);
  M5.Display.setCursor(170, 56);
  M5.Display.printf("%d", waiting);
  M5.Display.setTextSize(2);
  M5.Display.setCursor(170, 120);
  M5.Display.setTextColor(TFT_ORANGE, TFT_BLACK);
  M5.Display.print("waiting");

  M5.Display.setTextSize(2);
  M5.Display.setTextColor(TFT_WHITE, TFT_BLACK);
  M5.Display.setCursor(8, 160);
  M5.Display.printf("%d sessions", total);
  M5.Display.setCursor(8, 200);
  M5.Display.setTextColor(TFT_DARKGREY, TFT_BLACK);
  M5.Display.print(msg);
}

// Forward declarations for functions called from RxCallbacks (defined later).
static void sendDecision(const char* decision);
static void toggleAuto();

// Permission-takeover screen: navy background, tool + detail, button hints.
static void renderPrompt(const char* tool, const char* detail) {
  M5.Display.fillScreen(TFT_NAVY);
  M5.Display.setTextColor(TFT_WHITE, TFT_NAVY);
  M5.Display.setTextSize(3);
  M5.Display.setCursor(8, 8);
  M5.Display.printf("Approve %s?", tool);
  M5.Display.setTextSize(2);
  M5.Display.setCursor(8, 50);
  M5.Display.setTextWrap(true);
  M5.Display.print(detail);              // full tool call; no file contents
  M5.Display.setTextColor(TFT_GREEN, TFT_NAVY);
  M5.Display.setCursor(8, 210);
  M5.Display.print("[A] Allow");
  M5.Display.setTextColor(TFT_RED, TFT_NAVY);
  M5.Display.setCursor(180, 210);
  M5.Display.print("[C] Deny");
}

class ServerCallbacks : public BLEServerCallbacks {
  void onConnect(BLEServer*) override {
    centralConnected = true;
    Serial.println("[ble] central connected");
  }
  void onDisconnect(BLEServer* s) override {
    centralConnected = false;
    Serial.println("[ble] central disconnected -- re-advertising");
    s->getAdvertising()->start();
  }
};

class RxCallbacks : public BLECharacteristicCallbacks {
  void onWrite(BLECharacteristic* c) override {
    std::string v = c->getValue();
    if (v.empty()) return;
    Serial.print("[rx] ");
    Serial.write(reinterpret_cast<const uint8_t*>(v.data()), v.size());
    Serial.println();

    JsonDocument doc;
    DeserializationError err = deserializeJson(doc, v);
    if (err) return;
    if (doc["evt"] == "status") {
      renderStatus(doc["running"] | 0, doc["waiting"] | 0,
                   doc["total"] | 0, doc["msg"] | "");
    } else if (doc["evt"] == "prompt") {
      strlcpy(promptId,     doc["id"]     | "", sizeof(promptId));
      strlcpy(promptTool,   doc["tool"]   | "", sizeof(promptTool));
      strlcpy(promptDetail, doc["detail"] | "", sizeof(promptDetail));
      if (autoApprove) { sendDecision("allow"); }
      else             { renderPrompt(promptTool, promptDetail); }
    } else if (doc["cmd"] == "prompt_cancel") {
      if (strcmp(doc["id"] | "", promptId) == 0) {
        promptId[0] = 0;
        renderStatus(0, 0, 0, "");
      }
    }
  }
};

// DisplayOnly device: the stack picks a random passkey, we display it on
// screen and serial so the user can enter it on the paired host.
class SecurityCallbacks : public BLESecurityCallbacks {
  uint32_t onPassKeyRequest() override { return 0; }
  void onPassKeyNotify(uint32_t pk) override {
    Serial.printf("\n  BLE PAIRING PASSKEY: %06u\n\n", pk);
    M5.Display.fillScreen(TFT_BLACK);
    M5.Display.setTextSize(2);
    M5.Display.setCursor(8, 8);
    M5.Display.print("Pair this code:");
    M5.Display.setTextSize(4);
    M5.Display.setCursor(8, 60);
    M5.Display.printf("%06u", pk);
  }
  bool onConfirmPIN(uint32_t) override { return true; }
  bool onSecurityRequest() override { return true; }
  void onAuthenticationComplete(esp_ble_auth_cmpl_t cmpl) override {
    Serial.printf("[ble] pairing %s\n", cmpl.success ? "SUCCEEDED" : "FAILED");
  }
};

void setup() {
  auto cfg = M5.config();
  M5.begin(cfg);
  M5.Speaker.begin();
  M5.Display.setRotation(1);              // 320x240 landscape
  M5.Display.fillScreen(TFT_BLACK);
  M5.Display.setTextColor(TFT_WHITE, TFT_BLACK);
  M5.Display.setTextSize(2);
  M5.Display.setCursor(8, 8);
  M5.Display.print("Claude Buddy");
  M5.Display.setCursor(8, 40);
  M5.Display.print("starting...");

  Serial.begin(115200);
  delay(300);
  Serial.println();
  Serial.println("[buddy] Claude Buddy firmware starting");

  BLEDevice::init("Claude-Buddy");
  BLEDevice::setMTU(517);
  BLEDevice::setEncryptionLevel(ESP_BLE_SEC_ENCRYPT_MITM);
  BLEDevice::setSecurityCallbacks(new SecurityCallbacks());

  BLEServer* server = BLEDevice::createServer();
  server->setCallbacks(new ServerCallbacks());

  BLEService* svc = server->createService(NUS_SERVICE);

  // Require an encrypted (bonded) link for all GATT access. Without these
  // per-characteristic permissions the global MITM config is not enforced —
  // a nearby unpaired central could write spoofed status to RX.
  txChar = svc->createCharacteristic(NUS_TX, BLECharacteristic::PROPERTY_NOTIFY);
  txChar->setAccessPermissions(ESP_GATT_PERM_READ_ENCRYPTED);
  BLE2902* cccd = new BLE2902();
  cccd->setAccessPermissions(ESP_GATT_PERM_READ_ENCRYPTED |
                             ESP_GATT_PERM_WRITE_ENCRYPTED);
  txChar->addDescriptor(cccd);

  BLECharacteristic* rxChar = svc->createCharacteristic(
      NUS_RX,
      BLECharacteristic::PROPERTY_WRITE | BLECharacteristic::PROPERTY_WRITE_NR);
  rxChar->setAccessPermissions(ESP_GATT_PERM_WRITE_ENCRYPTED);
  rxChar->setCallbacks(new RxCallbacks());

  svc->start();

  BLEAdvertising* adv = BLEDevice::getAdvertising();
  adv->addServiceUUID(NUS_SERVICE);
  adv->setScanResponse(true);
  adv->setMinPreferred(0x06);
  adv->setMaxPreferred(0x12);
  BLEDevice::startAdvertising();

  BLESecurity* sec = new BLESecurity();
  sec->setAuthenticationMode(ESP_LE_AUTH_REQ_SC_MITM_BOND);
  sec->setCapability(ESP_IO_CAP_OUT);  // DisplayOnly
  sec->setKeySize(16);
  sec->setInitEncryptionKey(ESP_BLE_ENC_KEY_MASK | ESP_BLE_ID_KEY_MASK);
  sec->setRespEncryptionKey(ESP_BLE_ENC_KEY_MASK | ESP_BLE_ID_KEY_MASK);

  Serial.println("[buddy] advertising as 'Claude-Buddy'");
}

// Send a JSON string as a NUS TX notification to the central.
static void sendNotify(const char* json) {
  if (txChar == nullptr || !centralConnected) return;
  txChar->setValue((uint8_t*)json, strlen(json));
  txChar->notify();
}

// Send the button decision back to the bridge, then clear the prompt state
// and return to the status screen (next heartbeat will refresh the counts).
static void sendDecision(const char* decision) {
  if (autoApprove) { M5.Speaker.tone(660, 80); }
  char buf[160];  // fits a full promptId[48] without truncating the JSON
  snprintf(buf, sizeof(buf),
           "{\"cmd\":\"permission\",\"id\":\"%s\",\"decision\":\"%s\"}\n",
           promptId, decision);
  sendNotify(buf);
  promptId[0] = 0;           // clear pending prompt
  renderStatus(0, 0, 0, ""); // back to status; next heartbeat refreshes counts
}

// Toggle auto-approve mode, notify the bridge, and show a banner + beep.
static void toggleAuto() {
  autoApprove = !autoApprove;
  char buf[40];
  snprintf(buf, sizeof(buf), "{\"cmd\":\"auto\",\"state\":%s}\n",
           autoApprove ? "true" : "false");
  sendNotify(buf);
  // Banner so the auto-approve state is never ambiguous.
  M5.Display.fillRect(0, 0, 320, 28, autoApprove ? TFT_RED : TFT_BLACK);
  if (autoApprove) {
    M5.Display.setTextColor(TFT_WHITE, TFT_RED);
    M5.Display.setTextSize(2);
    M5.Display.setCursor(8, 6);
    M5.Display.print("AUTO-APPROVE ON");
    M5.Speaker.tone(880, 120);
  }
}

void loop() {
  M5.update();
  if (promptId[0] != 0 && M5.BtnA.wasPressed())      { sendDecision("allow"); }
  else if (promptId[0] != 0 && M5.BtnC.wasPressed()) { sendDecision("deny"); }
  else if (M5.BtnB.wasPressed())                     { toggleAuto(); }
  delay(20);
}
