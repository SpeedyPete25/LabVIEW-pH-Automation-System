import argparse
import json
import random
import re
import threading
import time
from dataclasses import dataclass, asdict
from datetime import datetime, timezone
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Optional

import serial


@dataclass
class Measurement:
    timestamp_utc: str
    ph: Optional[float] = None
    temperature_c: Optional[float] = None
    mv: Optional[float] = None
    raw: Optional[str] = None
    source: str = "meter"


class OrionStarMeter:
    def __init__(
        self,
        port: str,
        baudrate: int,
        bytesize: int,
        parity: str,
        stopbits: int,
        timeout_seconds: float,
        read_command: str,
        line_terminator: str,
    ):
        self._read_command = read_command
        self._line_terminator = line_terminator.encode("ascii", errors="ignore")
        self._serial = serial.Serial(
            port=port,
            baudrate=baudrate,
            bytesize=bytesize,
            parity=parity,
            stopbits=stopbits,
            timeout=timeout_seconds,
        )

    def close(self) -> None:
        if self._serial and self._serial.is_open:
            self._serial.close()

    def query_measurement(self) -> Measurement:
        command = self._read_command.encode("ascii", errors="ignore") + self._line_terminator
        self._serial.write(command)
        self._serial.flush()

        raw = self._serial.readline().decode("ascii", errors="ignore").strip()
        now = datetime.now(timezone.utc).isoformat()

        ph, temperature_c, mv = parse_orionstar_line(raw)
        return Measurement(
            timestamp_utc=now,
            ph=ph,
            temperature_c=temperature_c,
            mv=mv,
            raw=raw,
            source="meter",
        )


class MockMeter:
    def query_measurement(self) -> Measurement:
        now = datetime.now(timezone.utc).isoformat()
        ph = round(7.0 + random.uniform(-0.4, 0.4), 3)
        temperature_c = round(25.0 + random.uniform(-1.5, 1.5), 2)
        mv = round((7.0 - ph) * 59.16, 2)
        raw = f"MOCK,pH={ph},T={temperature_c}C,mV={mv}"
        return Measurement(
            timestamp_utc=now,
            ph=ph,
            temperature_c=temperature_c,
            mv=mv,
            raw=raw,
            source="mock",
        )


def parse_orionstar_line(raw: str):
    text = raw.strip()
    if not text:
        return None, None, None

    ph = _extract_value(text, [r"pH\s*[:=]?\s*(-?\d+(?:\.\d+)?)", r"(-?\d+(?:\.\d+)?)\s*pH"])
    temperature_c = _extract_value(text, [r"(?:T|Temp|Temperature)\s*[:=]?\s*(-?\d+(?:\.\d+)?)\s*(?:C|degC)?"])
    mv = _extract_value(text, [r"(-?\d+(?:\.\d+)?)\s*mV", r"mV\s*[:=]?\s*(-?\d+(?:\.\d+)?)"])

    if ph is None:
        csv_parts = [part.strip() for part in text.split(",")]
        numeric_parts = []
        for part in csv_parts:
            match = re.search(r"-?\d+(?:\.\d+)?", part)
            if match:
                numeric_parts.append(float(match.group(0)))
        if numeric_parts:
            ph = numeric_parts[0]
            if len(numeric_parts) > 1 and temperature_c is None:
                temperature_c = numeric_parts[1]
            if len(numeric_parts) > 2 and mv is None:
                mv = numeric_parts[2]

    return ph, temperature_c, mv


def _extract_value(text: str, patterns):
    for pattern in patterns:
        match = re.search(pattern, text, re.IGNORECASE)
        if match:
            try:
                return float(match.group(1))
            except (TypeError, ValueError):
                return None
    return None


class BridgeState:
    def __init__(self):
        self._lock = threading.Lock()
        self._last: Optional[Measurement] = None
        self._last_error: Optional[str] = None

    def update_measurement(self, measurement: Measurement) -> None:
        with self._lock:
            self._last = measurement
            self._last_error = None

    def update_error(self, error_message: str) -> None:
        with self._lock:
            self._last_error = error_message

    def snapshot(self):
        with self._lock:
            return self._last, self._last_error


class BridgeHTTPRequestHandler(BaseHTTPRequestHandler):
    state: BridgeState = None

    def _send_json(self, payload, status=HTTPStatus.OK):
        body = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        last, last_error = self.state.snapshot()

        if self.path == "/health":
            self._send_json(
                {
                    "status": "ok" if not last_error else "degraded",
                    "last_error": last_error,
                }
            )
            return

        if self.path == "/measurement":
            if last is None:
                self._send_json(
                    {
                        "status": "no_data",
                        "message": "No measurement received yet.",
                        "last_error": last_error,
                    },
                    status=HTTPStatus.SERVICE_UNAVAILABLE,
                )
                return

            self._send_json({"status": "ok", "measurement": asdict(last), "last_error": last_error})
            return

        self._send_json(
            {
                "status": "not_found",
                "message": "Use /health or /measurement",
            },
            status=HTTPStatus.NOT_FOUND,
        )

    def log_message(self, format_string, *args):
        return


class PollerThread(threading.Thread):
    def __init__(self, meter, state: BridgeState, interval_seconds: float):
        super().__init__(daemon=True)
        self._meter = meter
        self._state = state
        self._interval_seconds = interval_seconds
        self._stop_event = threading.Event()

    def stop(self):
        self._stop_event.set()

    def run(self):
        while not self._stop_event.is_set():
            try:
                measurement = self._meter.query_measurement()
                self._state.update_measurement(measurement)
            except Exception as exc:
                self._state.update_error(str(exc))
            time.sleep(self._interval_seconds)


def load_config(path: Path):
    with path.open("r", encoding="utf-8") as f:
        return json.load(f)


def main():
    parser = argparse.ArgumentParser(description="Orion Star pH serial bridge for LabVIEW")
    parser.add_argument("--config", default="app/config.json", help="Path to bridge config JSON")
    args = parser.parse_args()

    config_path = Path(args.config)
    if not config_path.exists():
        raise FileNotFoundError(f"Config not found: {config_path}. Copy app/config.example.json to app/config.json.")

    config = load_config(config_path)

    state = BridgeState()

    if config.get("mock_mode", False):
        meter = MockMeter()
    else:
        serial_cfg = config["serial"]
        meter_cfg = config["meter"]
        meter = OrionStarMeter(
            port=serial_cfg["port"],
            baudrate=serial_cfg["baudrate"],
            bytesize=serial_cfg["bytesize"],
            parity=serial_cfg["parity"],
            stopbits=serial_cfg["stopbits"],
            timeout_seconds=serial_cfg["timeout_seconds"],
            read_command=meter_cfg["read_command"],
            line_terminator=meter_cfg["line_terminator"],
        )

    poller = PollerThread(meter=meter, state=state, interval_seconds=config["polling"]["interval_seconds"])
    poller.start()

    api_cfg = config["api"]
    handler_cls = BridgeHTTPRequestHandler
    handler_cls.state = state

    server = ThreadingHTTPServer((api_cfg["host"], api_cfg["port"]), handler_cls)
    print(f"Bridge listening on http://{api_cfg['host']}:{api_cfg['port']}")
    print("Endpoints: /health, /measurement")

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        poller.stop()
        if hasattr(meter, "close"):
            meter.close()
        server.server_close()


if __name__ == "__main__":
    main()
