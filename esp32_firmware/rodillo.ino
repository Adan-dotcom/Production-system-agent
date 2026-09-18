/* ============================================================================
 * Biotécnica — Monitor de máquinas (on/off)
 * Sensor Hall en un rodillo (1 imán de neodimio => 1 pulso por vuelta).
 * Cada 1 s calcula la frecuencia de pulsos y la publica por MQTT al server.
 *
 * Arquitectura: ESP32 --(WiFi/MQTT)--> broker Mosquitto --> FastAPI (subscriber)
 *
 * Topic:    {MQTT_TOPIC_PREFIX}/{MACHINE_ID}/pulso
 * Payload:  {"freq_hz": 12.50}
 * Se publica SIEMPRE cada 1 s (incluso freq_hz=0) para que el server sepa que
 * el rodillo está parado y para que detecte caídas del ESP por timeout.
 *
 * Reconexión: connectWifi()/connectMqtt() reintentan indefinidamente (loop
 * bloqueante con retry), así que el ESP puede quedarse encendido y montado
 * en la máquina ANTES de que el server/broker estén arriba — en cuanto el
 * broker responda en MQTT_HOST, se conecta solo sin reflashear nada.
 *
 * Librerías (Arduino IDE → Gestor de librerías):
 *   - PubSubClient (Nick O'Leary)
 *   - (WiFi.h viene con el core ESP32)
 * ==========================================================================*/

#include <WiFi.h>
#include <PubSubClient.h>

// ── EDITAR POR ESTACIÓN ─────────────────────────────────────────────────────
const char* WIFI_SSID   = "Biotecnica2.4";
const char* WIFI_PASS   = "Bio_503.*";

// TODO: poner la IP FIJA real de la PC servidor en la red "Biotecnica2.4"
// en cuanto se desbloquee y se le asigne (ver esp32_firmware/README_MONITOR.md).
// Mientras tanto el ESP se queda reintentando MQTT sin problema.
const char* MQTT_HOST   = "PENDIENTE_IP_SERVER";
const int   MQTT_PORT   = 1883;

const int   MACHINE_ID  = 2;                 // = machines.machine_id (1=Cortadora 1, 2=Extrusora 1). ¡Único por ESP!
const int   MAGNETS     = 1;                 // imanes pegados al rodillo (normalmente 1)
// ─────────────────────────────────────────────────────────────────────────────

const int   HALL_PIN    = 4;                 // GPIO del sensor Hall (salida digital)
const unsigned long SAMPLE_MS = 1000;        // ventana de cálculo de frecuencia (1 s)

volatile unsigned long pulseCount = 0;
unsigned long lastSample = 0;

char topic[64];
WiFiClient wifiClient;
PubSubClient mqtt(wifiClient);

void IRAM_ATTR onPulse() {
  pulseCount++;
}

void connectWifi() {
  WiFi.mode(WIFI_STA);
  WiFi.begin(WIFI_SSID, WIFI_PASS);
  Serial.print("WiFi");
  while (WiFi.status() != WL_CONNECTED) {
    delay(400);
    Serial.print(".");
  }
  Serial.print(" OK  IP=");
  Serial.println(WiFi.localIP());
}

void connectMqtt() {
  String clientId = "esp32-maquina-" + String(MACHINE_ID);
  while (!mqtt.connected()) {
    Serial.print("MQTT...");
    if (mqtt.connect(clientId.c_str())) {
      Serial.println(" conectado");
    } else {
      Serial.print(" fallo rc=");
      Serial.print(mqtt.state());
      Serial.println(" reintento 2s");
      delay(2000);
    }
  }
}

void setup() {
  Serial.begin(115200);
  pinMode(HALL_PIN, INPUT_PULLUP);
  attachInterrupt(digitalPinToInterrupt(HALL_PIN), onPulse, FALLING);

  snprintf(topic, sizeof(topic), "biotecnica/maquinas/%d/pulso", MACHINE_ID);

  connectWifi();
  mqtt.setServer(MQTT_HOST, MQTT_PORT);
  connectMqtt();
  lastSample = millis();
}

void loop() {
  if (WiFi.status() != WL_CONNECTED) connectWifi();
  if (!mqtt.connected()) connectMqtt();
  mqtt.loop();

  unsigned long now = millis();
  if (now - lastSample >= SAMPLE_MS) {
    noInterrupts();
    unsigned long count = pulseCount;
    pulseCount = 0;
    interrupts();

    float elapsed = (now - lastSample) / 1000.0;
    lastSample = now;

    // pulsos/seg = vueltas/seg (con 1 imán). freq_hz reportada en Hz de pulso.
    float freqHz = (elapsed > 0) ? (count / elapsed) / MAGNETS : 0.0;

    char payload[48];
    snprintf(payload, sizeof(payload), "{\"freq_hz\":%.2f}", freqHz);
    mqtt.publish(topic, payload);

    Serial.print(topic);
    Serial.print("  ->  ");
    Serial.println(payload);
  }
}
