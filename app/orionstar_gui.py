import argparse
import json
import queue
import sys
import threading
import tkinter as tk
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path
from tkinter import messagebox, ttk

from app.orionstar_bridge import (
    CalibrationRun,
    CalibrationStepResult,
    CalibrationState,
    build_calibration_request,
    load_config,
    open_meter_from_config,
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
        self._theme_initialized = False
        self._calibration_active = False
        self._calibration_cancel_event = threading.Event()
        self._calibration_waiting_for_continue = False
        self._calibration_continue_deadline = None
        self._calibration_step_timeout_job = None
        self._calibration_request = None
        self._calibration_step_index = 0

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
        self.calibration_prompt_var = tk.StringVar(value="Ready")
        self.point_count_var = tk.IntVar(value=int(self._config["calibration"]["point_count"]))
        self.standard_vars = [tk.StringVar(), tk.StringVar(), tk.StringVar()]
        self.dark_mode_var = tk.BooleanVar(value=bool(self._config.get("ui", {}).get("dark_mode", False)))
        interval_seconds = float(self._config["polling"].get("interval_seconds", 2.0))
        interval_minutes = interval_seconds / 60.0
        self.read_duration_var = tk.StringVar(value=str(interval_minutes))

        for index, value in enumerate(self._config["calibration"].get("standards", [])):
            if index < len(self.standard_vars):
                self.standard_vars[index].set(str(value))

        self._build_ui()
        self._apply_theme()
        self._connect_meter()
        self._sync_standard_inputs()
        self.after(100, self._drain_events)
        self.protocol("WM_DELETE_WINDOW", self._on_close)

    def _build_ui(self) -> None:
        root = ttk.Frame(self, padding=16)
        root.pack(fill="both", expand=True)
        root.rowconfigure(1, weight=1)
        root.rowconfigure(2, weight=1)

        header = ttk.Frame(root)
        header.grid(row=0, column=0, sticky="ew", pady=(0, 12))
        header.columnconfigure(0, weight=1)
        ttk.Label(header, text="Orion Star pH Measurement and Calibration", font=("Segoe UI", 16, "bold")).grid(
            row=0, column=0, sticky="w"
        )
        ttk.Label(header, textvariable=self.connection_var).grid(row=0, column=1, sticky="e")

        notebook = ttk.Notebook(root)
        notebook.grid(row=1, column=0, sticky="nsew")

        measurement_tab = ttk.Frame(notebook, padding=12)
        calibration_tab = ttk.Frame(notebook, padding=12)
        settings_tab = ttk.Frame(notebook, padding=12)
        notebook.add(measurement_tab, text="Measurement")
        notebook.add(calibration_tab, text="Calibration")
        notebook.add(settings_tab, text="Settings")

        measurement_frame = ttk.LabelFrame(measurement_tab, text="Measurement")
        measurement_frame.pack(fill="both", expand=True)
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

        calibration_frame = ttk.LabelFrame(calibration_tab, text="Calibration")
        calibration_frame.pack(fill="both", expand=True)
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
        ttk.Label(calibration_frame, text="Current Step").grid(row=6, column=0, sticky="w", padx=10, pady=6)
        ttk.Label(calibration_frame, textvariable=self.calibration_prompt_var).grid(row=6, column=1, sticky="w", padx=10, pady=6)

        calibration_buttons = ttk.Frame(calibration_frame)
        calibration_buttons.grid(row=7, column=0, columnspan=2, sticky="ew", padx=10, pady=(10, 12))
        calibration_buttons.columnconfigure(0, weight=1)
        calibration_buttons.columnconfigure(1, weight=1)
        calibration_buttons.columnconfigure(2, weight=1)

        self.start_calibration_button = ttk.Button(calibration_buttons, text="Start Calibration", command=self._start_calibration)
        self.start_calibration_button.grid(row=0, column=0, sticky="ew", padx=(0, 8))

        self.continue_calibration_button = ttk.Button(
            calibration_buttons,
            text="Continue Step",
            command=self._continue_calibration_step,
            state="disabled",
        )
        self.continue_calibration_button.grid(row=0, column=1, sticky="ew", padx=4)

        self.cancel_calibration_button = ttk.Button(
            calibration_buttons,
            text="Cancel Calibration",
            command=self._cancel_calibration,
            state="disabled",
        )
        self.cancel_calibration_button.grid(row=0, column=2, sticky="ew", padx=(8, 0))

        settings_frame = ttk.LabelFrame(settings_tab, text="Application Settings")
        settings_frame.pack(fill="both", expand=True)
        settings_frame.columnconfigure(1, weight=1)

        ttk.Checkbutton(settings_frame, text="Dark Mode", variable=self.dark_mode_var).grid(
            row=0, column=0, columnspan=2, sticky="w", padx=10, pady=(10, 6)
        )
        ttk.Label(settings_frame, text="Read Duration (minutes)").grid(row=1, column=0, sticky="w", padx=10, pady=6)
        ttk.Entry(settings_frame, textvariable=self.read_duration_var, width=16).grid(row=1, column=1, sticky="w", padx=10, pady=6)

        settings_buttons = ttk.Frame(settings_frame)
        settings_buttons.grid(row=2, column=0, columnspan=2, sticky="ew", padx=10, pady=(12, 10))
        settings_buttons.columnconfigure(0, weight=1)
        settings_buttons.columnconfigure(1, weight=1)
        ttk.Button(settings_buttons, text="Apply", command=self._apply_settings).grid(row=0, column=0, sticky="ew", padx=(0, 8))
        ttk.Button(settings_buttons, text="Save", command=self._save_settings).grid(row=0, column=1, sticky="ew", padx=(8, 0))

        log_frame = ttk.LabelFrame(root, text="Event Log")
        log_frame.grid(row=2, column=0, sticky="nsew", pady=(12, 0))
        log_frame.columnconfigure(0, weight=1)
        log_frame.rowconfigure(0, weight=1)
        self.log_widget = tk.Text(log_frame, height=12, wrap="word", state="disabled")
        self.log_widget.grid(row=0, column=0, sticky="nsew")
        scrollbar = ttk.Scrollbar(log_frame, orient="vertical", command=self.log_widget.yview)
        scrollbar.grid(row=0, column=1, sticky="ns")
        self.log_widget.configure(yscrollcommand=scrollbar.set)

    def _apply_theme(self) -> None:
        style = ttk.Style()
        if not self._theme_initialized:
            style.theme_use("clam")
            self._theme_initialized = True

        if self.dark_mode_var.get():
            bg = "#1e1e1e"
            panel = "#2a2a2a"
            fg = "#f2f2f2"
            text_bg = "#232323"
            text_fg = "#e8e8e8"
        else:
            bg = "#f0f0f0"
            panel = "#ffffff"
            fg = "#111111"
            text_bg = "#ffffff"
            text_fg = "#111111"

        self.configure(bg=bg)
        style.configure("TFrame", background=bg)
        style.configure("TLabel", background=bg, foreground=fg)
        style.configure("TLabelframe", background=bg, foreground=fg)
        style.configure("TLabelframe.Label", background=bg, foreground=fg)
        style.configure("TButton", background=panel, foreground=fg)
        style.configure("TCheckbutton", background=bg, foreground=fg)
        style.configure("TEntry", fieldbackground=panel, foreground=fg)
        style.configure("TCombobox", fieldbackground=panel, foreground=fg)
        style.configure("TNotebook", background=bg)
        style.configure("TNotebook.Tab", background=panel, foreground=fg)
        self.log_widget.configure(background=text_bg, foreground=text_fg, insertbackground=text_fg)

    def _validate_read_duration(self) -> tuple[bool, str]:
        """Validate read duration in minutes with strict bounds. Returns (is_valid, error_message)."""
        try:
            value = float(self.read_duration_var.get())
        except ValueError:
            return False, "Read duration must be a valid number"

        min_duration = 0.1  # minutes
        max_duration = 60.0  # minutes
        if value < min_duration or value > max_duration:
            return False, f"Read duration must be between {min_duration} and {max_duration} minutes"
        return True, ""

    def _parse_read_duration(self) -> float:
        """Parse read duration from minutes to seconds. Validates before returning."""
        is_valid, error_msg = self._validate_read_duration()
        if not is_valid:
            raise ValueError(error_msg)
        minutes = float(self.read_duration_var.get())
        return minutes * 60.0  # Convert to seconds

    def _apply_settings(self) -> bool:
        """Apply settings after validation. Returns True if successful, False otherwise."""
        is_valid, error_msg = self._validate_read_duration()
        if not is_valid:
            messagebox.showerror("Invalid Read Duration", error_msg)
            return False

        try:
            duration_minutes = float(self.read_duration_var.get())
            duration_seconds = duration_minutes * 60.0
        except ValueError as exc:
            messagebox.showerror("Invalid Read Duration", str(exc))
            return False

        self._config.setdefault("ui", {})["dark_mode"] = bool(self.dark_mode_var.get())
        self._config.setdefault("polling", {})["interval_seconds"] = duration_seconds
        self._apply_theme()
        self._append_log(f"Settings applied: dark_mode={self.dark_mode_var.get()}, read_duration={duration_minutes}min")
        return True

    def _save_settings(self) -> None:
        """Save settings to disk. Validates strictly before writing."""
        is_valid, error_msg = self._validate_read_duration()
        if not is_valid:
            messagebox.showerror("Cannot Save: Invalid Settings", error_msg)
            return

        if not self._apply_settings():
            return

        try:
            with self._config_path.open("w", encoding="utf-8") as config_file:
                json.dump(self._config, config_file, indent=2)
                config_file.write("\n")
            self._append_log(f"Settings saved to {self._config_path}")
            messagebox.showinfo("Settings Saved", "Settings have been saved successfully.")
        except OSError as exc:
            messagebox.showerror("Save Failed", f"Could not write to config file: {exc}")

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
        try:
            is_valid, _ = self._validate_read_duration()
            if is_valid:
                interval_minutes = float(self.read_duration_var.get())
                interval_seconds = interval_minutes * 60.0
            else:
                interval_seconds = float(self._config["polling"].get("interval_seconds", 2.0))
        except (ValueError, KeyError):
            interval_seconds = 2.0
        interval_ms = max(250, int(interval_seconds * 1000))
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

    def _set_calibration_controls(self, active: bool, waiting_for_continue: bool = False) -> None:
        self.start_calibration_button.configure(state="disabled" if active else "normal")
        self.continue_calibration_button.configure(state="normal" if waiting_for_continue else "disabled")
        self.cancel_calibration_button.configure(state="normal" if active else "disabled")

    def _get_calibration_step_timeout_seconds(self) -> float:
        return float(self._config.get("calibration", {}).get("step_timeout_seconds", 180.0))

    def _start_calibration(self) -> None:
        if self._meter is None:
            messagebox.showerror("Meter Not Connected", "The meter is not connected. Check config and serial settings.")
            return

        if self._polling_enabled:
            messagebox.showerror("Stop Polling First", "Stop auto polling before starting calibration.")
            return

        if self._calibration_active:
            messagebox.showinfo("Calibration In Progress", "A calibration run is already active.")
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

        self._calibration_request = request
        self._calibration_step_index = 0
        self._calibration_active = True
        self._calibration_waiting_for_continue = False
        self._calibration_continue_deadline = None
        self._calibration_cancel_event.clear()
        self._set_calibration_controls(active=True, waiting_for_continue=False)

        queued_run = CalibrationRun(
            started_utc=datetime.now(timezone.utc).isoformat(),
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
        self.calibration_prompt_var.set("Entering calibration mode...")
        self._append_log(f"Calibration started with {request.point_count} points: {request.standards}")

        thread = threading.Thread(
            target=self._calibration_enter_worker,
            args=(request,),
            daemon=True,
        )
        thread.start()

    def _calibration_enter_worker(self, request) -> None:
        try:
            self._meter.send_command(
                request.enter_command,
                line_terminator=self._config["meter"]["line_terminator"],
                expect_response=False,
            )
            self._events.put(("calibration_entered", None))
        except Exception as exc:
            self._events.put(("calibration_error", str(exc)))

    def _prompt_next_calibration_step(self) -> None:
        request = self._calibration_request
        if request is None:
            return

        if self._calibration_step_index >= len(request.standards):
            self._finish_calibration(success=True, message="Calibration sequence completed")
            return

        step_number = self._calibration_step_index + 1
        standard = request.standards[self._calibration_step_index]
        timeout_seconds = self._get_calibration_step_timeout_seconds()

        self._calibration_waiting_for_continue = True
        self._calibration_continue_deadline = datetime.now(timezone.utc).timestamp() + timeout_seconds
        self.calibration_status_var.set("waiting_for_operator")
        self.calibration_prompt_var.set(
            f"Step {step_number}/{len(request.standards)}: place probe in pH {standard:.2f}, then click Continue"
        )
        self._append_log(
            f"Awaiting operator for step {step_number}: buffer pH {standard:.2f}. Timeout in {int(timeout_seconds)}s."
        )
        self._set_calibration_controls(active=True, waiting_for_continue=True)
        self._schedule_calibration_timeout_check()

    def _continue_calibration_step(self) -> None:
        if not self._calibration_active or not self._calibration_waiting_for_continue:
            return

        request = self._calibration_request
        if request is None:
            return

        self._calibration_waiting_for_continue = False
        self._calibration_continue_deadline = None
        self._set_calibration_controls(active=True, waiting_for_continue=False)

        step_number = self._calibration_step_index + 1
        standard = request.standards[self._calibration_step_index]
        self.calibration_status_var.set("running_step")
        self.calibration_prompt_var.set(f"Running step {step_number} for pH {standard:.2f}...")

        worker = threading.Thread(
            target=self._run_calibration_step_worker,
            args=(step_number, standard, request),
            daemon=True,
        )
        worker.start()

    def _run_calibration_step_worker(self, step_number: int, standard: float, request) -> None:
        try:
            settle_seconds = max(0.0, request.settle_seconds)
            elapsed = 0.0
            while elapsed < settle_seconds:
                if self._calibration_cancel_event.is_set():
                    self._events.put(("calibration_canceled", "Calibration canceled by operator"))
                    return
                sleep_slice = min(0.2, settle_seconds - elapsed)
                threading.Event().wait(sleep_slice)
                elapsed += sleep_slice

            if self._calibration_cancel_event.is_set():
                self._events.put(("calibration_canceled", "Calibration canceled by operator"))
                return

            command = request.point_command_template.format(point=step_number, value=standard)
            raw_response = self._meter.send_command(
                command,
                line_terminator=self._config["meter"]["line_terminator"],
                expect_response=True,
            )

            self._events.put(
                (
                    "calibration_step_done",
                    {
                        "point_index": step_number,
                        "standard_value": standard,
                        "command": command,
                        "raw_response": raw_response,
                    },
                )
            )
        except Exception as exc:
            self._events.put(("calibration_error", str(exc)))

    def _schedule_calibration_timeout_check(self) -> None:
        if self._calibration_step_timeout_job is not None:
            self.after_cancel(self._calibration_step_timeout_job)
        self._calibration_step_timeout_job = self.after(500, self._check_calibration_timeout)

    def _check_calibration_timeout(self) -> None:
        self._calibration_step_timeout_job = None
        if not self._calibration_active or not self._calibration_waiting_for_continue:
            return

        if self._calibration_continue_deadline is not None:
            now_ts = datetime.now(timezone.utc).timestamp()
            if now_ts >= self._calibration_continue_deadline:
                self._finish_calibration(success=False, message="Timed out waiting for operator confirmation")
                return

        self._schedule_calibration_timeout_check()

    def _cancel_calibration(self) -> None:
        if not self._calibration_active:
            return

        self._calibration_cancel_event.set()
        if self._calibration_waiting_for_continue:
            self._finish_calibration(success=False, message="Calibration canceled by operator")
        else:
            self.calibration_status_var.set("canceling")
            self.calibration_prompt_var.set("Cancel requested. Waiting for active step to stop...")

    def _finish_calibration(self, success: bool, message: str) -> None:
        snapshot = self._calibration_state.snapshot()
        snapshot.status = "completed" if success else "failed"
        snapshot.message = message
        snapshot.finished_utc = datetime.now(timezone.utc).isoformat()

        request = self._calibration_request
        if success and request and request.exit_command:
            try:
                self._meter.send_command(
                    request.exit_command,
                    line_terminator=self._config["meter"]["line_terminator"],
                    expect_response=False,
                )
            except Exception as exc:
                snapshot.status = "failed"
                snapshot.message = f"Calibration complete but exit command failed: {exc}"

        self._calibration_state.set_run(snapshot)
        self.calibration_status_var.set(snapshot.status)
        self.calibration_prompt_var.set(message)
        self._append_log(f"Calibration {snapshot.status}: {snapshot.message}")

        self._calibration_active = False
        self._calibration_waiting_for_continue = False
        self._calibration_continue_deadline = None
        self._calibration_request = None
        self._calibration_step_index = 0
        self._set_calibration_controls(active=False, waiting_for_continue=False)

        if self._calibration_step_timeout_job is not None:
            self.after_cancel(self._calibration_step_timeout_job)
            self._calibration_step_timeout_job = None

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
            elif event_type == "calibration_entered":
                snapshot = self._calibration_state.snapshot()
                snapshot.status = "running"
                snapshot.message = "Calibration mode entered"
                self._calibration_state.set_run(snapshot)
                self._prompt_next_calibration_step()
            elif event_type == "calibration_step_done":
                if self._calibration_cancel_event.is_set():
                    self._finish_calibration(success=False, message="Calibration canceled by operator")
                    continue

                snapshot = self._calibration_state.snapshot()
                step_result = CalibrationStepResult(
                    point_index=payload["point_index"],
                    standard_value=payload["standard_value"],
                    command=payload["command"],
                    acknowledged=bool(payload.get("raw_response")),
                    raw_response=payload.get("raw_response") or None,
                )
                snapshot.steps = snapshot.steps or []
                snapshot.steps.append(asdict(step_result))
                self._calibration_state.set_run(snapshot)
                self._append_log(
                    f"Step {step_result.point_index} done, response: {step_result.raw_response or 'NO RESPONSE'}"
                )
                self._calibration_step_index += 1
                self._prompt_next_calibration_step()
            elif event_type == "calibration_canceled":
                self._finish_calibration(success=False, message=str(payload))
            elif event_type == "calibration_error":
                self._finish_calibration(success=False, message=f"Calibration error: {payload}")

        snapshot = self._calibration_state.snapshot()
        self.calibration_status_var.set(snapshot.status)
        self.after(100, self._drain_events)

    def _on_close(self) -> None:
        self._calibration_cancel_event.set()
        if self._calibration_step_timeout_job is not None:
            self.after_cancel(self._calibration_step_timeout_job)
            self._calibration_step_timeout_job = None
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