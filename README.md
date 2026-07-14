# LabVIEW pH Automation System (Orion Star)

This repository contains a desktop GUI application for automating pH measurement and calibration with an Orion Star meter.

## What You Get

- A desktop GUI application: [app/orionstar_gui.py](app/orionstar_gui.py)
- Shared meter and calibration logic: [app/orionstar_bridge.py](app/orionstar_bridge.py)
- Config template for serial/API parameters: [app/config.example.json](app/config.example.json)
- Optional LabVIEW design notes: [labview/README.md](labview/README.md)
- Protocol notes and commissioning checklist: [docs/orionstar_protocol_notes.md](docs/orionstar_protocol_notes.md)

## Architecture

1. Orion Star meter communicates over serial (USB virtual COM or RS-232).
2. The desktop GUI reads measurements directly from the meter.
3. The operator selects 2-point or 3-point calibration in the GUI settings.
4. The operator enters the buffer values and starts calibration from the GUI.

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
python app\orionstar_gui.py --config app\config.json
```

### 4) Build and run the Windows executable

```powershell
./build_exe.ps1
./dist/OrionStarPH/OrionStarPH.exe
```

The build copies [app/config.json](app/config.json) into the packaged app folder so you can edit the config next to the executable.

### 5) Use the GUI

- Click `Read Now` to query a measurement immediately.
- Click `Start Polling` to poll continuously using the configured interval.
- Open the `Settings` tab to set `Dark Mode` and `Read Duration (seconds)`.
- Select `2` or `3` in `Point Count`.
- Enter the calibration buffer values in the enabled `Buffer` fields.
- Click `Start Calibration` to begin guided calibration.
- At each step, place the probe in the prompted buffer and click `Continue Step`.
- Use `Cancel Calibration` to safely abort a run.

The right side of the window contains the calibration controls. Only the selected number of buffer fields stays enabled.

## GUI Behavior

- The measurement panel shows pH, temperature, mV, timestamp, raw response, and the last error.
- The calibration panel lets the user choose 2-point or 3-point calibration and enter the exact standard values before starting.
- Calibration is step-guided, requires operator confirmation per buffer, and times out if confirmation is not received.
- The settings tab includes Dark Mode and Read Duration (polling interval), with Apply and Save controls.
  - Read Duration is specified in minutes and must be between 0.1 and 60 minutes. Validation happens before any save to disk.
  - Apply changes theme immediately; Save persists settings to disk only if all validation passes.
- The event log records connection issues, read failures, and calibration completion state.
- `mock_mode` still works, so the GUI can be tested without hardware attached.

## Notes

- Start with `"mock_mode": true` in `app/config.json` to validate the GUI workflow before wiring the real meter.
- If parsing is not correct for your exact meter output string, adjust `parse_orionstar_line()` in [app/orionstar_bridge.py](app/orionstar_bridge.py).
- The calibration commands in config are placeholders. Replace them with the exact Orion Star calibration sequence for your meter model before running against hardware.
- `calibration.step_timeout_seconds` controls how long the app waits for each operator step before failing the run.
- The HTTP bridge code remains in [app/orionstar_bridge.py](app/orionstar_bridge.py) as shared logic, but the primary application is now the desktop GUI.
