from __future__ import annotations

import importlib.metadata
import time
from typing import Any

import numpy as np

from .base import PowerProfilerDriver, SampleBatch

PPK2_VID = 0x1915
PPK2_PID = 0xC00A
PPK2_MEASUREMENT_INTERFACE = "01"


def _load_api():
    try:
        from ppk2_api.ppk2_api import PPK2_API, PPK2_MP
    except ImportError as exc:  # pragma: no cover - hardware dependency
        raise RuntimeError("ppk2-api is not installed") from exc
    return PPK2_API, PPK2_MP


def _interface_from_port(port: Any) -> str | None:
    """Best-effort extraction of the USB interface number.

    Windows exposes the PPK2 measurement/control CDC interface as MI_01 in the
    PNP/HWID string. On Linux/macOS pyserial usually exposes a location ending in
    ``.1`` or ``1``. We retain the value for logging even when it cannot be found.
    """
    hwid = (getattr(port, "hwid", None) or "").upper()
    for token in hwid.replace("&", " ").replace("\\", " ").split():
        if token.startswith("MI_") and len(token) >= 5:
            return token[3:5]
    location = str(getattr(port, "location", None) or "")
    if location:
        tail = location.split(".")[-1]
        if tail.isdigit():
            return tail.zfill(2)
        if location[-1:].isdigit():
            return location[-1:].zfill(2)
    return None


def _stable_device_id(port: Any, interface: str | None) -> str:
    serial_number = str(getattr(port, "serial_number", None) or "").strip()
    if serial_number:
        return serial_number
    location = str(getattr(port, "location", None) or "").strip()
    if location:
        return f"USB-{PPK2_VID:04X}:{PPK2_PID:04X}@{location}"
    hwid = str(getattr(port, "hwid", None) or "").strip()
    # The PNP instance suffix is not guaranteed to be stable after moving USB ports,
    # but it is still more useful in a measurement log than the transient COM name.
    if hwid:
        return f"USB-{PPK2_VID:04X}:{PPK2_PID:04X}:{interface or '??'}:{hwid}"
    return f"PPK2-{getattr(port, 'device', 'unknown')}"


def discover_ppk2_devices() -> list[dict[str, Any]]:
    """Discover every PPK2 measurement/control interface without relying on the
    Windows device description.

    The upstream ppk2-api currently filters Windows devices by a particular display
    name. Generic Windows CDC drivers often expose the same hardware as
    ``Serielles USB-Gerät`` instead. Filtering by Nordic VID/PID and MI_01 is more
    robust and also supports several PPK2 units at the same time.
    """
    try:
        import serial.tools.list_ports
    except ImportError:
        return []

    found: list[dict[str, Any]] = []
    for port in serial.tools.list_ports.comports():
        vid = getattr(port, "vid", None)
        pid = getattr(port, "pid", None)
        if vid != PPK2_VID or pid != PPK2_PID:
            continue
        interface = _interface_from_port(port)
        # PPK2 exposes more than one CDC interface. MI_01 is the measurement/control
        # interface validated against the device used for this pilot.
        if interface not in {None, PPK2_MEASUREMENT_INTERFACE}:
            continue
        device_id = _stable_device_id(port, interface)
        found.append(
            {
                "port": str(port.device),
                "device_id": device_id,
                "serial": str(getattr(port, "serial_number", None) or ""),
                "vid": f"{PPK2_VID:04X}",
                "pid": f"{PPK2_PID:04X}",
                "interface": interface or "",
                "manufacturer": str(getattr(port, "manufacturer", None) or ""),
                "product": str(getattr(port, "product", None) or "PPK2"),
                "description": str(getattr(port, "description", None) or ""),
                "location": str(getattr(port, "location", None) or ""),
                "hwid": str(getattr(port, "hwid", None) or ""),
            }
        )
    return sorted(found, key=lambda x: x["port"])


def describe_ppk2_port(port_name: str) -> dict[str, Any]:
    for item in discover_ppk2_devices():
        if item["port"].upper() == port_name.upper():
            return item
    # Keep the selected port in the log even if the unit was removed between UI
    # selection and start. The driver will then raise a useful serial error.
    return {
        "port": port_name,
        "device_id": f"PPK2-{port_name}",
        "serial": "",
        "vid": f"{PPK2_VID:04X}",
        "pid": f"{PPK2_PID:04X}",
        "interface": PPK2_MEASUREMENT_INTERFACE,
    }


class NordicPPK2Driver(PowerProfilerDriver):
    """PPK2 adapter with continuous background acquisition and tick reconstruction.

    ``PPK2_MP`` is used because it continuously drains the serial endpoint in a
    background thread. Current conversion is delegated to the ppk2-api calibration
    routine, while the 32-bit sample word is decoded here so the 6-bit sample counter
    can be retained. Counter discontinuities become gaps in ``sample_ticks``.
    """

    COUNTER_MASK = 0x3F
    COUNTER_SHIFT = 18

    def __init__(
        self,
        *,
        port: str,
        meter_mode: str = "source",
        voltage_mv: int = 3300,
        sample_rate_hz: int = 100_000,
        poll_s: float = 0.001,
        buffer_max_size_seconds: float = 5.0,
        buffer_chunk_seconds: float = 0.01,
    ) -> None:
        self.port = port
        self.meter_mode = meter_mode
        self.voltage_mv = voltage_mv
        self.sample_rate_hz = sample_rate_hz
        self.poll_s = poll_s
        self.buffer_max_size_seconds = buffer_max_size_seconds
        self.buffer_chunk_seconds = buffer_chunk_seconds
        self._api: Any = None
        self._running = False
        self._remainder = b""
        self._last_counter: int | None = None
        self._next_tick = 0
        self.detected_lost_samples = 0
        self._metadata: dict[str, Any] = {}

    def start(self) -> None:  # pragma: no cover - hardware path
        base_api, mp_api = _load_api()
        usb = describe_ppk2_port(self.port)
        self._api = mp_api(
            self.port,
            buffer_max_size_seconds=self.buffer_max_size_seconds,
            buffer_chunk_seconds=self.buffer_chunk_seconds,
            timeout=0.05,
        )

        # A previous process may have been terminated while streaming. Stop the raw
        # stream and clear residual bytes before requesting UTF-8 metadata.
        try:
            base_api.stop_measuring(self._api)
        except Exception:
            pass
        time.sleep(0.05)
        try:
            self._api.ser.reset_input_buffer()
        except Exception:
            pass
        time.sleep(0.05)
        self._api.get_modifiers()

        if self.meter_mode == "source":
            self._api.use_source_meter()
            self._api.set_source_voltage(self.voltage_mv)
            self._api.toggle_DUT_power("ON")
            dut_power = True
        elif self.meter_mode == "ampere":
            self._api.use_ampere_meter()
            self._api.current_vdd = self.voltage_mv
            dut_power = False
        else:
            raise ValueError(f"Unsupported meter_mode: {self.meter_mode}")

        try:
            api_version = importlib.metadata.version("ppk2-api")
        except importlib.metadata.PackageNotFoundError:
            api_version = "unknown"

        modifiers = dict(self._api.modifiers)
        self._metadata = {
            **usb,
            "device_id": usb.get("device_id") or f"PPK2-{self.port}",
            "api": "ppk2-api",
            "api_version": api_version,
            "hw": modifiers.get("HW"),
            "ia": modifiers.get("IA"),
            "calibrated": modifiers.get("Calibrated"),
            "calibration_modifiers": modifiers,
            "meter_mode": self.meter_mode,
            "source_voltage_mv": self.voltage_mv,
            "dut_power_enabled": dut_power,
            "sample_rate_hz": self.sample_rate_hz,
            "sample_period_us": 1_000_000.0 / self.sample_rate_hz,
            "buffer_max_size_seconds": self.buffer_max_size_seconds,
            "buffer_chunk_seconds": self.buffer_chunk_seconds,
        }

        self._remainder = b""
        self._last_counter = None
        self._next_tick = 0
        self.detected_lost_samples = 0
        self._api.start_measuring()
        self._running = True

    def metadata(self) -> dict[str, Any]:
        return dict(self._metadata)

    def _decode_words(self, raw: bytes) -> SampleBatch | None:  # pragma: no cover - hardware path
        assert self._api is not None
        payload = self._remainder + bytes(raw)
        complete = len(payload) - (len(payload) % 4)
        self._remainder = payload[complete:]
        if complete == 0:
            return None

        currents: list[float] = []
        digital: list[int] = []
        ticks: list[int] = []
        lost_in_batch = 0

        for offset in range(0, complete, 4):
            word = int.from_bytes(payload[offset : offset + 4], "little", signed=False)
            counter = (word >> self.COUNTER_SHIFT) & self.COUNTER_MASK

            if self._last_counter is not None:
                expected = (self._last_counter + 1) & self.COUNTER_MASK
                gap = (counter - expected) & self.COUNTER_MASK
                if gap:
                    self._next_tick += gap
                    lost_in_batch += gap
                    self.detected_lost_samples += gap

            try:
                result = self._api._handle_raw_data(word)  # noqa: SLF001 - compatibility adapter
            except Exception:
                self._last_counter = counter
                self._next_tick += 1
                lost_in_batch += 1
                self.detected_lost_samples += 1
                continue

            self._last_counter = counter
            if result is None:
                self._next_tick += 1
                lost_in_batch += 1
                self.detected_lost_samples += 1
                continue

            if isinstance(result, (tuple, list)):
                current_ua = result[0]
                logic = result[1] if len(result) > 1 else (word >> 24) & 0xFF
            else:
                current_ua = result
                logic = (word >> 24) & 0xFF
            if current_ua is None:
                self._next_tick += 1
                lost_in_batch += 1
                self.detected_lost_samples += 1
                continue

            currents.append(float(current_ua))
            digital.append(int(logic or 0) & 0xFF)
            ticks.append(self._next_tick)
            self._next_tick += 1

        if not currents:
            return None
        return SampleBatch(
            np.asarray(currents, dtype=np.float32),
            np.asarray(digital, dtype=np.uint8),
            sample_ticks=np.asarray(ticks, dtype=np.int64),
            detected_lost_samples=lost_in_batch,
        )

    def read_batch(self) -> SampleBatch | None:  # pragma: no cover - hardware path
        if not self._running or self._api is None:
            return None
        fetcher = getattr(self._api, "_fetcher", None)
        if fetcher is None or not fetcher.is_alive():
            raise RuntimeError("PPK2 acquisition reader stopped unexpectedly")
        raw = self._api.get_data()
        if not raw:
            time.sleep(self.poll_s)
            return None
        return self._decode_words(raw)

    def stop(self) -> SampleBatch | None:  # pragma: no cover - hardware path
        api = self._api
        if api is None:
            return
        tail = None
        try:
            if self._running:
                # PPK2_MP.stop_measuring discards its queued data. Retrieve the
                # received queue first so its charge is not lost on manual stop.
                try:
                    raw = api.get_data()
                finally:
                    api.stop_measuring()
                if raw:
                    tail = self._decode_words(raw)
        finally:
            try:
                if self.meter_mode == "source":
                    api.toggle_DUT_power("OFF")
            except Exception:
                pass
            # Do not close pyserial manually here. PPK2_MP has a destructor that sends
            # another stop command; closing the port first causes PortNotOpenError.
            # Releasing the object after its fetcher has been stopped lets pyserial
            # close cleanly when it is collected.
            self._running = False
            self._remainder = b""
            self._api = None
        return tail
