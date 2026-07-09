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

### 3) Run bridge

```powershell
python app\orionstar_bridge.py --config app\config.json
```

### 4) Test API

Open in browser or LabVIEW HTTP client:

- `http://127.0.0.1:8787/health`
- `http://127.0.0.1:8787/measurement`

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
