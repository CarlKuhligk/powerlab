import json

import numpy as np
import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.db import Measurement, WakeEvent
from app.main import create_app
from app.storage import event_reader
from app.storage.raw_store import RawStore


def make_event(store):
    writer = store.stream_event("large", 1)
    writer.block_samples = 100
    for start in range(0, 3000, 100):
        idx = np.arange(start, start + 100, dtype=np.int64)
        current = np.full(100, 4.0, dtype=np.float32)
        current[27] = -20 if start == 700 else 50
        current[43] = 99_000 if start == 2700 else 100
        writer.append(idx, current, np.ones(100, dtype=np.uint8))
    return writer.finish()


def forbid_decode(*args, **kwargs):
    pytest.fail("The oversized event must not decode any sample pages")


def test_large_preview_uses_footer_only_and_keeps_global_extrema(tmp_path, monkeypatch):
    store = RawStore(tmp_path)
    path = make_event(store)
    monkeypatch.setattr(event_reader, "MAX_RAW_SAMPLES", 1000)
    monkeypatch.setattr(event_reader.pq, "read_table", forbid_decode)
    monkeypatch.setattr(event_reader.pq.ParquetFile, "iter_batches", forbid_decode)
    with pytest.raises(event_reader.RawDataLimitError, match="Event zu groß"):
        store.read_event(path)
    raw, aggregated = store.event_preview(path, max_points=16)
    assert aggregated
    assert len(raw.sample_index) <= 16
    assert raw.sample_index[0] == 0
    assert raw.sample_index[-1] == 2999
    assert raw.current_ua.min() == -20
    assert raw.current_ua.max() == 99_000
    assert np.all(np.diff(raw.sample_index) >= 0)


def test_multibillion_sample_metadata_stays_within_preview_point_budget(tmp_path, monkeypatch):
    from types import SimpleNamespace
    store = RawStore(tmp_path)
    path = make_event(store)

    class Metadata:
        num_rows = 6_200_000_000
        num_row_groups = 62_000

        def row_group(self, number):
            stats = [SimpleNamespace(has_min_max=True, min=number * 100_000, max=(number + 1) * 100_000 - 1),
                     SimpleNamespace(has_min_max=True, min=-20.0 if number == 700 else 4.0,
                                     max=99_000.0 if number == 61_000 else 100.0)]
            return SimpleNamespace(column=lambda n: SimpleNamespace(statistics=stats[n]))

    monkeypatch.setattr(event_reader.pq, "ParquetFile", lambda *a, **k: SimpleNamespace(metadata=Metadata(), iter_batches=forbid_decode))
    raw, aggregated = store.event_preview(path, max_points=50_000)
    assert aggregated
    assert len(raw.sample_index) <= 50_000
    assert raw.sample_index[0] == 0
    assert raw.sample_index[-1] == 6_199_999_999
    assert raw.current_ua.min() == -20
    assert raw.current_ua.max() == 99_000
    assert sum(array.nbytes for array in (raw.sample_index, raw.current_ua, raw.digital)) <= 650_000


def test_window_reads_only_overlapping_groups_and_returns_exact_samples(tmp_path, monkeypatch):
    store = RawStore(tmp_path)
    path = make_event(store)
    monkeypatch.setattr(event_reader, "MAX_RAW_SAMPLES", 1000)
    original = event_reader.pq.ParquetFile.iter_batches
    groups = []

    def counted(self, **kwargs):
        groups.extend(kwargs["row_groups"])
        assert kwargs["batch_size"] <= event_reader.BATCH_SAMPLES
        return original(self, **kwargs)

    monkeypatch.setattr(event_reader.pq.ParquetFile, "iter_batches", counted)
    raw = store.read_event(path, start_sample=2730, end_sample=2750)
    assert groups == [27]
    np.testing.assert_array_equal(raw.sample_index, np.arange(2730, 2751))
    assert raw.current_ua[13] == 99_000
    assert raw.digital.tolist() == [1] * 21
    with pytest.raises(event_reader.RawDataLimitError, match="Zeitfenster zu groß"):
        store.read_event(path, start_sample=0, end_sample=2000)
    with pytest.raises(event_reader.RawDataLimitError):
        store.read_event(path, start_sample=1)
    with pytest.raises(event_reader.RawDataLimitError):
        store.read_event(path, start_sample=50, end_sample=20)


def test_oversized_npz_and_manifest_are_rejected_before_numpy_loading(tmp_path, monkeypatch):
    store = RawStore(tmp_path)
    path = tmp_path / "one.npz"
    np.savez_compressed(path, sample_index=np.arange(1200, dtype=np.int64),
                        current_ua=np.zeros(1200, np.float32), digital=np.zeros(1200, np.uint8))
    manifest = tmp_path / "event.json"
    manifest.write_text(json.dumps({"parts": ["one.npz", "one.npz"]}))
    monkeypatch.setattr(event_reader, "MAX_RAW_SAMPLES", 2000)
    monkeypatch.setattr(np, "load", forbid_decode)
    with pytest.raises(event_reader.RawDataLimitError, match="Event zu groß"):
        store.read_event("event.json")
    monkeypatch.setattr(event_reader, "MAX_NPZ_BYTES", 100)
    with pytest.raises(event_reader.RawDataLimitError, match="NPZ-Rohdatenblock"):
        store.read_event("one.npz")


def test_oversized_footer_is_rejected_before_arrow_opens_file(tmp_path, monkeypatch):
    path = tmp_path / "bad.parquet"
    import struct
    path.write_bytes(b"PAR1" + struct.pack("<I", event_reader.MAX_FOOTER_BYTES + 1) + b"PAR1")
    monkeypatch.setattr(event_reader.pq, "ParquetFile", forbid_decode)
    with pytest.raises(event_reader.RawDataLimitError, match="Metadaten"):
        RawStore(tmp_path).read_event("bad.parquet")


def test_concurrent_raw_read_is_rejected_and_slot_is_released(tmp_path):
    store = RawStore(tmp_path)
    with store._reading():
        with pytest.raises(event_reader.RawDataLimitError, match="läuft bereits"):
            store.event_preview("unused.parquet")
    with pytest.raises(FileNotFoundError):
        store.read_event("unused.parquet")
    with store._reading():
        pass


def test_event_api_returns_safe_preview_and_rejects_large_csv_and_windows(tmp_path, monkeypatch):
    settings = Settings(data_dir=tmp_path, database_url=f"sqlite:///{tmp_path / 'test.db'}")
    with TestClient(create_app(settings)) as client:
        manager = client.app.state.manager
        path = make_event(manager.raw_store)
        with manager.db.session() as session:
            session.add(Measurement(id="large", name="large", status="completed", sample_rate_hz=100_000, total_samples=3000))
            session.flush()
            session.add(WakeEvent(measurement_id="large", sequence=1, start_sample=0, trigger_sample=100,
                                  end_sample=2999, duration_us=29000, peak_ua=99_000, mean_ua=4,
                                  charge_uc=0, raw_file=path, digital_mask_seen=1))
            session.flush()
        monkeypatch.setattr(event_reader, "MAX_RAW_SAMPLES", 1000)
        response = client.get("/api/measurements/large/events/1?max_points=100")
        assert response.status_code == 200
        assert response.json()["aggregated"]
        assert response.json()["display_notice"]
        assert len(response.json()["sample_index"]) <= 100
        raw_response = client.get('/api/measurements/large/events/1?start_s=.0263&end_s=.0265&max_points=100')
        assert raw_response.status_code == 200
        raw_payload = raw_response.json()
        assert not raw_payload['aggregated'] and not raw_payload['downsampled']
        assert raw_payload['detail'] == 'raw'
        assert raw_payload['sample_index'] == list(range(2730, 2751))
        decimated = client.get('/api/measurements/large/events/1?start_s=-.001&end_s=.006&max_points=100').json()
        assert decimated['downsampled'] and not decimated['aggregated']
        assert decimated['detail'] == 'sample-extrema' and decimated['display_notice']
        assert client.get("/api/measurements/large/events/1/csv").status_code == 413
        assert client.get("/api/measurements/large/events/1?start_s=0&end_s=10").status_code == 413
        raw = client.get("/api/measurements/large/events/1?start_s=0.0263&end_s=0.0265").json()
        assert not raw["aggregated"]
        assert raw["sample_index"] == list(range(2730, 2751))
        assert max(raw["current_ua"]) == 99_000


def test_raw_only_api_preserves_every_sample_and_never_falls_back(tmp_path):
    settings = Settings(data_dir=tmp_path, database_url=f"sqlite:///{tmp_path / 'exact.db'}")
    with TestClient(create_app(settings)) as client:
        manager = client.app.state.manager
        path = make_event(manager.raw_store)
        with manager.db.session() as session:
            session.add(Measurement(id="large", name="raw", status="completed", sample_rate_hz=1000, total_samples=3000))
            session.flush()
            session.add(WakeEvent(measurement_id="large", sequence=1, start_sample=0, trigger_sample=100,
                                  end_sample=2999, duration_us=2900000, peak_ua=99000, mean_ua=4,
                                  charge_uc=0, raw_file=path, digital_mask_seen=1))
        url='/api/measurements/large/events/1?raw_only=true&max_points=100'
        data=client.get(url+'&start_s=2.6&end_s=2.699').json()
        assert data['detail']=='raw' and not data['downsampled'] and not data['aggregated']
        assert data['sample_index']==list(range(2700,2800))
        assert data['current_ua'][43]==99000
        assert data['digital']==[1]*100
        assert client.get(url).status_code==413
        assert client.get(url+'&start_s=0&end_s=2').status_code==413
        # Reject oversized requests instead of substituting a lossy preview.
        assert client.get('/api/measurements/large/cycle-energy').status_code==200
        assert client.get('/api/measurements/large/cycle-energy').json()['cycle_count']==0
        assert client.get('/api/measurements/missing/cycle-energy').status_code==404


def test_paged_raw_api_retains_all_samples_without_boundary_rounding_loss(tmp_path):
    settings=Settings(data_dir=tmp_path,database_url=f"sqlite:///{tmp_path / 'pages.db'}")
    with TestClient(create_app(settings)) as client:
        manager=client.app.state.manager
        path=make_event(manager.raw_store)
        with manager.db.session() as session:
            session.add(Measurement(id="large",name="paged raw",status="completed",sample_rate_hz=100000,total_samples=3000))
            session.flush()
            session.add(WakeEvent(measurement_id="large",sequence=1,start_sample=0,trigger_sample=100,
                                  end_sample=2899,duration_us=28000,peak_ua=99000,mean_ua=4,
                                  charge_uc=0,raw_file=path,digital_mask_seen=1))
        indices,values,digital=[],[],[]
        for start in range(0,3000,100):
            response=client.get('/api/measurements/large/events/1',params={
                'raw_only':'true','max_points':100,'start_s':(start-100)/100000,
                'end_s':(start+99-100)/100000})
            assert response.status_code==200
            page=response.json()
            assert page['detail']=='raw' and len(page['sample_index'])==100
            indices.extend(page['sample_index']);values.extend(page['current_ua']);digital.extend(page['digital'])
        assert indices==list(range(3000))
        assert values[727]==-20 and values[2743]==99000
        assert digital==[1]*3000
        expected=manager.raw_store.read_event(path)
        assert values==expected.current_ua.tolist()


def test_adaptive_view_streams_full_event_and_preserves_exact_peak_times(tmp_path,monkeypatch):
    store=RawStore(tmp_path)
    path=make_event(store)
    monkeypatch.setattr(event_reader,'MAX_RAW_SAMPLES',1000)
    original=event_reader.pq.ParquetFile.iter_batches
    def bounded(self,**kwargs):
        assert kwargs['batch_size']<=event_reader.BATCH_SAMPLES
        return original(self,**kwargs)
    monkeypatch.setattr(event_reader.pq.ParquetFile,'iter_batches',bounded)
    monkeypatch.setattr(event_reader,'read_window',forbid_decode)
    raw,count=store.event_view(path,0,2999,100)
    assert count==3000 and len(raw.sample_index)<=100
    assert raw.sample_index[0]==0 and raw.sample_index[-1]==2999
    assert raw.sample_index[np.argmin(raw.current_ua)]==727
    assert raw.sample_index[np.argmax(raw.current_ua)]==2743
    assert raw.digital.tolist()==[1]*len(raw.sample_index)
    assert np.all(np.diff(raw.sample_index)>0)


def test_adaptive_view_api_refines_zoom_to_every_raw_sample(tmp_path,monkeypatch):
    settings=Settings(data_dir=tmp_path,database_url=f"sqlite:///{tmp_path / 'views.db'}")
    with TestClient(create_app(settings)) as client:
        manager=client.app.state.manager
        path=make_event(manager.raw_store)
        with manager.db.session() as session:
            session.add(Measurement(id='large',name='adaptive view',status='completed',sample_rate_hz=100000,total_samples=3000))
            session.flush()
            session.add(WakeEvent(measurement_id='large',sequence=1,start_sample=0,trigger_sample=100,
                                  end_sample=2899,duration_us=28000,peak_ua=99000,mean_ua=4,charge_uc=0,
                                  raw_file=path,digital_mask_seen=1))
        monkeypatch.setattr(event_reader,'MAX_RAW_SAMPLES',1000)
        url='/api/measurements/large/events/1'
        overview=client.get(url,params={'adaptive_view':'true','max_points':100,'start_s':-.001,'end_s':.02899})
        assert overview.status_code==200
        data=overview.json()
        assert data['detail']=='sample-extrema' and data['downsampled'] and not data['aggregated']
        assert data['decoded_samples']==3000 and len(data['sample_index'])<=100
        assert data['sample_index'][0]==0 and data['sample_index'][-1]==2999
        assert data['sample_index'][data['current_ua'].index(99000)]==2743
        raw=client.get(url,params={'adaptive_view':'true','max_points':100,'start_s':.0263,'end_s':.0265}).json()
        assert raw['detail']=='raw' and not raw['downsampled']
        assert raw['sample_index']==list(range(2730,2751))
        assert raw['current_ua'][13]==99000
        assert client.get(url,params={'adaptive_view':'true','start_s':1,'end_s':0}).status_code==413
        assert client.get(url,params={'adaptive_view':'true'}).status_code==413
        assert client.get(url,params={'adaptive_view':'true','raw_only':'true','start_s':0,'end_s':1}).status_code==413


def test_million_sample_event_view_stays_small_and_zoom_keeps_short_peak(tmp_path):
    import time
    store=RawStore(tmp_path)
    writer=store.stream_event('million',1)
    peak=1247351
    for start in range(0,2500000,100000):
        idx=np.arange(start,start+100000,dtype=np.int64)
        current=(5+.1*np.sin(idx*.1)).astype(np.float32)
        current[idx==peak]=35000
        writer.append(idx,current,np.ones(len(idx),np.uint8))
    path=writer.finish()
    begin=time.monotonic()
    overview,count=store.event_view(path,0,2499999,25000)
    elapsed=time.monotonic()-begin
    assert count==2500000 and len(overview.sample_index)<=25000
    assert overview.sample_index[0]==0 and overview.sample_index[-1]==2499999
    assert overview.sample_index[np.argmax(overview.current_ua)]==peak
    assert sum(a.nbytes for a in (overview.sample_index,overview.current_ua,overview.digital))<=325000
    raw,count=store.event_view(path,peak-100,peak+100,25000)
    assert count==201 and raw.sample_index.tolist()==list(range(peak-100,peak+101))
    assert raw.current_ua[100]==35000
    print(json.dumps({'samples':2500000,'view_points':len(overview.sample_index),'view_seconds':round(elapsed,3)}))


def test_view_keeps_all_actual_samples_when_wide_requested_range_is_clipped(tmp_path):
    store=RawStore(tmp_path)
    path=make_event(store)
    raw,count=store.event_view(path,-100000,100000,5000)
    assert count==3000
    assert raw.sample_index.tolist()==list(range(3000))
    assert raw.current_ua[2743]==99000
