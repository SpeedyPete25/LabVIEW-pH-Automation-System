# Orion Star Protocol Notes

Exact serial command/response details can vary by Orion Star model and firmware.

## What To Confirm On Your Meter

- Output mode: on-demand command vs continuous stream
- Serial link type: USB virtual COM or RS-232
- Baudrate, parity, data bits, stop bits
- Command string for requesting current measurement
- Response line format and terminator (`CR`, `LF`, or `CRLF`)

## Suggested Bring-Up Procedure

1. Use terminal software (Tera Term, PuTTY, RealTerm) on meter COM port.
2. Send candidate command, for example `READ?`.
3. Capture the exact returned line.
4. Update `meter.read_command` and `meter.line_terminator` in `app/config.json`.
5. If needed, update parser logic in `parse_orionstar_line()`.

## Parser Behavior in This Starter

The parser attempts to extract:

- pH value (`pH=7.01`, `7.01 pH`, or first numeric CSV field)
- Temperature in C (`T=24.8C`, `Temp:24.8`)
- mV (`mV=-3.1`)

If your meter format is different, add a regex pattern for your exact string format.

## Reliability Recommendations

- Implement retry/backoff on serial failures.
- Add watchdog in LabVIEW for stale data.
- Log raw lines for traceability during validation.
- Perform periodic calibration checks and include calibration metadata in logs.
