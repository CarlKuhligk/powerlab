import numpy as np

from app.ppk.nordic import NordicPPK2Driver


class FakePPKApi:
    def _handle_raw_data(self, word: int):
        # Decoder contract test only: return a deterministic current and the logic bits.
        return float(word & 0x3FFF), (word >> 24) & 0xFF


def make_word(counter: int, adc: int, digital: int = 0) -> int:
    return (adc & 0x3FFF) | ((counter & 0x3F) << 18) | ((digital & 0xFF) << 24)


def test_ppk_counter_gap_preserves_device_timeline():
    d = NordicPPK2Driver(port="COM42")
    d._api = FakePPKApi()
    words = [
        make_word(10, 1, 0x01),
        make_word(11, 2, 0x01),
        # counters 12 and 13 are missing
        make_word(14, 3, 0x02),
    ]
    raw = b"".join(w.to_bytes(4, "little") for w in words)
    batch = d._decode_words(raw)
    assert batch is not None
    np.testing.assert_array_equal(batch.sample_ticks, [0, 1, 4])
    np.testing.assert_array_equal(batch.digital, [1, 1, 2])
    assert batch.detected_lost_samples == 2
    assert d.detected_lost_samples == 2


def test_ppk_windows_interface_and_device_id_helpers():
    from types import SimpleNamespace
    from app.ppk.nordic import _interface_from_port, _stable_device_id

    port = SimpleNamespace(
        device="COM4",
        hwid=r"USB\VID_1915&PID_C00A&MI_01\6&100941F0&0&0001",
        location="1-5.1",
        serial_number="ABCDEF123456",
    )
    assert _interface_from_port(port) == "01"
    assert _stable_device_id(port, "01") == "ABCDEF123456"


def test_discovery_rescans_connected_usb_devices_each_time(monkeypatch):
    from types import SimpleNamespace
    import serial.tools.list_ports
    from app.ppk.nordic import discover_ppk2_devices

    connected = []
    monkeypatch.setattr(serial.tools.list_ports, "comports", lambda: connected)
    assert discover_ppk2_devices() == []
    kit = SimpleNamespace(device="COM4", vid=0x1915, pid=0xC00A,
                          hwid=r"USB\VID_1915&PID_C00A&MI_01", serial_number="KIT-A")
    connected.append(kit)
    assert discover_ppk2_devices()[0]["device_id"] == "KIT-A"
    connected.clear()
    assert discover_ppk2_devices() == []
    kit.device = "COM9"
    connected.append(kit)
    assert discover_ppk2_devices()[0]["port"] == "COM9"


def test_stop_preserves_queued_samples_before_library_discards_buffer():
    class Api(FakePPKApi):
        stopped = False
        def get_data(self):
            assert not self.stopped
            return b"".join(make_word(counter, adc).to_bytes(4, "little") for counter, adc in [(12, 20), (15, 30)])
        def stop_measuring(self):
            self.stopped = True
    driver = NordicPPK2Driver(port="COM42", meter_mode="ampere")
    api = Api()
    driver._api = api
    driver._running = True
    driver._decode_words(b"".join(make_word(counter, 4).to_bytes(4, "little") for counter in [10, 11]))
    final = driver.stop()
    assert api.stopped
    assert driver._api is None
    assert not driver._running
    np.testing.assert_array_equal(final.currents_ua, [20, 30])
    np.testing.assert_array_equal(final.sample_ticks, [2, 5])
    assert final.detected_lost_samples == 2
