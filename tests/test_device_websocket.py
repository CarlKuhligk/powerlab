from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app


def test_device_socket_sends_initial_inventory_and_only_changed_snapshots(tmp_path, monkeypatch):
    settings = Settings(data_dir=tmp_path, database_url=f"sqlite:///{tmp_path / 'devices.db'}")
    first = {"port": "COM4", "device_id": "KIT-A", "busy": False}
    busy = {**first, "busy": True}
    reconnected = {**first, "port": "COM9"}
    snapshots = iter([[], [], [first], [first], [busy], [], [reconnected]])
    with TestClient(create_app(settings)) as client:
        manager = client.app.state.manager
        monkeypatch.setattr(manager, "devices_status", lambda: next(snapshots, [reconnected]))
        with client.websocket_connect("/ws/devices") as socket:
            assert socket.receive_json() == {"ppk2": []}
            assert socket.receive_json() == {"ppk2": [first]}
            assert socket.receive_json() == {"ppk2": [busy]}
            assert socket.receive_json() == {"ppk2": []}
            assert socket.receive_json() == {"ppk2": [reconnected]}
        # A new connection always receives the full current inventory.
        with client.websocket_connect("/ws/devices") as socket:
            assert socket.receive_json() == {"ppk2": [reconnected]}
        assert client.get("/api/devices").json() == {"ppk2": [reconnected]}


def test_device_socket_recovers_from_a_scan_error_without_disconnect(tmp_path, monkeypatch):
    settings = Settings(data_dir=tmp_path, database_url=f"sqlite:///{tmp_path / 'devices.db'}")
    calls = 0

    def scan():
        nonlocal calls
        calls += 1
        if calls == 1:
            raise OSError("USB enumeration temporarily unavailable")
        return [{"port": "COM4", "device_id": "KIT-A"}]

    with TestClient(create_app(settings)) as client:
        monkeypatch.setattr(client.app.state.manager, "devices_status", scan)
        with client.websocket_connect("/ws/devices") as socket:
            assert socket.receive_json() == {"ppk2": [{"port": "COM4", "device_id": "KIT-A"}]}
        assert calls >= 2
