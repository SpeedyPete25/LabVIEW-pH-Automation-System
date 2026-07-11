import argparse
import queue
import sys
import threading
import tkinter as tk
from dataclasses import asdict
from pathlib import Path
from tkinter import messagebox, ttk

from app.orionstar_bridge import (
    CalibrationRun,
    CalibrationState,
    build_calibration_request,
    load_config,
    open_meter_from_config,
    run_calibration,
)


def resolve_config_path(config_arg: str) -> Path:
    candidate = Path(config_arg)
    if candidate.exists():
        return candidate

    base_dir = Path(sys.executable).resolve().parent if getattr(sys, "frozen", False) else Path(__file__).resolve().parents[1]
    search_paths = [
        base_dir / config_arg,
        base_dir / "app" / "config.json",
        base_dir / "config.json",
    ]

    for path in search_paths:
        if path.exists():
            return path

    return candidate


class OrionStarGUI(tk.Tk):
    def __init__(self, config_path: Path):
        super().__init__()
        self.title("Orion Star pH Controller")
        self.geometry("900x640")
        self.minsize(860, 580)

        self._config_path = config_path
        self._config = load_config(config_path)
        self._events = queue.Queue()
        self._calibration_state = CalibrationState()
        self._meter = None
        self._measurement_inflight = False
        self._polling_enabled = False
        self._poll_job = None

        self.connection_var = tk.StringVar(value="Disconnected")
        self.mode_var = tk.StringVar(value="mock" if self._config.get("mock_mode") else "live")
        self.status_var = tk.StringVar(value="Ready")
        self.last_error_var = tk.StringVar(value="")
        self.ph_var = tk.StringVar(value="--")
        self.temperature_var = tk.StringVar(value="--")
        self.mv_var = tk.StringVar(value="--")
        self.timestamp_var = tk.StringVar(value="--")
        self.raw_var = tk.StringVar(value="--")
        self.calibration_status_var = tk.StringVar(value="idle")
        self.point_count_var = tk.IntVar(value=int(self._config["calibration"]["point_count"]))
        self.standard_vars = [tk.StringVar(), tk.StringVar(), tk.StringVar()]

        for index, value in enumerate(self._config["calibration"].get("standards", [])):
            if index < len(self.standard_vars):
                self.standard_vars[index].set(str(value))

        self._build_ui()
        self._connect_meter()
        self._sync_standard_inputs()
        self.after(100, self._drain_events)
        self.protocol("WM_DELETE_WINDOW", self._on_close)

    def _build_ui(self) -> None:
        root = ttk.Frame(self, padding=16)
        root.pack(fill="both", expand=True)
        root.columnconfigure(0, weight=3)
        root.columnconfigure(1, weight=2)
        root.rowconfigure(2, weight=1)

        header = ttk.Frame(root)
        header.grid(row=0, column=0, columnspan=2, sticky="ew", pady=(0, 12))
        header.columnconfigure(0, weight=1)
        ttk.Label(header, text="Orion Star pH Measurement and Calibration", font=("Segoe UI", 16, "bold")).grid(
            row=0, column=0, sticky="w"
        )
        ttk.Label(header, textvariable=self.connection_var).grid(row=0, column=1, sticky="e")

        measurement_frame = ttk.LabelFrame(root, text="Measurement")
        measurement_frame.grid(row=1, column=0, sticky="nsew", padx=(0, 12))
        measurement_frame.columnconfigure(1, weight=1)

        measurement_rows = [
            ("Status", self.status_var),
            ("pH", self.ph_var),
            ("Temperature (C)", self.temperature_var),
            ("mV", self.mv_var),
            ("Timestamp UTC", self.timestamp_var),
            ("Raw", self.raw_var),
            ("Last Error", self.last_error_var),
        ]
        for row_index, (label_text, variable) in enumerate(measurement_rows):
            ttk.Label(measurement_frame, text=label_text).grid(row=row_index, column=0, sticky="w", padx=10, pady=6)
            ttk.Label(measurement_frame, textvariable=variable).grid(row=row_index, column=1, sticky="w", padx=10, pady=6)

        controls_frame = ttk.Frame(measurement_frame)
        controls_frame.grid(row=len(measurement_rows), column=0, columnspan=2, sticky="ew", padx=10, pady=(10, 12))
        controls_frame.columnconfigure(0, weight=1)
        controls_frame.columnconfigure(1, weight=1)
        controls_frame.columnconfigure(2, weight=1)
        ttk.Button(controls_frame, text="Read Now", command=self._request_measurement).grid(row=0, column=0, sticky="ew", padx=(0, 8))
        ttk.Button(controls_frame, text="Start Polling", command=self._start_polling).grid(row=0, column=1, sticky="ew", padx=4)
        ttk.Button(controls_frame, text="Stop Polling", command=self._stop_polling).grid(row=0, column=2, sticky="ew", padx=(8, 0))

        calibration_frame = ttk.LabelFrame(root, text="Calibration")
        calibration_frame.grid(row=1, column=1, sticky="nsew")
        calibration_frame.columnconfigure(1, weight=1)

        ttk.Label(calibration_frame, text="Mode").grid(row=0, column=0, sticky="w", padx=10, pady=6)
        ttk.Label(calibration_frame, textvariable=self.mode_var).grid(row=0, column=1, sticky="w", padx=10, pady=6)
        ttk.Label(calibration_frame, text="Point Count").grid(row=1, column=0, sticky="w", padx=10, pady=6)
        point_combo = ttk.Combobox(calibration_frame, textvariable=self.point_count_var, values=(2, 3), state="readonly", width=8)
        point_combo.grid(row=1, column=1, sticky="w", padx=10, pady=6)
        point_combo.bind("<<ComboboxSelected>>", lambda _event: self._sync_standard_inputs())

        self.standard_entries = []
        for index, variable in enumerate(self.standard_vars):
            ttk.Label(calibration_frame, text=f"Buffer {index + 1}").grid(row=2 + index, column=0, sticky="w", padx=10, pady=6)
            entry = ttk.Entry(calibration_frame, textvariable=variable, width=18)
            entry.grid(row=2 + index, column=1, sticky="ew", padx=10, pady=6)
            self.standard_entries.append(entry)

        ttk.Label(calibration_frame, text="Calibration Status").grid(row=5, column=0, sticky="w", padx=10, pady=6)
        ttk.Label(calibration_frame, textvariable=self.calibration_status_var).grid(row=5, column=1, sticky="w", padx=10, pady=6)

        ttk.Button(calibration_frame, text="Start Calibration", command=self._start_calibration).grid(
            row=6, column=0, columnspan=2, sticky="ew", padx=10, pady=(10, 12)
        )

        log_frame = ttk.LabelFrame(root, text="Event Log")
        log_frame.grid(row=2, column=0, columnspan=2, sticky="nsew", pady=(12, 0))
        log_frame.columnconfigure(0, weight=1)
        log_frame.rowconfigure(0, weight=1)
        self.log_widget = tk.Text(log_frame, height=12, wrap="word", state="disabled")
        self.log_widget.grid(row=0, column=0, sticky="nsew")
        scrollbar = ttk.Scrollbar(log_frame, orient="vertical", command=self.log_widget.yview)
        scrollbar.grid(row=0, column=1, sticky="ns")
        self.log_widget.configure(yscrollcommand=scrollbar.set)

    def _connect_meter(self) -> None:
        try:
            self._meter = open_meter_from_config(self._config)
            self.connection_var.set(f"Connected ({self.mode_var.get()})")
            self._append_log(f"Loaded config from {self._config_path}")
        except Exception as exc:
            self._meter = None
            self.connection_var.set("Connection failed")
            self.last_error_var.set(str(exc))
            self._append_log(f"Meter connection failed: {exc}")

    def _sync_standard_inputs(self) -> None:
        point_count = self.point_count_var.get()
        for index, entry in enumerate(self.standard_entries):
            state = "normal" if index < point_count else "disabled"
            entry.configure(state=state)
            if state == "disabled":
                self.standard_vars[index].set("")

    def _append_log(self, message: str) -> None:
        self.log_widget.configure(state="normal")
        self.log_widget.insert("end", f"{message}\n")
        self.log_widget.see("end")
        self.log_widget.configure(state="disabled")

    def _request_measurement(self) -> None:
        if self._measurement_inflight:
            return
        if self._meter is None:
            messagebox.showerror("Meter Not Connected", "The meter is not connected. Check config and serial settings.")
            return

        self._measurement_inflight = True
        thread = threading.Thread(target=self._measurement_worker, daemon=True)
        thread.start()

    def _measurement_worker(self) -> None:
        try:
            measurement = self._meter.query_measurement()
            self._events.put(("measurement", asdict(measurement)))
        except Exception as exc:
            self._events.put(("measurement_error", str(exc)))
        finally:
            self._events.put(("measurement_done", None))

    def _start_polling(self) -> None:
        if self._polling_enabled:
            return
        self._polling_enabled = True
        self._append_log("Auto polling started")
        self._schedule_poll()

    def _schedule_poll(self) -> None:
        if not self._polling_enabled:
            return
        interval_ms = max(250, int(float(self._config["polling"]["interval_seconds"]) * 1000))
        self._poll_job = self.after(interval_ms, self._poll_once)

    def _poll_once(self) -> None:
        self._request_measurement()
        self._schedule_poll()

    def _stop_polling(self) -> None:
        self._polling_enabled = False
        if self._poll_job is not None:
            self.after_cancel(self._poll_job)
            self._poll_job = None
        self._append_log("Auto polling stopped")

    def _start_calibration(self) -> None:
        if self._meter is None:
            messagebox.showerror("Meter Not Connected", "The meter is not connected. Check config and serial settings.")
            return

        try:
            standards = [float(self.standard_vars[index].get()) for index in range(self.point_count_var.get())]
            payload = {
                "point_count": self.point_count_var.get(),
                "standards": standards,
            }
            request = build_calibration_request(self._config, payload)
        except ValueError as exc:
            messagebox.showerror("Invalid Calibration Settings", str(exc))
            return

        queued_run = CalibrationRun(
            started_utc="queued",
            status="queued",
            message="Calibration queued from GUI",
            request={
                "point_count": request.point_count,
                "standards": request.standards,
                "enter_command": request.enter_command,
                "point_command_template": request.point_command_template,
                "settle_seconds": request.settle_seconds,
                "exit_command": request.exit_command,
            },
            steps=[],
        )
        self._calibration_state.set_run(queued_run)
        self.calibration_status_var.set("queued")
        self._append_log(f"Calibration started with {request.point_count} points: {request.standards}")

        thread = threading.Thread(
            target=self._calibration_worker,
            args=(request,),
            daemon=True,
        )
        thread.start()

    def _calibration_worker(self, request) -> None:
        try:
            run_calibration(
                self._meter,
                request,
                self._calibration_state,
                self._config["meter"]["line_terminator"],
            )
            self._events.put(("calibration_update", None))
        except Exception as exc:
            self._events.put(("calibration_error", str(exc)))

    def _drain_events(self) -> None:
        while True:
            try:
                event_type, payload = self._events.get_nowait()
            except queue.Empty:
                break

            if event_type == "measurement":
                self.status_var.set("Measurement updated")
                self.ph_var.set(str(payload.get("ph", "--")))
                self.temperature_var.set(str(payload.get("temperature_c", "--")))
                self.mv_var.set(str(payload.get("mv", "--")))
                self.timestamp_var.set(payload.get("timestamp_utc", "--"))
                self.raw_var.set(payload.get("raw", "--"))
                self.last_error_var.set("")
            elif event_type == "measurement_error":
                self.status_var.set("Measurement failed")
                self.last_error_var.set(payload)
                self._append_log(f"Measurement error: {payload}")
            elif event_type == "measurement_done":
                self._measurement_inflight = False
            elif event_type == "calibration_update":
                snapshot = self._calibration_state.snapshot()
                self.calibration_status_var.set(snapshot.status)
                self._append_log(f"Calibration {snapshot.status}: {snapshot.message}")
            elif event_type == "calibration_error":
                self.calibration_status_var.set("failed")
                self._append_log(f"Calibration error: {payload}")

        snapshot = self._calibration_state.snapshot()
        self.calibration_status_var.set(snapshot.status)
        self.after(100, self._drain_events)

    def _on_close(self) -> None:
        self._stop_polling()
        if self._meter is not None and hasattr(self._meter, "close"):
            self._meter.close()
        self.destroy()


def main() -> None:
    parser = argparse.ArgumentParser(description="Orion Star pH GUI application")
    parser.add_argument("--config", default="app/config.json", help="Path to GUI config JSON")
    args = parser.parse_args()

    config_path = resolve_config_path(args.config)
    if not config_path.exists():
        raise FileNotFoundError(f"Config not found: {config_path}. Copy app/config.example.json to app/config.json.")

    app = OrionStarGUI(config_path)
    app.mainloop()


if __name__ == "__main__":
    main()