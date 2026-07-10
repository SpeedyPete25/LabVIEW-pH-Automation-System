# LabVIEW pH Automation System (Orion Star)

This repository contains a starter architecture for automating pH measurement from an Orion Star meter and consuming the measurements in LabVIEW.

## What You Get

- A Python serial bridge service: [app/orionstar_bridge.py](app/orionstar_bridge.py)
- Config template for serial/API parameters: [app/config.example.json](app/config.example.json)
- LabVIEW integration workflow: [labview/README.md](labview/README.md)
- Protocol notes and commissioning checklist: [docs/orionstar_protocol_notes.md](docs/orionstar_protocol_notes.md)

## Architecture

1. Orion Star meter communicates over serial (USB virtual COM or RS-232).
2. Python bridge polls the meter and exposes latest reading over local HTTP.
3. LabVIEW VI calls `GET /measurement` and logs/visualizes data.
4. LabVIEW VI can start a calibration run with `POST /calibration/start` using point settings from config or request body.

## Quick Start

### 1) Python setup

```powershell
cd c:\Users\mfigm\Documents\LabVIEW-pH-Automation-System
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

### 2) Configure device settings

```powershell
copy app\config.example.json app\config.json
```

Then edit `app/config.json`:

- Set serial port (`COMx`)
- Confirm baudrate/parity/stop bits from Orion Star settings
- Confirm `read_command` and line terminator used by your meter model
- Configure `calibration.point_count` as 2 or 3
- Enter the standard buffer values in `calibration.standards`
- Replace the calibration command templates with the exact commands for your Orion Star model

### 3) Run bridge

```powershell
python app\orionstar_bridge.py --config app\config.json
```

### 4) Test API

Open in browser or LabVIEW HTTP client:

- `http://127.0.0.1:8787/health`
- `http://127.0.0.1:8787/measurement`
- `http://127.0.0.1:8787/calibration/status`

Start calibration with an HTTP `POST` to `http://127.0.0.1:8787/calibration/start` and a JSON body like:

```json
{
  "point_count": 3,
  "standards": [4.0, 7.0, 10.0]
}
```

## Measurement Response Format

```json
{
  "status": "ok",
  "measurement": {
    "timestamp_utc": "2026-07-09T12:34:56.123456+00:00",
    "ph": 7.012,
    "temperature_c": 24.9,
    "mv": -0.71,
    "raw": "pH=7.012,T=24.9C,mV=-0.71",
    "source": "meter"
  },
  "last_error": null
}
```

## Notes

- Start with `"mock_mode": true` in `app/config.json` to validate LabVIEW integration before wiring the real meter.
- If parsing is not correct for your exact meter output string, adjust `parse_orionstar_line()` in [app/orionstar_bridge.py](app/orionstar_bridge.py).
- The calibration commands in config are placeholders. Replace them with the exact Orion Star calibration sequence for your meter model before running against hardware.
