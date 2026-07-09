# LabVIEW Integration Guide

This guide shows how to create a LabVIEW VI that reads pH values from the local bridge API.

## Recommended VI Design

Use a Producer/Consumer architecture:

1. Producer loop (timed loop, e.g. 1 s)
2. HTTP GET request to `/measurement`
3. Parse JSON
4. Enqueue measurement cluster
5. Consumer loop logs data, updates UI, and handles alarms

## Core LabVIEW Blocks

- HTTP Client Open Handle.vi
- HTTP Client GET.vi
- JSONtext or Unflatten From JSON.vi
- Queue functions (Obtain Queue, Enqueue, Dequeue)
- While Loop + Wait (ms)
- Error cluster propagation and merge

## Data Cluster Suggestion

Create a typedef cluster named `pH_Measurement.ctl`:

- timestamp_utc (string)
- ph (DBL)
- temperature_c (DBL)
- mv (DBL)
- raw (string)
- source (string)
- valid (Boolean)
- error_message (string)

## Polling Logic

1. Call `GET http://127.0.0.1:8787/measurement`
2. If `status == "ok"`, set `valid = TRUE`
3. If service unavailable or parse fails, set `valid = FALSE`
4. Always enqueue data to keep the consumer loop deterministic

## Logging Suggestions

- CSV with timestamp, pH, temperature, mV, validity
- TDMS for long acquisition runs
- Add metadata row: operator, probe ID, calibration timestamp

## Alarm Ideas

- High/Low pH limit alarms
- Stale-data alarm if timestamp age > threshold
- Sensor fault alarm if `last_error` is non-empty

## Commissioning Checklist

- Verify COM settings match meter
- Verify bridge health endpoint is `ok`
- Verify LabVIEW parses valid and invalid responses
- Verify disconnect/reconnect handling
- Verify file logging and alarm behavior under faults
