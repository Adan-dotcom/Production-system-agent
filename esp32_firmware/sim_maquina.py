"""
Simulador de ESP32 para probar el monitor SIN hardware.

Publica pulsos MQTT como lo haría un ESP32 real. Alterna periódicamente entre
"girando" (freq>0) y "parada" (freq=0) para ver el dashboard reaccionar.

Requisitos:
    pip install paho-mqtt

Uso:
    python esp32_firmware/sim_maquina.py 1            # máquina_id=1, broker localhost
    python esp32_firmware/sim_maquina.py 2 --host 192.168.1.100 --hz 8
    python esp32_firmware/sim_maquina.py 1 --stopped  # simula máquina detenida (freq=0)

machine_id DEBE existir en la tabla machines de la DB.
Lánzalo en varias terminales (id 1, id 2, ...) para simular varios ESP.
"""
import argparse
import json
import time

import paho.mqtt.client as mqtt


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("machine_id", type=int, help="machines.machine_id")
    ap.add_argument("--host", default="localhost")
    ap.add_argument("--port", type=int, default=1883)
    ap.add_argument("--prefix", default="biotecnica/maquinas")
    ap.add_argument("--hz", type=float, default=12.0, help="frecuencia cuando 'gira'")
    ap.add_argument("--stopped", action="store_true", help="mantener parada (freq=0)")
    ap.add_argument("--cycle", type=int, default=15, help="segundos por fase girar/parar")
    args = ap.parse_args()

    topic = f"{args.prefix}/{args.machine_id}/pulso"
    client = mqtt.Client()
    client.connect(args.host, args.port, 60)
    client.loop_start()
    print(f"Publicando en {args.host}:{args.port} → {topic}  (Ctrl+C para salir)")

    t0 = time.time()
    try:
        while True:
            if args.stopped:
                freq = 0.0
            else:
                # alterna: gira durante `cycle` s, luego para `cycle` s
                phase = int((time.time() - t0) // args.cycle) % 2
                freq = args.hz if phase == 0 else 0.0
            client.publish(topic, json.dumps({"freq_hz": round(freq, 2)}))
            print(f"  {topic}  freq_hz={freq}")
            time.sleep(1)
    except KeyboardInterrupt:
        print("\nFin.")
    finally:
        client.loop_stop()
        client.disconnect()


if __name__ == "__main__":
    main()
