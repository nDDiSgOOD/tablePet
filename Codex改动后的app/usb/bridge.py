"""USB 串口工作线程 / USB serial worker thread with auto-reconnect."""

from __future__ import annotations

import asyncio
import threading
import time

from .. import state
from ..config import USB_BRIDGE_ENABLED, USB_SERIAL_BAUD
from .handlers import handle_usb_frame
from .protocol import usb_candidate_ports, usb_read_frame, usb_write_frame, usb_json_bytes
from ..config import USB_ERROR_JSON

try:
    import serial
except Exception:  # pragma: no cover
    serial = None  # type: ignore[assignment]


def _sleep_backoff(delay: float) -> None:
    """带 250ms 唤醒间隔的指数退避睡眠。"""
    deadline = time.monotonic() + delay
    while time.monotonic() < deadline:
        sleep_remain = deadline - time.monotonic()
        if sleep_remain > 0:
            time.sleep(min(0.25, sleep_remain))


def _usb_bridge_worker(loop: asyncio.AbstractEventLoop) -> None:
    if not USB_BRIDGE_ENABLED:
        return
    if serial is None:
        state.USB_BRIDGE_STATE["last_error"] = "pyserial is not installed"
        return

    retry_delay = 0.5  # start at 500ms, max 30s
    max_retry_delay = 30.0
    consecutive_errors = 0

    while True:
        opened = None
        try:
            ports = usb_candidate_ports()
            if not ports:
                state.USB_BRIDGE_STATE["connected"] = False
                state.USB_BRIDGE_STATE["last_error"] = "no USB serial port found"
                _sleep_backoff(retry_delay)
                retry_delay = min(retry_delay * 1.5, max_retry_delay)
                continue

            for port_name in ports:
                try:
                    opened = serial.Serial(
                        port_name,
                        USB_SERIAL_BAUD,
                        timeout=0.15,
                        write_timeout=5.0,
                        rtscts=False,
                        dsrdtr=False,
                    )
                    state.USB_BRIDGE_STATE["port"] = port_name
                    break
                except Exception as exc:
                    state.USB_BRIDGE_STATE["last_error"] = f"{port_name}: {exc}"
                    opened = None
            if opened is None:
                state.USB_BRIDGE_STATE["connected"] = False
                _sleep_backoff(retry_delay)
                retry_delay = min(retry_delay * 1.5, max_retry_delay)
                continue

            # Reset backoff on successful connection
            retry_delay = 0.5
            consecutive_errors = 0

            with opened as port:
                state.USB_BRIDGE_STATE["connected"] = True
                state.USB_BRIDGE_STATE["last_error"] = ""
                while True:
                    seq = 0
                    try:
                        frame = usb_read_frame(port)
                        if frame is None:
                            continue
                        msg_type, seq, payload = frame
                        future = asyncio.run_coroutine_threadsafe(
                            handle_usb_frame(msg_type, payload), loop
                        )
                        response_type, response_payload = future.result(timeout=90)
                        if response_type is not None:
                            usb_write_frame(port, response_type, seq, response_payload)
                        consecutive_errors = 0
                    except TimeoutError:
                        continue  # Normal timeout, keep reading
                    except (serial.SerialException, IOError, OSError):
                        raise  # Reconnect needed
                    except Exception as exc:
                        consecutive_errors += 1
                        try:
                            usb_write_frame(
                                port,
                                USB_ERROR_JSON,
                                seq,
                                usb_json_bytes(
                                    {
                                        "ok": False,
                                        "error": str(exc)[:500],
                                        "consecutive_errors": consecutive_errors,
                                    }
                                ),
                            )
                        except Exception:
                            pass  # Port may be dead
        except (serial.SerialException, IOError, OSError) as exc:
            state.USB_BRIDGE_STATE["connected"] = False
            state.USB_BRIDGE_STATE["last_error"] = f"Connection lost: {exc}"
            _sleep_backoff(retry_delay)
            retry_delay = min(retry_delay * 2.0, max_retry_delay)
        except Exception as exc:
            state.USB_BRIDGE_STATE["connected"] = False
            state.USB_BRIDGE_STATE["last_error"] = str(exc)[:500]
            _sleep_backoff(retry_delay)
            retry_delay = min(retry_delay * 2.0, max_retry_delay)


def start_usb_bridge_thread(loop: asyncio.AbstractEventLoop) -> None:
    """由 FastAPI 启动钩子调用，确保只启动一次。"""
    if state.USB_THREAD_STARTED:
        return
    state.USB_THREAD_STARTED = True
    thread = threading.Thread(
        target=_usb_bridge_worker,
        args=(loop,),
        name="tablepet-usb-bridge",
        daemon=True,
    )
    thread.start()
