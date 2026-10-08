const state = {
  view: 'live', navigationToken: 0, sessionReconnectTimer: null, sessionRefreshPending: false,
  measurements: [], devices: {ppk2: []}, liveOverview: {running:[], scheduled:[]},
  devicesWs: null, devicesReconnectTimer: null, preferredDevice: null,
  selectedMeasurementIds: new Set(), measurementBulkBusy: false,
  currentMeasurement: null, currentOverview: null, currentEvent: null,
  sessionMeasurement: null, sessionWs: null, overviewWs: null,
  historyScale: 'log', liveScale: 'log', liveFollow: true, lastLiveRevision: null,
  historySmoothing: 'off',
  lastLiveSnapshot: null, liveFullRefreshAt: 0, liveFullUserZoomed: false, liveFullRange: null,
  liveSeriesRequestToken: 0, liveZoomTimer: null, liveIgnoreRelayoutUntil: 0, liveLastSeries: null,
  liveSeriesInFlight: null, livePlotPending: 0,
  liveStreamSeries: null, liveStreamRenderPending: false, liveStreamRenderActive: false,
  liveRenderChain: Promise.resolve(),
  eventVisibleRange: null, eventPlotPending: 0, eventZoomTimer:null,
  sessionStopPendingId: null,
  editScheduledId: null, historyMode: 'overview', historyRenderToken: 0, historyPlotPromise: Promise.resolve(),
};
const $ = id => document.getElementById(id);
const plotConfig = {responsive:true,displaylogo:false,scrollZoom:true,modeBarButtonsToRemove:['sendDataToCloud']};

async function api(url, options={}) {
  const res = await fetch(url,{headers:{'Content-Type':'application/json',...(options.headers||{})},...options});
  if(!res.ok){let detail=`${res.status} ${res.statusText}`;try{detail=(await res.json()).detail||detail}catch(_){}throw new Error(detail)}
  if(res.status===204)return null; return res.json();
}
function toast(message,error=false){const el=$('toast');el.textContent=message;el.className=`toast show${error?' error':''}`;clearTimeout(el._timer);el._timer=setTimeout(()=>el.className='toast',3000)}
function escapeHtml(v){return String(v??'').replace(/[&<>'"]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;',"'":'&#39;','"':'&quot;'}[c]))}
function fmtCurrent(uA){if(uA==null||!Number.isFinite(Number(uA)))return'—';const v=Number(uA),a=Math.abs(v);if(a>=1e6)return`${(v/1e6).toFixed(3)} A`;if(a>=1000)return`${(v/1000).toFixed(a>=100000?1:3)} mA`;if(a<1)return`${(v*1000).toFixed(1)} nA`;return`${v.toFixed(a<10?3:2)} µA`}
function currentUnit(values){
  let peak=0;for(const value of values)if(value!=null&&Number.isFinite(Number(value)))peak=Math.max(peak,Math.abs(Number(value)));
  return peak>=1e6?{label:'A',factor:1e-6}:peak>=1000?{label:'mA',factor:.001}:peak>0&&peak<1?{label:'nA',factor:1000}:{label:'µA',factor:1};
}
function eventCurrentUnit(d,range=state.eventVisibleRange){
  if(!range)return currentUnit(d.current_ua||[]);
  return currentUnit((d.current_ua||[]).filter((_,i)=>d.t_us[i]>=range[0]&&d.t_us[i]<=range[1]));
}
function hideCurrentTooltip(){$('currentChartTooltip')?.classList.add('hidden')}
function positionCurrentTooltip(pointer){
  const tooltip=$('currentChartTooltip');if(!tooltip||!pointer||!Number.isFinite(pointer.clientX)||!Number.isFinite(pointer.clientY))return;
  const rect=tooltip.getBoundingClientRect();
  const width=globalThis.innerWidth||1200,height=globalThis.innerHeight||800;
  let left=pointer.clientX+16,top=pointer.clientY+16;
  if(left+rect.width>width-8)left=pointer.clientX-rect.width-16;
  if(top+rect.height>height-8)top=pointer.clientY-rect.height-16;
  tooltip.style.left=`${Math.max(8,left)}px`;tooltip.style.top=`${Math.max(8,top)}px`;
}
function bindEventChartInteractions(d,unit){
  const el=$('wakeEventChart'),tooltip=$('currentChartTooltip');
  el.removeAllListeners?.('plotly_hover');el.removeAllListeners?.('plotly_unhover');el.removeAllListeners?.('plotly_relayout');
  if(el.dataset.pointerBound!=='1'){
    el.dataset.pointerBound='1';
    el.addEventListener('pointermove',ev=>{el._currentPointer=ev;if(!tooltip.classList.contains('hidden'))positionCurrentTooltip(ev)});
    el.addEventListener('pointerleave',hideCurrentTooltip);el.addEventListener('pointerdown',hideCurrentTooltip);
  }
  el.on('plotly_hover',ev=>{
    if(state.view!=='detail'||state.currentEvent!==d)return;
    const point=ev.points?.[0];if(!point){hideCurrentTooltip();return;}
    const index=point.pointNumber??point.pointIndex;
    let details;
    if(point.curveNumber===0){
      const raw=d.current_ua[index],display=eventDisplayCurrent(d)[index];
      details=d.aggregated?`Block-Extremwert: <strong>${escapeHtml(fmtCurrent(raw))}</strong><br>Zeitposition angenähert`:d.downsampled?`Rohsample aus Min/Max-Auswahl: <strong>${escapeHtml(fmtCurrent(raw))}</strong>`:(state.historySmoothing==='off'?`Strom: <strong>${escapeHtml(fmtCurrent(raw))}</strong>`:`Geglättet: <strong>${escapeHtml(fmtCurrent(display))}</strong><br>Rohwert: ${escapeHtml(fmtCurrent(raw))}`);
      // Plotly can pick the current trace when a state marker shares its sample.
      // Keep both the state and the sample value visible in that case.
      const markerTrace=(el.data||[]).find(trace=>trace.meta?.role==='state-marker'&&trace.x.some((x,i)=>Number(x)===Number(point.x)&&Number(trace.y[i])===Number(point.y)));
      if(markerTrace)details=`${escapeHtml(markerTrace.customdata[markerTrace.x.findIndex(x=>Number(x)===Number(point.x))])}<br>${details}`;
    }else if(point.data?.meta?.role==='state-marker'){
      details=escapeHtml(point.customdata||point.text||'Zustandswechsel');
    }else{
      const bit=Number(String(point.data?.name||'').slice(1));
      details=`${escapeHtml(point.data?.name)}: <strong>${Math.round(point.y-bit*1.2)}</strong>`;
    }
    tooltip.innerHTML=`Zeit: ${escapeHtml(fmtEventDuration(point.x))}<br>${details}`;
    tooltip.classList.remove('hidden');positionCurrentTooltip(ev.event||el._currentPointer);
  });
  el.on('plotly_unhover',hideCurrentTooltip);
  el.on('plotly_relayout',ev=>{
    hideCurrentTooltip();if(state.eventPlotPending||state.view!=='detail'||state.currentEvent!==d)return;
    const range=ev['xaxis.range']??(ev['xaxis.range[0]']!==undefined?[ev['xaxis.range[0]'],ev['xaxis.range[1]']]:null);
    if(range&&range.length===2&&range.every(v=>Number.isFinite(Number(v))))state.eventVisibleRange=range.map(Number);
    else if(ev['xaxis.autorange']===true)state.eventVisibleRange=null;
    else return;
    clearTimeout(state.eventZoomTimer);
    state.eventZoomTimer=setTimeout(()=>reloadEventRange(d,state.eventVisibleRange).catch(showEventRenderError),200);
  });
}
function showEventRenderError(err){if(err.name==='AbortError')return;console.error('Wake event chart render failed',err);toast('Wake-Event-Chart konnte nicht gerendert werden.',true)}
function fmtCharge(uC){if(uC==null)return'—';const v=Number(uC);if(Math.abs(v)>=3.6e6)return`${(v/3.6e6).toFixed(4)} mAh`;if(Math.abs(v)>=1000)return`${(v/1000).toFixed(3)} mC`;return`${v.toFixed(2)} µC`}
function fmtEnergy(uWh){if(uWh==null)return'—';const v=Number(uWh);return Math.abs(v)>=1000?`${(v/1000).toFixed(4)} mWh`:`${v.toFixed(3)} µWh`}
function fmtPercent(v,digits=3){if(v==null||!Number.isFinite(Number(v)))return'—';return`${Number(v).toFixed(digits)} %`}
function fmtDuration(seconds){if(seconds==null)return'—';const s=Math.max(0,Number(seconds));if(s<1)return`${(s*1000).toFixed(0)} ms`;const h=Math.floor(s/3600),m=Math.floor((s%3600)/60),sec=Math.floor(s%60);if(h)return`${h}h ${String(m).padStart(2,'0')}m ${String(sec).padStart(2,'0')}s`;if(m)return`${m}m ${String(sec).padStart(2,'0')}s`;return`${s.toFixed(1)} s`}
function fmtEventDuration(us){if(us==null)return'—';const v=Number(us);if(v>=1e6)return`${(v/1e6).toFixed(3)} s`;if(v>=1000)return`${(v/1000).toFixed(3)} ms`;return`${v.toFixed(1)} µs`}
function fmtDate(iso){if(!iso)return'—';return new Date(iso).toLocaleString('de-DE',{dateStyle:'short',timeStyle:'medium'})}
function localInput(isoOrDate){const d=isoOrDate instanceof Date?isoOrDate:new Date(isoOrDate);const pad=n=>String(n).padStart(2,'0');return`${d.getFullYear()}-${pad(d.getMonth()+1)}-${pad(d.getDate())}T${pad(d.getHours())}:${pad(d.getMinutes())}:${pad(d.getSeconds())}`}
function asIsoLocalInput(value){return value?new Date(value).toISOString():null}

function setView(name){
  hideCurrentTooltip();closeExportMenu();
  if(name!=='detail')cancelEventLoad();
  state.view=name;++state.navigationToken;++state.historyRenderToken;
  if(name!=='session'){closeSessionWs();state.sessionMeasurement=null;}
  document.querySelectorAll('.view').forEach(v=>v.classList.remove('active'));document.querySelectorAll('.nav').forEach(v=>{v.classList.remove('active');v.ariaCurrent=null});
  const map={live:'liveView',session:'sessionView',measurements:'measurementsView',detail:'detailView',battery:'batteryView'};$(map[name]).classList.add('active');
  const nav=document.querySelector(`.nav[data-view="${name==='session'?'live':name==='detail'?'measurements':name}"]`);if(nav){nav.classList.add('active');nav.ariaCurrent='page';}
  const titles={live:['Aktiv','Laufende und geplante PPK2-Messungen'],session:['Aktiv · Messung','Setup und Live-Daten der ausgewählten Session'],measurements:['Messungen','Historie durchsuchen, analysieren und exportieren'],detail:['Messprotokoll','Kennzahlen, Zustandsverlauf und Ereignisse'],battery:['Batterielaufzeit','Batterieenergie und Geräteeinstellung mit gemessener Streuung vergleichen']};
  [$('pageTitle').textContent,$('pageSubtitle').textContent]=titles[name];
}

function renderDevices(devices){
    const select=$('portSelect');
    const selected=(state.devices.ppk2||[]).find(p=>p.port===select.value);
    if(selected)state.preferredDevice={port:selected.port,id:selected.device_id||selected.serial||null};
    const ports=devices.ppk2||[],preferred=state.preferredDevice;
    const match=preferred?ports.find(p=>preferred.id?(p.device_id||p.serial)===preferred.id:p.port===preferred.port):ports.find(p=>!p.busy)||ports[0];
    const missing=preferred&&!match;
    const placeholder=!ports.length?'<option value="">Kein PPK2 gefunden</option>':missing?'<option value="">Ausgewähltes PPK2 nicht angeschlossen</option>':'';
    const options=placeholder+ports.map(p=>`<option value="${escapeHtml(p.port)}">${escapeHtml(p.port)} · ${escapeHtml(p.device_id||p.serial||'PPK2')}${p.busy?' · AKTIV':''}</option>`).join('');
    if(select.innerHTML!==options)select.innerHTML=options;
    select.value=match?.port||'';
    if(match)state.preferredDevice={port:match.port,id:match.device_id||match.serial||null};
    state.devices=devices;$('deviceCount').textContent=ports.length;
}
function startDeviceDiscovery(){
  if(state.devicesWs)return;
  clearTimeout(state.devicesReconnectTimer);state.devicesReconnectTimer=null;
  const proto=location.protocol==='https:'?'wss:':'ws:';
  const ws=new WebSocket(`${proto}//${location.host}/ws/devices`);state.devicesWs=ws;
  ws.onmessage=e=>{if(state.devicesWs===ws)renderDevices(JSON.parse(e.data))};
  ws.onerror=()=>{if(state.devicesWs===ws)ws.close()};
  ws.onclose=()=>{
    if(state.devicesWs!==ws)return;
    state.devicesWs=null;
    state.devicesReconnectTimer=setTimeout(startDeviceDiscovery,1500);
  };
}

function renderSessionCards(containerId,items,scheduled=false,now=Date.now()){
  const container=$(containerId),existing=new Map(Array.from(container.children,card=>[card.dataset.session,card]));
  const ids=new Set(items.map(item=>String(scheduled?item.id:item.measurement_id)));
  for(const [id,card] of existing)if(!ids.has(id))card.remove();
  items.forEach((item,index)=>{
    const id=String(scheduled?item.id:item.measurement_id);
    let card=existing.get(id);
    if(!card){
      card=document.createElement('article');card.className='session-card';card.dataset.session=id;card.tabIndex=0;card.role='button';
      card.innerHTML=`<div class="session-card-head"><div><div class="session-card-badges"><span class="status-badge ${scheduled?'scheduled':'recording'}" data-field="status"></span>${scheduled?'':'<span class="status-badge hidden" data-field="phase"></span>'}</div><h3 data-field="name"></h3><p data-field="device"></p></div></div><div class="session-card-grid"><div><span>${scheduled?'Start':'Gestartet'}</span><strong data-field="start"></strong></div><div><span>${scheduled?'Stop':'Auto-Stop'}</span><strong data-field="stop"></strong></div>${scheduled?'':'<div><span>Laufzeit</span><strong data-field="elapsed"></strong></div><div data-field="remainingRow"><span>Verbleibend</span><strong data-field="remaining"></strong></div>'}</div>`;
      card.addEventListener('click',()=>openSession(card.dataset.session));
      card.addEventListener('keydown',event=>{if(event.key==='Enter'||event.key===' '){event.preventDefault();openSession(card.dataset.session)}});
    }
    const fields={status:scheduled?'GEPLANT':'RECORDING',name:item.name??'',device:`${item.port||'—'} · ${item.ppk2_id||'PPK2'}`,start:fmtDate(scheduled?item.scheduled_start_at:item.started_at),stop:scheduled||['wake_count','sleep_count'].includes(item.stop_mode)?scheduleStopText(item):item.planned_end_at?fmtDate(item.planned_end_at):'manuell'};
    if(!scheduled){
      const phase={ACTIVE:['Wake','wake-state'],SLEEP:['Sleep','sleep-state'],WAITING_SLEEP:['Sleep-Suche','search-state'],CALIBRATING:['Kalibrierung','search-state']}[item.state];
      const badge=card.querySelector('[data-field="phase"]');
      const phaseClass=`status-badge ${phase?phase[1]:'hidden'}`;
      if(badge.className!==phaseClass)badge.className=phaseClass;
      fields.phase=phase?phase[0]:'';
      const start=item.started_at?Date.parse(item.started_at):NaN,end=item.planned_end_at?Date.parse(item.planned_end_at):NaN;
      fields.elapsed=Number.isFinite(start)?(now-start<1000?'0 s':fmtDuration(Math.floor(Math.max(0,now-start)/1000))):'—';
      fields.remaining=Number.isFinite(end)?(now>=end?'0 s':fmtDuration(Math.ceil(Math.max(0,end-now)/1000))):'—';
      card.querySelector('[data-field="remainingRow"]').classList.toggle('hidden',!Number.isFinite(end));
    }
    for(const [field,value] of Object.entries(fields)){
      const el=card.querySelector(`[data-field="${field}"]`);if(el.textContent!==value)el.textContent=value;
    }
    if(container.children[index]!==card)container.insertBefore(card,container.children[index]||null);
  });
}
function renderLiveOverview(data){
  data=data||{running:[],scheduled:[]};state.liveOverview=data;const running=data.running||[],scheduled=data.scheduled||[],total=running.length+scheduled.length;
  $('runningCount').textContent=running.length;$('scheduledCount').textContent=scheduled.length;$('liveCount').textContent=total;$('liveCount').classList.toggle('hidden',total===0);
  $('liveEmpty').classList.toggle('hidden',total!==0);$('liveLists').classList.toggle('hidden',total===0);
  $('noRunning').classList.toggle('hidden',running.length!==0);$('noScheduled').classList.toggle('hidden',scheduled.length!==0);
  const serverNow=data.server_time?Date.parse(data.server_time):NaN;
  renderSessionCards('runningCards',running,false,Number.isFinite(serverNow)?serverNow:Date.now());
  renderSessionCards('scheduledCards',scheduled,true);
  if(state.view==='session'&&state.sessionMeasurement?.status==='scheduled'&&!state.sessionRefreshPending) {
    const id=state.sessionMeasurement.id;
    if(running.some(x=>x.measurement_id===id)||!scheduled.some(x=>x.id===id)){
      const token=state.navigationToken;state.sessionRefreshPending=true;
      api(`/api/measurements/${id}`).then(m=>{
        if(state.view==='session'&&token===state.navigationToken&&state.sessionMeasurement?.id===id&&m.status!=='scheduled')return openSession(id);
      }).catch(()=>{}).finally(()=>{state.sessionRefreshPending=false});
    }
  }
}
function scheduleStopText(m){if(m.stop_mode==='duration')return fmtDuration(m.requested_duration_s);if(m.stop_mode==='end')return fmtDate(m.scheduled_end_at);if(m.stop_mode==='wake_count'||m.stop_mode==='sleep_count')return`${m.event_count??m.settings?.event_count} bestätigte ${m.stop_mode==='wake_count'?'Wakes → Sleep-Start':'Sleeps → Wake-Start'}`;return'manuell'}
function connectOverview(){if(state.overviewWs)try{state.overviewWs.close()}catch(_){}const proto=location.protocol==='https:'?'wss:':'ws:';const ws=new WebSocket(`${proto}//${location.host}/ws/live`);state.overviewWs=ws;ws.onmessage=e=>renderLiveOverview(JSON.parse(e.data));ws.onclose=()=>setTimeout(connectOverview,1500)}

function updateScheduleFields(){
  const f=$('measurementForm'),start=f.elements.start_mode.value,stop=f.elements.stop_mode.value;
  const startInput=$('scheduledStartAt'),durationValue=$('durationValue'),durationUnit=$('durationUnit'),endInput=$('scheduledEndAt');
  const startDisabled=start!=='scheduled',durationDisabled=stop!=='duration',endDisabled=stop!=='end';
  startInput.disabled=startDisabled;startInput.required=!startDisabled;$('startAtLabel').classList.toggle('is-disabled',startDisabled);
  durationValue.disabled=durationDisabled;durationUnit.disabled=durationDisabled;durationValue.required=!durationDisabled;$('durationLabel').classList.toggle('is-disabled',durationDisabled);
  endInput.disabled=endDisabled;endInput.required=!endDisabled;$('endAtLabel').classList.toggle('is-disabled',endDisabled);
  $('startAtLabel').hidden=startDisabled;$('durationLabel').hidden=durationDisabled;$('endAtLabel').hidden=endDisabled;
  $('scheduleStartHint').hidden=!startDisabled;$('scheduleStopHint').hidden=stop!=='manual';
  const eventDisabled=stop!=='wake_count'&&stop!=='sleep_count';
  $('eventCount').disabled=eventDisabled;$('eventCount').required=!eventDisabled;$('eventCountLabel').hidden=eventDisabled;
  $('eventCountHelp').textContent=stop==='wake_count'?'Stop am nächsten bestätigten Sleep-Start nach der gewählten Anzahl Wakes.':'Der erste bestätigte Sleep zählt mit. Stop am nächsten bestätigten Wake-Start nach der gewählten Anzahl Sleeps.';
  const now=new Date();const minStart=localInput(now);startInput.min=minStart;
  const selectedStart=!startDisabled&&startInput.value?new Date(startInput.value):now;
  endInput.min=localInput(new Date(selectedStart.getTime()+1000));
}
$('startMode').addEventListener('change',updateScheduleFields);$('stopMode').addEventListener('change',updateScheduleFields);$('scheduledStartAt').addEventListener('change',updateScheduleFields);
function setDialogDefaults(){const f=$('measurementForm');f.reset();f.elements.name.value='ULP Sleep / Wake Test';f.elements.project.value='ULP Application';f.elements.device.value='Prototype #1';f.elements.firmware.value='';f.elements.detection_mode.value='threshold';$('scheduledStartAt').value=localInput(new Date(Date.now()+5*60000));$('scheduledEndAt').value=localInput(new Date(Date.now()+65*60000));CurrentInputs.restore();TimeInputs.restore();updateScheduleFields();updateSpectralSettings()}
async function openMeasurementDialog(edit=null){await MeasurementPreview.reset();startDeviceDiscovery();state.editScheduledId=edit?.id||null;const selectedPort=$('portSelect').value;setDialogDefaults();$('portSelect').value=selectedPort;renderDevices(state.devices);const f=$('measurementForm');f.dataset.dialogToken=String(Number(f.dataset.dialogToken||0)+1);if(edit){
  CurrentInputs.canonicalize();TimeInputs.canonicalize();const s=edit.settings||{};for(const k of ['sleep_threshold_ua','wake_threshold_ua','sleep_min_s','wake_min_ms','post_trigger_ms','pre_trigger_ms','name','project','device','serial_number','firmware','hardware_version','notes','port','meter_mode','voltage_mv'])if(f.elements[k]&&s[k]!=null)f.elements[k].value=s[k];
  // Restore supported modes; removed historical modes require an explicit new selection.
  f.elements.detection_mode.value=['threshold','spectral_compare'].includes(s.detection_mode)?s.detection_mode:'threshold';f.elements.spectral_margin_db.value=s.spectral_margin_db??12;updateSpectralSettings();CurrentInputs.restore();TimeInputs.restore();
  f.elements.name.value=edit.name;f.elements.project.value=edit.project||'';f.elements.device.value=edit.device||'';f.elements.serial_number.value=edit.serial_number||'';f.elements.firmware.value=edit.firmware||'';f.elements.hardware_version.value=edit.hardware_version||'';f.elements.notes.value=edit.notes||'';f.elements.port.value=edit.port||'';f.elements.meter_mode.value=edit.meter_mode;f.elements.voltage_mv.value=edit.voltage_mv;
  f.elements.start_mode.value='scheduled';$('scheduledStartAt').value=localInput(edit.scheduled_start_at);f.elements.stop_mode.value=edit.stop_mode||'manual';f.elements.event_count.value=edit.event_count??edit.settings?.event_count??5;if(edit.requested_duration_s){f.elements.duration_value.value=(edit.requested_duration_s/60).toFixed(2);f.elements.duration_unit.value='60'}if(edit.scheduled_end_at)$('scheduledEndAt').value=localInput(edit.scheduled_end_at);
  $('measurementDialogEyebrow').textContent='GEPLANTE MESSUNG';$('measurementDialogTitle').textContent='Planung bearbeiten';$('measurementSubmit').textContent='Änderungen speichern';
}else{$('measurementDialogEyebrow').textContent='NEUE MESSUNG';$('measurementDialogTitle').textContent='Messung anlegen';$('measurementSubmit').textContent='Messung anlegen'}updateScheduleFields();updateEventSettings();$('measurementDialog').showModal();$('measurementName').focus();$('measurementName').select()}
function closeMeasurementDialog(){$('measurementDialog').close();state.editScheduledId=null}
document.querySelectorAll('[data-close-dialog]').forEach(b=>b.addEventListener('click',closeMeasurementDialog));$('newMeasurementBtn').addEventListener('click',()=>openMeasurementDialog());$('idleStartBtn').addEventListener('click',()=>openMeasurementDialog());
function measurementPayload(){const f=new FormData($('measurementForm')),num=n=>{const input=$('measurementForm').elements[n];return input.dataset.currentUnit?CurrentInputs.ua(input):input.dataset.timeUnit?TimeInputs.value(input):Number(input.value)},startMode=f.get('start_mode'),stopMode=f.get('stop_mode');return{sleep_threshold_ua:num('sleep_threshold_ua'),wake_threshold_ua:num('wake_threshold_ua'),sleep_min_s:num('sleep_min_s'),wake_min_ms:num('wake_min_ms'),detection_mode:f.get('detection_mode'),spectral_margin_db:num('spectral_margin_db'),pre_trigger_ms:num('pre_trigger_ms'),post_trigger_ms:num('post_trigger_ms'),name:f.get('name'),project:f.get('project'),device:f.get('device'),serial_number:f.get('serial_number'),firmware:f.get('firmware'),hardware_version:f.get('hardware_version'),notes:f.get('notes'),port:f.get('port'),meter_mode:f.get('meter_mode'),voltage_mv:num('voltage_mv'),start_mode:startMode,scheduled_start_at:startMode==='scheduled'?asIsoLocalInput(f.get('scheduled_start_at')):null,stop_mode:stopMode,event_count:['wake_count','sleep_count'].includes(stopMode)?num('event_count'):null,duration_s:stopMode==='duration'?num('duration_value')*num('duration_unit'):null,scheduled_end_at:stopMode==='end'?asIsoLocalInput(f.get('scheduled_end_at')):null}}
$('measurementForm').addEventListener('submit',async e=>{e.preventDefault();const form=e.currentTarget,dialogToken=form.dataset.dialogToken,editScheduledId=state.editScheduledId;if(form.dataset.submitting==='1')return;updateScheduleFields();if(!e.currentTarget.reportValidity())return;const payload=measurementPayload();if(!payload.port){toast('Kein PPK2 erkannt.',true);return}if(payload.start_mode==='scheduled'&&!payload.scheduled_start_at){toast('Bitte eine Startzeit festlegen.',true);return}if(payload.stop_mode==='duration'&&!(payload.duration_s>0)){toast('Bitte eine gültige Messdauer festlegen.',true);return}if(payload.stop_mode==='end'&&!payload.scheduled_end_at){toast('Bitte eine Endzeit festlegen.',true);return}if(payload.scheduled_end_at){const reference=payload.scheduled_start_at||new Date().toISOString();if(new Date(payload.scheduled_end_at)<=new Date(reference)){toast('Die Endzeit muss nach dem Start liegen.',true);return}}form.dataset.submitting='1';$('measurementSubmit').disabled=true;try{await MeasurementPreview.beforeSave();if(!$('measurementDialog').open||form.dataset.dialogToken!==dialogToken)return;const m=editScheduledId?await api(`/api/measurements/${editScheduledId}/scheduled`,{method:'PATCH',body:JSON.stringify(payload)}):await api('/api/measurements',{method:'POST',body:JSON.stringify(payload)});closeMeasurementDialog();toast(m.status==='scheduled'?'Messung geplant':'Messung gestartet');await openSession(m.id)}catch(err){toast(err.message,true)}finally{form.dataset.submitting='0';$('measurementSubmit').disabled=false}});

async function openSession(id){
  const token=++state.navigationToken;
  closeSessionWs();
  try{const m=await api(`/api/measurements/${id}`);if(token!==state.navigationToken)return;if(!['scheduled','starting','recording'].includes(m.status)){return openMeasurement(id)}state.sessionMeasurement=m;renderSession(m);setView('session');if(m.status==='recording'||m.status==='starting')connectSessionLive(id);else closeSessionWs()}catch(err){if(token===state.navigationToken)toast(err.message,true)}
}
function renderSession(m){
  renderLiveWakePhases(m.cycle_energy?.valid_wake_phases||[]);
  $('sessionStopBtn').disabled=state.sessionStopPendingId===m.id;
  $('sessionStopBtn').textContent=state.sessionStopPendingId===m.id?'Stoppt …':'■ Stop';
  $('sessionStatus').textContent=String(m.status||'').toUpperCase();$('sessionName').textContent=m.name;$('sessionMeta').textContent=[m.project,m.device,m.serial_number?`SN: ${m.serial_number}`:'',m.firmware?`FW: ${m.firmware}`:'',m.hardware_version?`HW: ${m.hardware_version}`:''].filter(Boolean).join(' · ')||'Ohne Metadaten';
  const scheduled=m.status==='scheduled';$('editScheduledBtn').classList.toggle('hidden',!scheduled);$('cancelScheduledBtn').classList.toggle('hidden',!scheduled);$('sessionStopBtn').classList.toggle('hidden',scheduled);$('sessionLiveArea').classList.toggle('hidden',scheduled);$('sessionPendingInfo').classList.toggle('hidden',!scheduled);
  $('sessionTimeMain').textContent=scheduled?'Geplant':m.started_at?fmtDuration((Date.now()-new Date(m.started_at).getTime())/1000):'Startet …';$('sessionTimeSub').textContent=scheduled?`Stop: ${scheduleStopText(m)}`:`Start: ${fmtDate(m.started_at)}`;
  const s=m.settings||{};$('sessionSetup').innerHTML=[['PPK2',m.ppk2_id||m.port||'—'],['COM-Port',m.port||'—'],['Messmodus',m.meter_mode==='source'?'Source meter':'Ampere meter'],['Spannung',`${(m.voltage_mv/1000).toFixed(3)} V`],['Sample Rate',`${(m.sample_rate_hz/1000).toFixed(0)} kS/s`],['Erkennung',s.detection_mode==='spectral_compare'?'Spektralvergleich (experimentell)':s.detection_mode==='threshold'?'Stromschwellen und Mindestdauer':'Entfernter Modus – Planung bearbeiten'],['Vorlauf',`${s.pre_trigger_ms??1000} ms`],['Nachlauf',`${s.post_trigger_ms??1000} ms`],...(['threshold','spectral_compare'].includes(s.detection_mode)?[['Sleep unter',fmtCurrent(s.sleep_threshold_ua)],['Wake über',fmtCurrent(s.wake_threshold_ua)],['Sleep-Mindestdauer',`${s.sleep_min_s} s`],['Wake-Mindestdauer',`${s.wake_min_ms} ms`]]:[]),['Auto-Stop',scheduleStopText(m)]].map(([k,v])=>`<div class="setup-item"><span>${escapeHtml(k)}</span><strong>${escapeHtml(v)}</strong></div>`).join('');
  if(scheduled)$('sessionPendingInfo').innerHTML=`<strong>Ausstehende Messung.</strong><br>PowerLab startet diese Session automatisch am <b>${escapeHtml(fmtDate(m.scheduled_start_at))}</b>. Das gewählte PPK2 muss zu diesem Zeitpunkt angeschlossen und frei sein. Bis dahin kannst du Setup und Zeitplanung über „Planung bearbeiten“ ändern.`;
}
function closeSessionWs(){
  clearTimeout(state.sessionReconnectTimer);state.sessionReconnectTimer=null;
  clearTimeout(state.liveZoomTimer);state.liveZoomTimer=null;
  ++state.liveSeriesRequestToken;state.liveSeriesInFlight=null;state.lastLiveSnapshot=null;
  state.liveStreamSeries=null;state.liveStreamRenderPending=false;
  const ws=state.sessionWs;state.sessionWs=null;
  if(ws){ws.onmessage=null;ws.onclose=null;try{ws.close()}catch(_){}}
}
function connectSessionLive(id,{reconnect=false}={}){
  if(state.view!=='session'||state.sessionMeasurement?.id!==id)return;
  closeSessionWs();
  if(!reconnect){state.liveFollow=true;state.liveFullUserZoomed=false;state.liveFullRange=null;state.liveLastSeries=null;}
  state.lastLiveRevision=null;state.lastLiveSnapshot=null;state.liveFullRefreshAt=0;state.liveSeriesRequestToken++;
  updateLiveFollowButton();
  const proto=location.protocol==='https:'?'wss:':'ws:';
  const maxPoints=Math.max(500,Math.min(4000,Math.ceil($('liveChart')?.clientWidth||1000)));
  const ws=new WebSocket(`${proto}//${location.host}/ws/live/${id}?max_points=${maxPoints}`);state.sessionWs=ws;
  ws.onmessage=async e=>{
    if(state.sessionWs!==ws||state.view!=='session'||state.sessionMeasurement?.id!==id)return;
    const frame=JSON.parse(e.data),d=frame.snapshot;
    if(d.running){
      state.lastLiveSnapshot=d;
      if(frame.series){
        state.liveStreamSeries=mergeLiveFrame(state.liveStreamSeries,frame);
        if(frame.reset)renderLiveWakePhases(state.liveStreamSeries.valid_wake_phases||[]);
      }
      queueLiveStreamRender();
      renderSessionLive(d);
    }else{
      const token=state.navigationToken;closeSessionWs();
      try{const m=await api(`/api/measurements/${id}`);if(token!==state.navigationToken||state.view!=='session'||state.sessionMeasurement?.id!==id)return;if(!['recording','starting'].includes(m.status)){toast(m.status==='failed'?`Messung abgebrochen: ${m.error||'Aufzeichnungsfehler'}`:'Messung beendet',m.status==='failed');openMeasurement(id)}}catch(_){}
    }
  };
  ws.onclose=()=>{
    if(state.sessionWs!==ws||state.view!=='session'||state.sessionMeasurement?.id!==id)return;
    state.sessionReconnectTimer=setTimeout(()=>{
      state.sessionReconnectTimer=null;
      if(state.sessionWs===ws&&state.view==='session'&&state.sessionMeasurement?.id===id&&['recording','starting'].includes(state.sessionMeasurement.status))connectSessionLive(id,{reconnect:true});
    },1500);
  };
}
function updateLiveFollowButton(){
  const b=$('liveFollowBtn');b.textContent=state.liveFollow?'● Live folgen':'○ Ansicht fixiert';b.classList.toggle('paused',!state.liveFollow);
  b.title=state.liveFollow?'Die komplette Messhistorie bleibt sichtbar und wächst am rechten Rand weiter.':'Der sichtbare Bereich bleibt fixiert. Zoomen und Verschieben lädt automatisch passende Detaildaten.';
  const sub=$('liveChartSubtitle');if(sub)sub.textContent=state.liveFollow?'Komplette Messhistorie · adaptive Level-of-Detail-Darstellung':'Ansicht fixiert · Zoom lädt automatisch höhere Auflösung';
}
function liveTrace(points,name='Historie',lineWidth=1.15,opacity=1,unit={factor:1}){
  // Sparse event summaries do not describe a waveform. Keep their peak markers
  // separately and break lines between independent measured/mean segments.
  const segmented=[];let previous=null;
  for(const point of points){
    if(point.kind==='mean'){previous=null;segmented.push(null);continue;}
    if(String(point.kind||'').startsWith('event_')){previous=null;segmented.push(null);continue;}
    const key=point.line_key??(point.event_id!=null?`wake-${point.event_id}`:point.kind||'history');
    if(segmented.length&&previous!==key)segmented.push(null);
    segmented.push(point);previous=key;
  }
  const x=segmented.map(p=>p===null?null:Number(p.t_s));
  const actual=segmented.map(p=>p===null?null:Number(p.current_ua));
  const y=actual.map(v=>v===null?null:(state.liveScale==='log'?Math.max(.001,v):v)*unit.factor);
  const customdata=segmented.map((p,i)=>p===null?null:[actual[i],p.kind||'history',fmtCurrent(actual[i])]);
  return{x,y,customdata,type:'scattergl',mode:'lines',name,line:{width:lineWidth,color:'#65d98b'},opacity,connectgaps:false,hovertemplate:'t=%{x:.6f}s<br>%{customdata[2]}<br>%{customdata[1]}<extra></extra>'};
}
function liveSummaryTraces(points,unit={factor:1}){
  const transform=v=>(state.liveScale==='log'?Math.max(.001,Number(v)):Number(v))*unit.factor;
  const groups=[];
  for(const p of points){
    const group=groups[groups.length-1],previous=group?.[group.length-1];
    if(!previous||previous.line_key!==p.line_key||previous.end_sample+1!==p.sample_index)groups.push([p]);
    else group.push(p);
  }
  const bandX=[],bandY=[],x=[],y=[],customdata=[];
  for(const group of groups){
    for(const p of group){
      const start=p.display_start_s??p.t_s,end=p.display_end_s??p.end_s;
      bandX.push(start,end);bandY.push(transform(p.max_ua),transform(p.max_ua));
      x.push(start,end);y.push(transform(p.current_ua),transform(p.current_ua));
      const cd=[fmtCurrent(p.current_ua),fmtCurrent(p.min_ua),fmtCurrent(p.max_ua),p.sample_count,p.t_s,p.end_s];
      customdata.push(cd,cd);
    }
    for(const p of [...group].reverse()){
      bandX.push(p.display_end_s??p.end_s,p.display_start_s??p.t_s);
      bandY.push(transform(p.min_ua),transform(p.min_ua));
    }
    bandX.push(null);bandY.push(null);x.push(null);y.push(null);customdata.push(null);
  }
  return [
    {x:bandX,y:bandY,type:'scatter',mode:'lines',name:'Min/Max-Bereich',fill:'toself',
      fillcolor:'rgba(101,217,139,0.12)',line:{width:0},hoverinfo:'skip',connectgaps:false},
    {x,y,customdata,type:'scattergl',mode:'lines',name:'Mittlerer Strom',line:{width:1.7,color:'#65d98b'},connectgaps:false,
      hovertemplate:'Mittelwert %{customdata[0]}<br>Min %{customdata[1]} · Max %{customdata[2]}<br>%{customdata[3]} Samples · Block %{customdata[4]:.6f}–%{customdata[5]:.6f} s<extra></extra>'}
  ];
}
function liveEventTrace(events,unit={factor:1}){
  const actual=events.map(e=>Number(e.peak_ua)),y=actual.map(v=>(state.liveScale==='log'?Math.max(.001,v):v)*unit.factor);
  return{x:events.map(e=>Number(e.t_s)),y,customdata:events.map((e,i)=>[e.sequence,actual[i],fmtCurrent(actual[i])]),type:'scattergl',mode:'markers',name:'Wake',marker:{symbol:'triangle-up',size:7,color:'#f5bd5b'},hovertemplate:'Wake #%{customdata[0]}<br>t=%{x:.6f}s<br>Peak %{customdata[2]}<extra></extra>'};
}
function stateMarkerLabel(m){return {sleep_start:'Sleep Start',wake_start:'Wake Start',sleep_validated:'Sleep validiert',wake_validated:'Wake validiert',fft_sleep_start:'FFT Sleep (experimentell)',fft_wake_start:'FFT Wake (experimentell)'}[m.kind||'sleep_start']||m.kind}
function stateMarkerColor(m){return m.kind?.startsWith('fft_')?(m.kind.includes('wake')?'#f472b6':'#22d3ee'):(m.kind?.includes('wake')?'#f5bd5b':'#60a5fa')}
function sleepStartTrace(markers,unit={factor:1},history=false){
  return {x:markers.map(m=>history?(m.timestamp||m.t_s):m.t_s),
    y:markers.map(m=>history?(m.kind?.includes('wake')?1:0):(state.liveScale==='log'?Math.max(.001,m.current_ua):m.current_ua)*unit.factor),
    customdata:markers.map(m=>[m.t_s,stateMarkerLabel(m)]),text:markers.map(m=>m.sample_index===0?'Sleep Start':''),
    textposition:'top right',type:'scatter',mode:'markers+text',name:'Sleep / Wake',
    marker:{symbol:markers.map(m=>m.kind?.endsWith('validated')?'circle-open':'circle'),size:6,color:markers.map(stateMarkerColor)},textfont:{color:'#60a5fa'},
    hovertemplate:'%{customdata[1]}<br>t=%{customdata[0]:.6f}s<extra></extra>',cliponaxis:false};
}
function sleepStartShapes(markers,history=false){
  return markers.map(m=>({type:'line',xref:'x',yref:'paper',x0:history?(m.timestamp||m.t_s):m.t_s,
    x1:history?(m.timestamp||m.t_s):m.t_s,y0:0,y1:1,opacity:.3,line:{color:stateMarkerColor(m),width:1,dash:m.kind?.startsWith('fft_')?'dash':m.kind?.endsWith('validated')?'dot':'solid'}}));
}
function liveLayout(measurementId,{range=null,latestS=0,unit={label:'µA'}}={}){
  const xaxis={title:{text:'Zeit seit Messbeginn [s]',standoff:14},gridcolor:'#222a31',color:'#aeb8c2',zeroline:false,autorange:false,uirevision:state.liveFollow?`follow-${latestS}`:`fixed-${measurementId}`};
  if(range&&range.length===2)xaxis.range=range;else xaxis.range=[0,Math.max(.001,Number(latestS)||.001)];
  return{paper_bgcolor:'transparent',plot_bgcolor:'transparent',margin:{l:82,r:24,t:10,b:68},xaxis,yaxis:{title:{text:`Strom [${unit.label}]`,standoff:14},type:state.liveScale,gridcolor:'#222a31',color:'#aeb8c2',zeroline:false,uirevision:`live-y-${measurementId}-${state.liveScale}-${unit.label}`},font:{family:'Segoe UI, Inter, system-ui, sans-serif',color:'#c8d0d8',size:11},showlegend:false,hovermode:'closest',hoverlabel:{bgcolor:'#171d23',bordercolor:'#48535f',font:{color:'#edf2f6'}},dragmode:'zoom',uirevision:`live-${measurementId}-${state.liveScale}`};
}
async function renderLiveSeries(series,d,token){
  const render=async()=>{
    if(token!==state.liveSeriesRequestToken||state.view!=='session'||state.sessionMeasurement?.id!==d.measurement_id)return;
    state.liveLastSeries=series;
    const traces=[];
    const sleepStarts=series.state_markers||[];
    const historyPoints=series.history_points||series.points||[];
    const livePoints=series.live_points||[];
    const summaries=series.summary_points||[];
    const unit=currentUnit([...historyPoints.map(p=>p.current_ua),...livePoints.map(p=>p.current_ua),...summaries.map(p=>p.max_ua),...(series.events||[]).map(e=>e.peak_ua)]);
    if(summaries.length)traces.push(...liveSummaryTraces(summaries,unit));
    if(series.display_mode!=='summary'){
      if(historyPoints.some(p=>p.kind!=='mean'))traces.push(liveTrace(historyPoints,'Historie',1.15,1,unit));
      if(livePoints.length)traces.push(liveTrace(livePoints,'Live-Tail',1.0,.9,unit));
    }
    if((series.events||[]).length)traces.push(liveEventTrace(series.events,unit));
    if(sleepStarts.length)traces.push(sleepStartTrace(sleepStarts,unit));
    const plotRange=state.liveFollow?[0,Math.max(.001,Number(series.latest_s)||.001)]:(state.liveFullRange||[Number(series.start_s)||0,Math.max(.001,Number(series.end_s)||.001)]);
    state.liveIgnoreRelayoutUntil=Date.now()+250;
    ++state.livePlotPending;
    try{await Plotly.react('liveChart',traces,{...liveLayout(d.measurement_id,{range:plotRange,latestS:series.latest_s,unit}),shapes:sleepStartShapes(sleepStarts),xaxis:{...liveLayout(d.measurement_id,{range:plotRange,latestS:series.latest_s,unit}).xaxis,title:{text:series.phase==='startup'?'Zeit seit Einschalten [s]':'Zeit seit Sleep Start [s]'}}},plotConfig)}finally{--state.livePlotPending;state.liveIgnoreRelayoutUntil=Date.now()+250;}
    if(token!==state.liveSeriesRequestToken)return;
    bindLiveChartInteractions();
    const sub=$('liveChartSubtitle');if(sub){const mode=series.display_mode==='summary'?'Mittelwerte + Min/Max-Bereich · Zoom zeigt Details':String(series.detail||'').startsWith('raw')?'Zoom: Wake-Rohdaten + Mittelwertübersicht':'Zoom: verdichtete Messwerte · Min/Max-Bereich';const n=Number(series.point_count||0);sub.textContent=`${mode} · ${n.toLocaleString('de-DE')} Datenblöcke / Punkte`; }
  };
  state.liveRenderChain=state.liveRenderChain.catch(e=>console.warn('Live render failed',e)).then(render);
  return state.liveRenderChain;
}
async function queueLiveStreamRender(){
  state.liveStreamRenderPending=true;
  if(state.liveStreamRenderActive)return;
  state.liveStreamRenderActive=true;
  try{
    while(state.liveStreamRenderPending){
      state.liveStreamRenderPending=false;
      if(state.view==='session'&&state.liveStreamSeries&&state.lastLiveSnapshot)await renderLiveSpectrogram(state.liveStreamSeries);
      if(!state.liveFollow||!state.liveStreamSeries||!state.lastLiveSnapshot||state.view!=='session')continue;
      await renderLiveSeries(state.liveStreamSeries,state.lastLiveSnapshot,state.liveSeriesRequestToken);
    }
  }catch(e){console.warn('Live stream render failed',e)}finally{state.liveStreamRenderActive=false;}
}
async function refreshLiveSeries({force=false,range=null}={}){
  const d=state.lastLiveSnapshot;if(state.view!=='session'||!d||!d.measurement_id)return;
  if(state.liveFollow&&!range){queueLiveStreamRender();return;}
  if(!force&&state.liveSeriesInFlight!==null)return;
  const requestedRange=range||(state.liveFollow?null:state.liveFullRange);
  const width=$('liveChart')?.clientWidth||1000;
  const params=new URLSearchParams({max_points:String(Math.max(500,Math.min(4000,Math.ceil(width)))),view:state.liveFullUserZoomed?'detail':'overview'});
  if(requestedRange&&requestedRange.length===2){params.set('start_s',String(Math.max(0,Number(requestedRange[0]))));params.set('end_s',String(Math.max(0,Number(requestedRange[1]))));}
  const token=++state.liveSeriesRequestToken;
  state.liveSeriesInFlight=token;state.liveFullRefreshAt=Date.now();
  try{
    const series=await api(`/api/measurements/${d.measurement_id}/series?${params.toString()}`);
    if(token!==state.liveSeriesRequestToken||state.view!=='session'||state.sessionMeasurement?.id!==d.measurement_id)return;
    state.liveLastSeries=series;state.liveFullRefreshAt=Date.now();
    await renderLiveSeries(series,d,token);
  }catch(e){console.warn('Live LOD refresh failed',e)}finally{if(state.liveSeriesInFlight===token)state.liveSeriesInFlight=null;}
}
function scheduleLiveRangeRefresh(range){
  if(state.liveZoomTimer)clearTimeout(state.liveZoomTimer);
  state.liveZoomTimer=setTimeout(()=>refreshLiveSeries({force:true,range}),180);
}
function bindLiveChartInteractions(){
  const el=$('liveChart');if(el.dataset.bound==='1'||typeof el.on!=='function')return;el.dataset.bound='1';
  el.on('plotly_relayout',ev=>{
    if(state.view!=='session'||state.livePlotPending||Date.now()<state.liveIgnoreRelayoutUntil)return;
    const arr=ev['xaxis.range'];const r0=ev['xaxis.range[0]'],r1=ev['xaxis.range[1]'];let range=null;
    if(Array.isArray(arr)&&arr.length===2)range=[Number(arr[0]),Number(arr[1])];else if(r0!==undefined&&r1!==undefined)range=[Number(r0),Number(r1)];
    if(range&&range.every(Number.isFinite)){
      ++state.liveSeriesRequestToken;
      state.liveFollow=false;state.liveFullUserZoomed=true;state.liveFullRange=[Math.max(0,range[0]),Math.max(0,range[1])];updateLiveFollowButton();scheduleLiveRangeRefresh(state.liveFullRange);return;
    }
    if(ev['xaxis.autorange']===true){
      ++state.liveSeriesRequestToken;
      state.liveFollow=false;state.liveFullUserZoomed=false;
      const latest=Number(state.liveLastSeries?.latest_s??state.lastLiveSnapshot?.total_samples/100000??0);state.liveFullRange=[0,Math.max(.001,latest)];updateLiveFollowButton();scheduleLiveRangeRefresh(state.liveFullRange);
    }
  });
}
function renderLiveWakePhases(phases){
  $('liveWakePhaseCount').textContent=String(phases.length);
  $('liveWakePhaseRows').innerHTML=phases.map(p=>`<tr><td>Wake #${Number(p.sequence)}</td><td>${escapeHtml(fmtDuration(p.start_s))}</td><td>${escapeHtml(fmtEventDuration(p.duration_s*1e6))}</td><td>${escapeHtml(fmtCurrent(p.mean_ua))}</td><td>${escapeHtml(fmtCurrent(p.peak_ua))}</td><td>${escapeHtml(fmtCharge(p.charge_uc))}</td><td>${escapeHtml(fmtEnergy(p.energy_uwh))}</td><td>${p.interpolated_samples?'Mit ergänzten Daten':'Vollständig'}</td></tr>`).join('')||'<tr><td colspan="8">Noch keine gültige Wake-Phase abgeschlossen.</td></tr>';
}
function renderSessionLive(d,force=false){
  const stopping=d.stopping||state.sessionStopPendingId===d.measurement_id;
  $('sessionStopBtn').disabled=!!stopping;$('sessionStopBtn').textContent=stopping?'Stoppt …':'■ Stop';
  $('sessionStatus').textContent=stopping?'STOPPING':d.state==='WAITING_SLEEP'?'SLEEP-SUCHE':'RECORDING';$('sessionTimeMain').textContent=d.state==='WAITING_SLEEP'?d.detection?.expected_sleep_enabled&&d.detection?.expected_sleep_match===false?`Warte auf Ruhebereich bei ${fmtCurrent(d.detection.expected_sleep_ua)}`:`Sleep-Suche: ${(d.detection?.stability_s||0).toFixed(1)} / ${d.detection?.required_stability_s??60} s`:fmtDuration(d.detection?d.detection.timeline_samples/(d.sample_rate_hz||100000):(Date.now()-new Date(d.started_at).getTime())/1000);$('sessionTimeSub').textContent=d.planned_end_at?`Auto-Stop: ${fmtDate(d.planned_end_at)}`:`Start: ${fmtDate(d.started_at)}`;
  $('kpiCurrent').textContent=fmtCurrent(d.current_ua);$('kpiSleep').textContent=fmtCurrent(d.baseline_ua);$('kpiThreshold').textContent=d.detection?`Sleep < ${fmtCurrent(d.detection.sleep_threshold_ua)} · Wake > ${fmtCurrent(d.detection.wake_threshold_ua)}`:'—';$('kpiPeak').textContent=fmtCurrent(d.peak_current_ua);$('kpiWake').textContent=Number(d.wake_count||0).toLocaleString('de-DE');$('kpiBackground').textContent=d.spectral_comparison?`FFT: ${d.spectral_comparison.state} | ${d.spectral_comparison.score_db==null?`${Math.round(d.spectral_comparison.training_s)}/${d.spectral_comparison.reference_s} s Referenz`:d.spectral_comparison.score_db.toFixed(1)+' dB Abweichung'}`:'Feste Schwellen';$('kpiCharge').textContent=fmtCharge(d.total_charge_uc);$('kpiEnergy').textContent=fmtEnergy(d.energy_uwh);
  if(state.liveFollow){refreshLiveSeries({force});}
}
async function stopSessionMeasurement(){
  const id=state.sessionMeasurement?.id;if(!id||state.sessionStopPendingId===id)return;
  state.sessionStopPendingId=id;$('sessionStopBtn').disabled=true;$('sessionStopBtn').textContent='Stoppt …';$('sessionStatus').textContent='STOPPING';
  try{await api(`/api/measurements/${id}/stop`,{method:'POST'});toast('Messung beendet')}
  catch(e){toast(e.message,true);if(state.sessionMeasurement?.id===id)$('sessionStatus').textContent='RECORDING'}
  finally{
    if(state.sessionStopPendingId===id)state.sessionStopPendingId=null;
    if(state.sessionMeasurement?.id===id){$('sessionStopBtn').disabled=false;$('sessionStopBtn').textContent='■ Stop'}
  }
}
$('backToLive').addEventListener('click',()=>{closeSessionWs();setView('live')});$('editScheduledBtn').addEventListener('click',()=>openMeasurementDialog(state.sessionMeasurement));$('cancelScheduledBtn').addEventListener('click',async()=>{if(!confirm('Geplante Messung wirklich abbrechen?'))return;try{await api(`/api/measurements/${state.sessionMeasurement.id}/cancel`,{method:'POST'});toast('Planung abgebrochen');setView('live')}catch(e){toast(e.message,true)}});$('sessionStopBtn').addEventListener('click',stopSessionMeasurement);
function updateSpectralSettings(){ $('spectralSettings').classList.toggle('hidden',$('detectionMode').value!=='spectral_compare'); }
$('detectionMode').addEventListener('change',updateSpectralSettings);
$('spectrogramToggle').addEventListener('change',()=>{
  $('spectrogramPanel').classList.toggle('hidden',!$('spectrogramToggle').checked);
  $('spectrogramToggle').setAttribute('aria-expanded',String($('spectrogramToggle').checked));
  queueLiveStreamRender();
});
document.querySelectorAll('[data-live-scale]').forEach(b=>b.addEventListener('click',()=>{state.liveScale=b.dataset.liveScale;document.querySelectorAll('[data-live-scale]').forEach(x=>x.classList.toggle('active',x===b));refreshLiveSeries({force:true,range:state.liveFollow?null:state.liveFullRange})}));
$('liveFollowBtn').addEventListener('click',()=>{
  clearTimeout(state.liveZoomTimer);state.liveZoomTimer=null;
  ++state.liveSeriesRequestToken;
  if(state.liveFollow){
    const chart=$('liveChart'),r=chart?.layout?.xaxis?.range;state.liveFollow=false;state.liveFullUserZoomed=false;state.liveFullRange=Array.isArray(r)&&r.length===2?[Number(r[0]),Number(r[1])]:[0,Number(state.liveLastSeries?.latest_s||0)];updateLiveFollowButton();
  }else{
    state.liveFollow=true;state.liveFullUserZoomed=false;state.liveFullRange=null;state.liveFullRefreshAt=0;updateLiveFollowButton();refreshLiveSeries({force:true});
  }
});


async function loadMeasurements(){try{state.measurements=await api('/api/measurements');renderMeasurementRows()}catch(e){toast(e.message,true)}}
function historyMeasurements(){return state.measurements.filter(m=>!['scheduled','starting','recording'].includes(m.status))}
function visibleMeasurements(){const q=$('historySearch').value.trim().toLowerCase();return historyMeasurements().filter(m=>!q||[m.name,m.project,m.device,m.serial_number,m.firmware,m.hardware_version,m.status].join(' ').toLowerCase().includes(q))}
function updateMeasurementSelection(){
  const rows=visibleMeasurements(),count=state.selectedMeasurementIds.size;
  const visibleCount=rows.filter(m=>state.selectedMeasurementIds.has(m.id)).length;
  $('measurementSelectionCount').textContent=`${count} ausgewählt${count>visibleCount?` (${count-visibleCount} ausgeblendet)`:''}`;
  for(const id of ['clearMeasurementSelection','exportSelectedMeasurements','deleteSelectedMeasurements'])$(id).disabled=!count||state.measurementBulkBusy;
  $('bulkExportKind').disabled=state.measurementBulkBusy;
  const all=$('selectAllMeasurements');
  all.checked=rows.length>0&&visibleCount===rows.length;
  all.indeterminate=visibleCount>0&&visibleCount<rows.length;
  all.disabled=!rows.length||state.measurementBulkBusy;
}
function renderMeasurementRows(){
  const validIds=new Set(historyMeasurements().map(m=>m.id));
  for(const id of state.selectedMeasurementIds)if(!validIds.has(id))state.selectedMeasurementIds.delete(id);
  const rows=visibleMeasurements();
  $('emptyMeasurements').classList.toggle('hidden',rows.length!==0);
  $('measurementRows').innerHTML=rows.map(m=>`<tr data-id="${escapeHtml(m.id)}" class="${state.selectedMeasurementIds.has(m.id)?'selected':''}"><td class="measurement-select-cell"><input type="checkbox" data-select-measurement="${escapeHtml(m.id)}" aria-label="Messung ${escapeHtml(m.name)} auswählen" ${state.selectedMeasurementIds.has(m.id)?'checked':''} ${state.measurementBulkBusy?'disabled':''}></td><td>${escapeHtml(fmtDate(m.started_at||m.created_at))}</td><td><button type="button" class="measurement-open" aria-label="Messung ${escapeHtml(m.name)} öffnen">${escapeHtml(m.name)}</button></td><td>${escapeHtml([m.project,m.device].filter(Boolean).join(' · ')||'—')}</td><td>${escapeHtml(m.firmware||'—')}</td><td>${escapeHtml(fmtDuration(m.duration_s))}</td><td>${escapeHtml(fmtCurrent(m.sleep_current_ua))}</td><td>${Number(m.wake_count||0).toLocaleString('de-DE')}</td><td><span class="status-badge ${escapeHtml(m.status)}">${escapeHtml(m.status)}</span></td></tr>`).join('');
  document.querySelectorAll('#measurementRows tr').forEach(r=>r.addEventListener('click',e=>{if(!e.target.closest('.measurement-select-cell'))openMeasurement(r.dataset.id)}));
  document.querySelectorAll('[data-select-measurement]').forEach(box=>box.addEventListener('change',()=>{
    if(state.measurementBulkBusy)return;
    const id=box.dataset.selectMeasurement;
    if(box.checked)state.selectedMeasurementIds.add(id);else state.selectedMeasurementIds.delete(id);
    box.closest('tr').classList.toggle('selected',box.checked);updateMeasurementSelection();
  }));
  updateMeasurementSelection();
}
$('selectAllMeasurements').addEventListener('change',e=>{
  if(state.measurementBulkBusy)return;
  for(const m of visibleMeasurements())if(e.target.checked)state.selectedMeasurementIds.add(m.id);else state.selectedMeasurementIds.delete(m.id);
  renderMeasurementRows();
});
$('clearMeasurementSelection').addEventListener('click',()=>{if(state.measurementBulkBusy)return;state.selectedMeasurementIds.clear();renderMeasurementRows()});
async function deleteSelectedMeasurements(){
  const ids=[...state.selectedMeasurementIds];
  if(state.measurementBulkBusy||!ids.length||!confirm(`${ids.length} ausgewählte Messungen inklusive Rohdaten endgültig löschen?`))return;
  state.measurementBulkBusy=true;renderMeasurementRows();
  let deleted=0;const failures=[];
  try{
    for(const id of ids){
      try{await api(`/api/measurements/${encodeURIComponent(id)}`,{method:'DELETE'});state.selectedMeasurementIds.delete(id);state.measurements=state.measurements.filter(m=>m.id!==id);++deleted}
      catch(e){failures.push(e.message)}
    }
    await loadMeasurements();
    toast(failures.length?`${deleted} gelöscht, ${failures.length} fehlgeschlagen: ${failures[0]}`:`${deleted} Messungen gelöscht`,failures.length>0);
  }finally{state.measurementBulkBusy=false;renderMeasurementRows()}
}
async function exportSelectedMeasurements(){
  const ids=[...state.selectedMeasurementIds];
  if(state.measurementBulkBusy||!ids.length)return;
  state.measurementBulkBusy=true;renderMeasurementRows();
  try{
    const response=await fetch('/api/measurements/bulk/export',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({measurement_ids:ids,kind:$('bulkExportKind').value})});
    if(!response.ok){let message='Export fehlgeschlagen';try{message=(await response.json()).detail||message}catch(_){}throw new Error(message)}
    const url=URL.createObjectURL(await response.blob()),link=document.createElement('a');
    try{link.href=url;link.download='powerlab_measurements.zip';document.body.appendChild(link);link.click()}finally{link.remove();setTimeout(()=>URL.revokeObjectURL(url),60000)}
    toast(`${ids.length} Messungen exportiert`);
  }catch(e){toast(e.message,true)}finally{state.measurementBulkBusy=false;renderMeasurementRows()}
}
$('deleteSelectedMeasurements').addEventListener('click',deleteSelectedMeasurements);
$('exportSelectedMeasurements').addEventListener('click',exportSelectedMeasurements);
$('historySearch').addEventListener('input',renderMeasurementRows);$('refreshMeasurements').addEventListener('click',loadMeasurements);
async function openMeasurement(id){
  cancelEventLoad();
  const token=++state.navigationToken;
  let renderToken;
  try{
    closeSessionWs();
    state.historyMode='loading';
    renderToken=++state.historyRenderToken;
    const [m,overview]=await Promise.all([
      api(`/api/measurements/${id}`),
      api(`/api/measurements/${id}/overview?max_points=20000`)
    ]);
    if(token!==state.navigationToken||renderToken!==state.historyRenderToken)return;
    state.currentMeasurement=m;
    state.currentOverview=overview;
    state.currentEvent=null;
    state.historyMode='overview';
    // Make the Plotly target visible before rendering it. Rendering into a hidden
    // view was a source of race/layout problems in earlier releases.
    setView('detail');
    renderMeasurementDetail(m,overview);
  }catch(e){if(token===state.navigationToken)toast(e.message,true)}
}

function renderDetectionMetadata(m){
  const detection=m.settings?.detection_result;
  const reference=detection?.wake_sleep_reference?.ready?detection.wake_sleep_reference:detection?.sleep_pattern;
  const field=(label,value)=>`<div><dt>${escapeHtml(label)}</dt><dd>${escapeHtml(value)}</dd></div>`;
  const group=(title,tone,fields)=>`<section class="detection-summary ${tone}"><h4>${escapeHtml(title)}</h4><dl>${fields.map(([label,value])=>field(label,value)).join('')}</dl></section>`;
  const groups=[];
  if(detection?.mode==='threshold'){
    groups.push(group('Sleep-Erkennung','detail-sleep',[
      ['Schwelle',`< ${fmtCurrent(detection.sleep_threshold_ua)}`],
      ['Mindestdauer',fmtDuration(detection.sleep_min_s)]
    ]),group('Wake-Erkennung','detail-wake',[
      ['Schwelle',`> ${fmtCurrent(detection.wake_threshold_ua)}`],
      ['Mindestdauer',fmtDuration(detection.wake_min_ms==null?null:detection.wake_min_ms/1000)]
    ]));
  }else if(detection){
    groups.push(group('Sleep-Erkennung','detail-sleep',[
      ['Bereich · 1–99 %',detection.sleep_p01_ua!=null?`${fmtCurrent(detection.sleep_p01_ua)} – ${fmtCurrent(detection.sleep_p99_ua)}`:'Noch kein Sleep bestätigt'],
      ['Bootphase ausgeschlossen',fmtDuration(detection.startup_excluded_s)]
    ]));
  }
  if(reference?.ready){
    groups.push(group('Sleep-Referenz','detail-sleep',[
      ['Mittelwert',fmtCurrent(reference.mean_ua)],['Streuung σ',fmtCurrent(reference.std_ua)],
      ['Bereich · 1–99 %',`${fmtCurrent(reference.low_ua)} – ${fmtCurrent(reference.high_ua)}`]
    ]));
  }
  if(detection&&detection.mode!=='threshold'){
    groups.push(group('Hintergrundpulse','detail-neutral',[
      ['Beobachtet',String(m.background_events?.length||0)],
      ...(detection.background_patterns||[]).filter(p=>p.learned).map((p,i)=>[`Muster ${i+1}`,`${fmtDuration(p.period_s)} · ${fmtEventDuration(p.duration_ms*1000)} · ${fmtCurrent(p.excess_peak_ua)}`])
    ]));
  }
  const spectral=m.settings?.spectral_result;
  if(spectral)groups.push(group('Spektralvergleich (experimentell)','detail-neutral',[['Letzter FFT-Zustand',spectral.state],['FFT-Wakes',String(spectral.wake_count)],['Abweichungsschwelle',`${spectral.margin_db} dB`],['Referenz',`${Math.round(spectral.training_s)} / ${spectral.reference_s} s`],['Ereignisse und Stopps','Stromschwellen und Mindestdauer']]));
  $('detailDetection').innerHTML=groups.join('');
  $('detailDetectionSection').classList.toggle('hidden',!groups.length);
}

function renderSleepAnalysis(data){
  for(const [id,key,format] of [['Time','recorded_duration_s',fmtDuration],['Current','average_current_ua',fmtCurrent],['Charge','charge_uc',fmtCharge],['Energy','energy_uwh',fmtEnergy],['ValidDuration','average_valid_duration_s',fmtDuration],['ValidEnergy','average_valid_energy_uwh',fmtEnergy]]){
    $(`detailSleep${id}`).textContent=format(data?.[key]??null);
  }
  $('detailSleepNote').textContent=`${data?.recorded_segment_count??0} gespeicherte Sleep-Abschnitte, ${data?.background_count??0} zugeordnete Hintergrundereignisse. Die erfassten Kennwerte zählen empfangene Samples ohne Ergänzung von Datenlücken, auch ohne vollständigen Wake-Zyklus. Speicher-Checkpoints sind keine eigenen Sleep-Phasen.`;
}

function renderEventProtocol(m,overview){
  const rate=m.sample_rate_hz||100000;
  const cycles=m.cycle_energy||overview.analysis?.confirmed_cycles;
  const validSleep=m.sleep_analysis?.valid_phases||cycles?.valid_sleep_phases||[];
  const validWake=new Map((cycles?.valid_wake_phases||[]).map(p=>[p.sequence,p]));
  const starts=(overview.state_markers||[]).filter(p=>p.kind==='sleep_start'||p.kind==='wake_start').sort((a,b)=>a.t_s-b.t_s);
  const validSleepByStart=new Map(validSleep.map(p=>[Math.round(p.start_s*rate),p]));
  const nextWakeByStart=new Map();
  let nextWake=null;
  for(let i=starts.length-1;i>=0;--i){
    const p=starts[i];
    if(p.kind==='wake_start')nextWake=p.t_s;
    else nextWakeByStart.set(p.t_s,nextWake);
  }
  const sleepStarts=starts.filter(p=>p.kind==='sleep_start');
  const sleepPhases=sleepStarts.length?sleepStarts.map((p,i)=>{
    const valid=validSleepByStart.get(Math.round(p.t_s*rate));
    const end=nextWakeByStart.get(p.t_s)??overview.analysis?.timeline_duration_s??m.duration_s;
    return {kind:'sleep',label:`Sleep #${i+1}`,start_s:p.t_s,
      duration_s:end==null?null:Math.max(0,end-p.t_s),...valid,valid:!!valid};
  }):validSleep.map((p,i)=>({kind:'sleep',label:`Sleep #${i+1}`,valid:true,...p}));
  const events=[...(m.events||[]),...(m.background_events||[])].sort((a,b)=>a.trigger_sample-b.trigger_sample||a.sequence-b.sequence);
  let previousWake=null;
  const rows=events.map(e=>{
    const background=e.event_kind==='background';
    const start=e.trigger_sample!=null?e.trigger_sample/rate:e.trigger_at&&m.started_at?(new Date(e.trigger_at)-new Date(m.started_at))/1000:0;
    const period=!background&&previousWake!=null?start-previousWake:null;
    if(!background)previousWake=start;
    const valid=background?null:validWake.get(e.sequence);
    return {kind:background?'background':'wake',label:`${background?'Hintergrund':'Wake'} #${Number(e.sequence)}`,
      id:e.id,start_s:start,period_s:period,duration_s:e.duration_us==null?null:e.duration_us/1e6,
      mean_ua:e.mean_ua,peak_ua:e.peak_ua,charge_uc:e.charge_uc,
      energy_uwh:e.charge_uc==null||m.voltage_mv==null?null:e.charge_uc*m.voltage_mv/1000/3600,
      digital_mask_seen:e.digital_mask_seen,...valid,valid:!!valid};
  });
  rows.push(...sleepPhases);
  rows.sort((a,b)=>a.start_s-b.start_s);
  $('wakeEventCount').textContent=`${sleepPhases.length} Sleep / ${(m.events||[]).length} Wake / ${(m.background_events||[]).length} Hintergrund`;
  $('eventProtocol').open=false;
  $('eventRows').innerHTML=rows.map(p=>{
    const tone=p.kind==='sleep'?'detail-sleep':p.kind==='wake'?'detail-wake':'detail-neutral';
    const label=p.id!=null?`<button type="button" class="event-phase event-open ${tone}" aria-label="${escapeHtml(p.label)} öffnen">${escapeHtml(p.label)}</button>`:`<span class="event-phase ${tone}">${escapeHtml(p.label)}</span>`;
    const data=p.interpolated_samples?'Mit ergänzten Daten':p.valid?'Gültiger Zyklus':p.kind==='background'?'Im Sleep enthalten':'Ohne gültigen Zyklus';
    return `<tr${p.id!=null?` data-event="${Number(p.id)}"`:''}><td>${label}</td><td>${escapeHtml(fmtDuration(p.start_s))}</td><td>${escapeHtml(fmtDuration(p.period_s))}</td><td>${escapeHtml(fmtEventDuration(p.duration_s==null?null:p.duration_s*1e6))}</td><td>${escapeHtml(fmtCurrent(p.mean_ua))}</td><td>${escapeHtml(fmtCurrent(p.peak_ua))}</td><td>${escapeHtml(fmtCharge(p.charge_uc))}</td><td>${escapeHtml(fmtEnergy(p.energy_uwh))}</td><td>${p.digital_mask_seen==null?'—':`0x${Number(p.digital_mask_seen).toString(16).padStart(2,'0')}`}</td><td>${data}</td></tr>`;
  }).join('')||'<tr><td colspan="10">Keine Sleep- oder Wake-Phasen verfügbar.</td></tr>';
  document.querySelectorAll('#eventRows tr[data-event]').forEach(r=>r.addEventListener('click',()=>openEvent(Number(r.dataset.event))));
}
function renderMeasurementDetail(m,overview){
  renderSleepAnalysis(m.sleep_analysis);
  const a=overview.analysis||{};
  WakeAnalysisUI.render(m.cycle_energy||a.confirmed_cycles,'detail');
  $('detailStatus').textContent=({completed:'Abgeschlossen',failed:'Fehlgeschlagen',cancelled:'Abgebrochen',recording:'Aufzeichnung',scheduled:'Geplant',starting:'Startet'})[m.status]||m.status||'—';
  $('detailStatus').className=`status-badge ${['completed','failed','cancelled','recording','scheduled','starting'].includes(m.status)?m.status:''}`;
  $('detailName').textContent=m.name;
  renderDetectionMetadata(m);
  $('detailError').textContent=m.error?`Abbruchgrund: ${m.error}`:'';
  $('detailError').classList.toggle('hidden',!m.error);
  $('detailMeta').textContent=[m.project,m.device,m.serial_number?`SN: ${m.serial_number}`:'',m.firmware?`FW: ${m.firmware}`:'',m.hardware_version?`HW: ${m.hardware_version}`:''].filter(Boolean).join(' · ')||'Ohne Metadaten';
  $('detailDuration').textContent=fmtDuration(m.duration_s);
  $('detailStarted').textContent=fmtDate(m.started_at);
  $('detailSleep').textContent=fmtCurrent(['threshold','spectral_compare'].includes(m.settings?.detection_mode)?a.average_sleep_current_ua:(a.average_sleep_current_ua ?? m.sleep_current_ua));
  $('detailWakeCurrent').textContent=fmtCurrent(a.average_wake_current_ua);
  $('detailPeriod').textContent=fmtDuration(a.average_period_s);
  $('detailWakeDuration').textContent=fmtDuration(a.average_wake_duration_s);
  $('detailWake').textContent=Number(a.wake_count ?? m.wake_count ?? 0).toLocaleString('de-DE');
  $('detailDuty').textContent=fmtPercent(a.wake_duty_cycle_pct);
  $('detailCharge').textContent=fmtCharge(m.total_charge_uc);
  $('detailEnergy').textContent=fmtEnergy(m.energy_uwh);

  const ppk=m.ppk2_config||{};
  const contextGroups=[['Projekt & Gerät',[
    ['Projekt',m.project||'—'],
    ['Gerät',m.device||'—'],
    ['Seriennummer',m.serial_number||'—'],
    ['Firmware-Version',m.firmware||'—'],
    ['Hardware-Version',m.hardware_version||'—'],
    ['Notiz',m.notes||'—']
  ]],['Aufzeichnung',[
    ['PPK2 ID',m.ppk2_id||'—'],
    ['COM-Port',m.port||'—'],
    ['USB-Seriennummer',ppk.serial||'—'],
    ['PPK2 HW / IA',[ppk.hw,ppk.ia].filter(Boolean).join(' / ')||'—'],
    ['Modus',m.meter_mode||'—'],
    ['Versorgung',`${(m.voltage_mv/1000).toFixed(3)} V`],
    ['Abtastrate',`${(m.sample_rate_hz/1000).toFixed(0)} kS/s`]
  ]],['Messqualität & Statistik',[
    ['Ø Gesamtstrom',fmtCurrent(m.average_current_ua)],
    ['Spitzenstrom',fmtCurrent(m.peak_current_ua)],
    ['Perioden σ',fmtDuration(a.period_std_s)],
    ['Datenabdeckung',`${Number(m.data_coverage_pct??100).toFixed(6)} %`],
    ['Verlorene Samples',Number(m.detected_lost_samples||0).toLocaleString('de-DE')]
  ]]];
  $('metadataList').innerHTML=contextGroups.map(([title,fields])=>`<section><h4>${escapeHtml(title)}</h4><dl>${fields.map(([k,v])=>`<div><dt>${escapeHtml(k)}</dt><dd>${escapeHtml(v)}</dd></div>`).join('')}</dl></section>`).join('');
  $('ppkConfigDetails').classList.remove('hidden');
  $('ppkConfigJson').textContent=JSON.stringify(ppk,null,2);

  renderEventProtocol(m,overview);

  $('exportJson').href=`/api/measurements/${m.id}/export/json`;
  $('exportPdf').href=`/api/measurements/${m.id}/export/pdf`;
  $('exportCsv').href=`/api/measurements/${m.id}/export/csv`;
  $('exportBundle').href=`/api/measurements/${m.id}/export/bundle`;
  showHistoryOverview();
  renderOverviewChart(overview,{force:true});
}

function queueHistoryPlot(renderToken,mode,renderFn){
  state.historyPlotPromise=Promise.resolve(state.historyPlotPromise).catch(()=>{}).then(async()=>{
    if(renderToken!==state.historyRenderToken||state.historyMode!==mode)return;
    await renderFn();
  });
  return state.historyPlotPromise;
}

function selectEventRow(id=null){
  document.querySelectorAll('#eventRows tr[data-event]').forEach(row=>row.classList.toggle('selected',id!=null&&Number(row.dataset.event)===Number(id)));
}
function showHistoryOverview(){
  hideCurrentTooltip();
  $('historyOverviewChart').blur?.();
  $('historyOverviewCard').classList.remove('hidden');
  $('wakeEventCard').classList.add('hidden');
  selectEventRow(null);
}
function showWakeEventPanel(){
  $('historyOverviewChart').blur?.();
  $('historyOverviewCard').classList.add('hidden');
  $('wakeEventCard').classList.remove('hidden');
}

function bindOverviewScrollZoom(el){
  if(el.dataset.scrollZoomBound==='1')return;
  el.dataset.scrollZoomBound='1';
  const hint=$('historyOverviewZoomHint');
  const update=()=>{
    const active=document.activeElement===el;
    el.classList.toggle('scroll-zoom-active',active);
    hint.textContent=active?'Scroll-Zoom aktiv · Außerhalb klicken oder Esc zum Beenden.':'Chart anklicken, um mit dem Mausrad zu zoomen.';
  };
  el.addEventListener('wheel',ev=>{
    // Let the browser scroll the page while keeping Plotly from handling the wheel.
    if(document.activeElement!==el)ev.stopImmediatePropagation();
  },{capture:true,passive:true});
  el.addEventListener('pointerdown',()=>el.focus({preventScroll:true}));
  el.addEventListener('focus',update);
  el.addEventListener('blur',update);
  el.addEventListener('keydown',ev=>{if(ev.key==='Escape')el.blur()});
  document.addEventListener('pointerdown',ev=>{if(document.activeElement===el&&!el.contains(ev.target))el.blur()});
  update();
}

function renderOverviewChart(overview,{force=false}={}){
  if((state.historyMode==='event'||state.historyMode==='event-loading')&&!force)return;
  state.historyMode='overview';
  state.currentEvent=null;
  showHistoryOverview();
  const renderToken=++state.historyRenderToken;
  const timeline=overview.timeline||[];
  const analysis=overview.analysis||{};
  const traces=[];

  if(timeline.length){
    traces.push({
      x:timeline.map(p=>p.timestamp||p.t_s),
      y:timeline.map(p=>Number(p.value)),
      customdata:timeline.map(p=>[p.state,p.event_id,p.t_s]),
      type:'scatter',mode:'lines',name:'Zustand',
      line:{width:2.4,color:'#65d98b',shape:'hv'},
      hovertemplate:'%{x}<br>Zustand: %{customdata[0]}<extra></extra>'
    });
  }

  const events=overview.events||[];
  if(events.length){
    traces.push({
      x:events.map(e=>e.trigger_at),y:events.map(()=>1),
      customdata:events.map(e=>[Number(e.id),Number(e.sequence),Number(e.duration_us),Number(e.mean_ua),Number(e.peak_ua)]),
      type:'scatter',mode:'markers',name:'Wake event',
      marker:{symbol:'triangle-up',size:10,color:'#f5bd5b',line:{width:1,color:'#201707'}},
      hovertemplate:'Wake #%{customdata[1]}<br>%{x}<br>Dauer %{customdata[2]:.0f} µs<br>Ø %{customdata[3]:.3f} µA<br>Peak %{customdata[4]:.3f} µA<extra></extra>'
    });
  }

  const sleepStarts=(overview.state_markers||[]).filter(m=>!String(m.kind||'').endsWith('validated'));
  // Wake events already mark their start and open the recorded raw data.
  const sleepMarkers=sleepStarts.filter(m=>m.kind!=='wake_start');
  if(sleepMarkers.length)traces.push(sleepStartTrace(sleepMarkers,{factor:1},true));
  const markers=(overview.markers||[]).filter(m=>!String(m.kind||'').endsWith('validated'));
  if(markers.length){
    traces.push({
      x:markers.map(m=>m.at),y:markers.map(()=>.5),text:markers.map(m=>m.label),
      mode:'markers',type:'scatter',name:'Marker',marker:{symbol:'diamond',size:9,color:'#6ea8fe'},
      hovertemplate:'%{x}<br>%{text}<extra></extra>'
    });
  }

  $('historyOverviewTitle').textContent='Sleep / Wake-Verlauf';
  const period=analysis.average_period_s==null?'—':fmtDuration(analysis.average_period_s);
  $('historyOverviewSubtitle').textContent=`Exakte Zustandswechsel · Ø Periode ${period} · Wake-Marker öffnen 100-kS/s-Rohdaten`;

  const layout={
    paper_bgcolor:'transparent',plot_bgcolor:'transparent',margin:{l:92,r:24,t:10,b:68},
    xaxis:{title:{text:'Zeit',standoff:14},gridcolor:'#222a31',color:'#aeb8c2',zeroline:false},
    yaxis:{title:{text:'Zustand',standoff:14},range:[-.18,1.18],tickmode:'array',tickvals:[0,1],ticktext:['Sleep','Wake'],gridcolor:'#222a31',color:'#aeb8c2',zeroline:false,fixedrange:false},
    font:{family:'Segoe UI, Inter, system-ui, sans-serif',color:'#c8d0d8',size:11},hovermode:'closest',dragmode:'zoom',legend:{orientation:'h',y:1.08},
    shapes:sleepStartShapes(sleepStarts,true),showlegend:false,
    uirevision:`states-${state.currentMeasurement?.id}`
  };

  queueHistoryPlot(renderToken,'overview',async()=>{
    const el=$('historyOverviewChart');
    bindOverviewScrollZoom(el);
    el.removeAllListeners?.('plotly_click');
    await Plotly.react(el,traces,layout,plotConfig);
    if(renderToken!==state.historyRenderToken||state.historyMode!=='overview')return;
    el.removeAllListeners?.('plotly_click');
    el.on('plotly_click',ev=>{
      const point=ev.points?.[0];
      const cd=point?.customdata;
      if(point?.data?.name!=='Wake event')return;
      const eventId=Array.isArray(cd)?Number(cd[0]||0):0;
      if(eventId)openEvent(eventId);
    });
  }).catch(err=>{console.error('State history chart render failed',err);toast('Sleep/Wake-History konnte nicht gerendert werden.',true)});
}

async function openEvent(id){
  if(state.view!=='detail'||!state.currentMeasurement)return;
  cancelEventLoad();
  ++state.navigationToken;
  const measurementId=state.currentMeasurement.id;
  state.historyMode='event-loading';
  state.eventVisibleRange=null;hideCurrentTooltip();
  state.currentEvent=null;
  selectEventRow(id);
  showWakeEventPanel();
  const renderToken=++state.historyRenderToken;
  $('wakeEventTitle').textContent='Wake Event wird geladen …';
  $('wakeEventSubtitle').textContent=`Event ${id} · Übersicht wird geladen`;
  const chart=$('wakeEventChart');
  try{Plotly.purge(chart)}catch(_){}
  chart.innerHTML='';chart.classList.add('hidden');
  const status=$('wakeEventStatus');status.className='event-loading';status.textContent='Übersicht wird geladen …';
  try{
    const event=[...(state.currentMeasurement.events||[]),...(state.currentMeasurement.background_events||[])].find(e=>Number(e.id)===Number(id));
    const range=eventFullRange(event);
    const rate=state.currentMeasurement.sample_rate_hz||100000;
    const count=Math.round((range[1]-range[0])*rate/1e6)+1;
    $('eventPointBudget').value=Math.max(1000,Math.min(50000,Math.round(count/100/1000)*1000));
    const d=await loadEventView(measurementId,id,range,renderToken);
    if(!d)return;
    d.fullRange=d.t_us.length?[d.t_us[0],d.t_us[d.t_us.length-1]]:range;
    state.eventVisibleRange=d.fullRange;
    if(state.currentMeasurement?.id!==measurementId||renderToken!==state.historyRenderToken)return;
    state.currentEvent=d;
    state.historyMode='event';
    await renderEventChart(d,renderToken);
  }catch(e){
    if(renderToken===state.historyRenderToken){
      state.historyMode='event-error';
      state.currentEvent=null;
      $('wakeEventTitle').textContent='Wake Event konnte nicht geladen werden';
      $('wakeEventSubtitle').textContent=e.message;
      try{Plotly.purge(chart)}catch(_){}
      chart.classList.add('hidden');status.className='event-load-error';
      status.innerHTML=`<strong>Rohdaten nicht verfügbar.</strong><br>${escapeHtml(e.message)}<br><small>Die Zustandsübersicht und Event-Metadaten bleiben erhalten.</small>`;
      toast(`Wake-Event konnte nicht geladen werden: ${e.message}`,true);
    }
  }
}

function digitalTransitionTrace(t,digital,bit){const x=[],y=[];let last=null;for(let i=0;i<digital.length;i++){const v=(digital[i]>>bit)&1;if(last===null||v!==last||i===digital.length-1){if(last!==null&&v!==last){x.push(t[i]);y.push(last+bit*1.2)}x.push(t[i]);y.push(v+bit*1.2);last=v}}return{x,y,type:'scatter',mode:'lines',line:{width:1},name:`D${bit}`,yaxis:'y2'}}
function eventFullRange(event){
  const m=state.currentMeasurement,rate=m?.sample_rate_hz||100000;
  const pre=(m?.settings?.pre_trigger_ms??1000)*1000;
  const post=(m?.settings?.post_trigger_ms??1000)*1000;
  const start=Number.isFinite(event?.start_sample)&&Number.isFinite(event?.trigger_sample)?(event.start_sample-event.trigger_sample)*1e6/rate:-pre;
  const end=Number.isFinite(event?.end_sample)&&Number.isFinite(event?.trigger_sample)?(event.end_sample-event.trigger_sample)*1e6/rate:(event?.duration_us||0);
  return [start,end+post];
}
const eventViewCache=new Map();
let eventRequest=null;
function cancelEventLoad(){
  clearTimeout(state.eventZoomTimer);
  eventRequest?.abort();eventRequest=null;
}
function eventPointBudget(){return Math.max(100,Math.min(50000,Math.round(Number($('eventPointBudget').value)||25000)))}
function eventRawUrl(measurementId,eventId,range){
  const query=new URLSearchParams({max_points:String(eventPointBudget()),adaptive_view:'true',start_s:String(range[0]/1e6),end_s:String(range[1]/1e6)});
  return `/api/measurements/${measurementId}/events/${eventId}?${query}`;
}
async function loadEventView(measurementId,eventId,range,token){
  cancelEventLoad();
  const url=eventRawUrl(measurementId,eventId,range);
  if(eventViewCache.has(url)){
    const data=eventViewCache.get(url);eventViewCache.delete(url);eventViewCache.set(url,data);return data;
  }
  const request=new AbortController();eventRequest=request;
  try{
    $('wakeEventStatus').className='event-loading';$('wakeEventStatus').textContent='Details werden geladen …';
    let data;
    for(let attempt=0;attempt<4;attempt++){
      if(request.signal.aborted)throw new DOMException('Cancelled','AbortError');
      try{data=await api(url,{signal:request.signal});break}
      catch(error){
        if(error.name==='AbortError'||attempt===3||!error.message.includes('läuft bereits'))throw error;
        await new Promise(resolve=>setTimeout(resolve,150));
      }
    }
    if(token!==state.historyRenderToken||state.view!=='detail'||state.currentMeasurement?.id!==measurementId)return null;
    eventViewCache.set(url,data);
    if(eventViewCache.size>8)eventViewCache.delete(eventViewCache.keys().next().value);
    return data;
  }finally{if(eventRequest===request)eventRequest=null}
}
async function reloadEventRange(d,range){
  const measurementId=state.currentMeasurement?.id;if(!measurementId||state.currentEvent!==d)return;
  const token=++state.historyRenderToken;
  range=range||d.fullRange||eventFullRange(d.event);
  const next=await loadEventView(measurementId,d.event.id,range,token);
  if(!next||token!==state.historyRenderToken)return;
  next.fullRange=d.fullRange;
  state.eventVisibleRange=range;
  await renderEventChart(next,token);
}
$('eventPointBudget').addEventListener('change',()=>{
  if(!$('eventPointBudget').checkValidity())return;
  if(state.currentEvent)reloadEventRange(state.currentEvent,state.eventVisibleRange).catch(showEventRenderError);
});
const eventSmoothingCache=new WeakMap();
function median(values){const sorted=values.slice().sort((a,b)=>a-b),mid=Math.floor(sorted.length/2);return sorted.length?(sorted.length%2?sorted[mid]:(sorted[mid-1]+sorted[mid])/2):0}
function smoothEventCurrent(values,times,strength){
  const sigmaSamples={light:2,medium:5,strong:10}[strength];
  const result=values.slice();
  if(!sigmaSamples||values.length<5||times.length!==values.length)return result;
  // Estimate noise from normalized linear-interpolation residuals using MAD.
  // This discounts slopes, supports uneven timestamps and tolerates signal edges.
  const intervals=[],differences=[],stride=Math.max(1,Math.ceil(values.length/8192));
  for(let i=1;i<values.length;i+=stride){
    const dt=times[i]-times[i-1];
    if(Number.isFinite(dt)&&dt>0)intervals.push(dt);
  }
  const step=median(intervals);
  if(!(step>0))return result;
  for(let i=1;i<values.length-1;i+=stride){
    const a=times[i]-times[i-1],b=times[i+1]-times[i];
    if([values[i-1],values[i],values[i+1]].every(Number.isFinite)&&a>0&&b>0&&a<=step*3&&b<=step*3){
      const left=b/(a+b),right=a/(a+b);
      differences.push((values[i]-left*values[i-1]-right*values[i+1])/Math.sqrt(1+left*left+right*right));
    }
  }
  if(differences.length<3)return result;
  const center=median(differences),noise=median(differences.map(v=>Math.abs(v-center)))*1.4826;
  if(!(noise>0))return result;
  const rangeSigma=noise*2.5,timeSigma=step*sigmaSamples,radius=Math.ceil(sigmaSamples*3);
  // Do not blend across missing samples or large timestamp gaps.
  const segments=new Int32Array(values.length);let segment=0;
  for(let i=0;i<values.length;i++){
    if(i&&(!Number.isFinite(values[i-1])||!Number.isFinite(values[i])||!Number.isFinite(times[i-1])||!Number.isFinite(times[i])||times[i]<=times[i-1]||times[i]-times[i-1]>step*3))++segment;
    segments[i]=segment;
  }
  for(let i=0;i<values.length;i++){
    if(!Number.isFinite(values[i])||!Number.isFinite(times[i]))continue;
    let weighted=0,total=0;
    for(let j=Math.max(0,i-radius);j<=Math.min(values.length-1,i+radius);j++){
      if(segments[j]!==segments[i]||!Number.isFinite(values[j]))continue;
      const dt=(times[j]-times[i])/timeSigma,dv=(values[j]-values[i])/rangeSigma;
      const weight=Math.exp(-.5*(dt*dt+dv*dv));
      weighted+=weight*values[j];total+=weight;
    }
    if(total>0)result[i]=weighted/total;
  }
  return result;
}
function eventDisplayCurrent(d){
  const actual=d.current_ua||[];
  if(d.aggregated||d.downsampled||state.historySmoothing==='off')return actual;
  let cached=eventSmoothingCache.get(d);
  if(!cached){cached={};eventSmoothingCache.set(d,cached)}
  return cached[state.historySmoothing]||(cached[state.historySmoothing]=smoothEventCurrent(actual,d.t_us||[],state.historySmoothing));
}
function renderEventChart(d,renderToken=++state.historyRenderToken){
  state.historyMode='event';
  state.currentEvent=d;
  const e=d.event,actual=d.current_ua||[],display=eventDisplayCurrent(d),smoothed=!d.aggregated&&!d.downsampled&&state.historySmoothing!=='off';
  const unit=eventCurrentUnit(d);
  const y=display.map(v=>v==null?null:(state.historyScale==='log'?Math.max(.001,v):v)*unit.factor);
  // Keep the bounded event waveform and its markers in one SVG layer so rings
  // stay smooth and appear above the current trace.
  const traces=[{x:d.t_us||[],y,customdata:smoothed?actual.map((v,i)=>[v,display[i]]):actual,type:'scatter',mode:'lines',name:d.aggregated?'Block-Minima/Maxima':(smoothed?'Current · geglättet':'Current'),line:{width:1.1,color:'#65d98b'},hoverinfo:'none',yaxis:'y'}];
  for(let bit=0;bit<8;bit++)if(!d.aggregated&&!d.downsampled&&(Number(e.digital_mask_seen||0)&(1<<bit))!==0)traces.push({...digitalTransitionTrace(d.t_us||[],d.digital||[],bit),type:'scatter'});
  for(const trace of traces)trace.hoverinfo='none';
  showWakeEventPanel();
  selectEventRow(e.id);
  $('wakeEventTitle').textContent=`Wake Event #${e.sequence}`;
  $('wakeEventSubtitle').innerHTML=`${escapeHtml(fmtDate(e.trigger_at))} · ${escapeHtml(fmtEventDuration(e.duration_us))} · Ø ${escapeHtml(fmtCurrent(e.mean_ua))} · Peak ${escapeHtml(fmtCurrent(e.peak_ua))} · <a href="/api/measurements/${state.currentMeasurement.id}/events/${e.id}/csv">Rohdaten CSV ↓</a>`;
  if(smoothed)$('wakeEventSubtitle').innerHTML+=` · Glättung: ${{light:'leicht',medium:'mittel',strong:'stark'}[state.historySmoothing]} (nur Darstellung)`;
  const displayHint=d.display_notice||(d.aggregated||d.downsampled?'Die Ansicht zeigt eine Min/Max-Auswahl mit originalen Sample-Zeiten. Beim Zoomen werden feinere Daten bis zu den Rohsamples nachgeladen.':'Alle Rohsamples im sichtbaren Bereich werden angezeigt.');
  $('wakeEventSubtitle').innerHTML+=` · ${(d.t_us||[]).length.toLocaleString('de-DE')} Punkte <span class="field-help event-display-help"><button type="button" class="info-button" aria-label="Information zur Datendarstellung" aria-describedby="eventDisplayHint">i</button><span id="eventDisplayHint" class="field-tooltip" role="tooltip">${escapeHtml(displayHint)}</span></span>`;
  const hasDigital=traces.length>1;
  const eventMarkers=d.state_markers||[];
  const markerKinds=[...new Set(eventMarkers.map(m=>m.kind))];
  for(const kind of markerKinds){
    const markers=eventMarkers.filter(m=>m.kind===kind),color=stateMarkerColor(markers[0]);
    const confirmed=kind.endsWith('validated');
    const name={wake_start:'Wake-Start',wake_validated:'Wake bestätigt',sleep_start:'Sleep-Start',sleep_validated:'Sleep bestätigt'}[kind]||stateMarkerLabel(markers[0]);
    traces.push({
      x:markers.map(m=>m.t_us),
      y:markers.map(m=>(state.historyScale==='log'?Math.max(.001,m.current_ua):m.current_ua)*unit.factor),
      customdata:markers.map(()=>name),
      text:markers.map(()=>kind==='wake_start'||kind==='sleep_start'?name:''),
      textposition:kind.includes('wake')?'top right':'top left',
      textfont:{size:11,color},
      type:'scatter',mode:'markers+text',name,legendgroup:kind,meta:{role:'state-marker'},
      marker:{size:10,symbol:confirmed?'circle-open':'circle',color,line:{width:confirmed?2:1,color:confirmed?color:'#101419'}},
      hoverinfo:'none'
    });
  }
  const visibleRange=state.eventVisibleRange;
  const fullRange=d.fullRange;
  const atFullRange=visibleRange&&fullRange&&visibleRange[0]===fullRange[0]&&visibleRange[1]===fullRange[1];
  const padding=atFullRange?Math.max(d.sample_period_us||1,(fullRange[1]-fullRange[0])*.025):0;
  const chartRange=atFullRange?[fullRange[0]-padding,fullRange[1]+padding]:visibleRange;
  const layout={
    paper_bgcolor:'transparent',plot_bgcolor:'transparent',margin:{l:82,r:58,t:60,b:70},
    xaxis:{title:{text:'Zeit relativ zum Trigger [µs]',standoff:14},gridcolor:'#222a31',color:'#aeb8c2',zeroline:true,zerolinecolor:'#58626c',showspikes:false,...(chartRange?{range:chartRange,autorange:false}:{})},
    yaxis:{title:{text:`Strom [${unit.label}]`,standoff:14},type:state.historyScale,gridcolor:'#222a31',color:'#aeb8c2',zeroline:false,showspikes:false,domain:hasDigital?[.28,1]:[0,1],uirevision:`event-y-${e.id}-${state.historyScale}-${unit.label}`},
    yaxis2:{title:{text:'Digital'},gridcolor:'#1b2228',color:'#8d99a5',domain:[0,.18],tickmode:'array',tickvals:[]},
    font:{family:'Segoe UI, Inter, system-ui, sans-serif',color:'#c8d0d8',size:11},hovermode:'closest',hoverlabel:{bgcolor:'#171d23',bordercolor:'#48535f',font:{color:'#edf2f6'}},dragmode:'zoom',showlegend:true,legend:{orientation:'h',x:0,xanchor:'left',y:1.02,yanchor:'bottom'},
    shapes:eventMarkers.length?eventMarkers.map(m=>({type:'line',x0:m.t_us,x1:m.t_us,y0:0,y1:1,yref:'paper',legendgroup:m.kind,opacity:.3,line:{color:stateMarkerColor(m),width:1,dash:m.kind.endsWith('validated')?'dot':'solid'}})):[{type:'line',x0:0,x1:0,y0:0,y1:1,yref:'paper',opacity:.3,line:{color:'#f5bd5b',width:1,dash:'dot'}}],
    uirevision:`event-${state.currentMeasurement?.id}-${e.id}-${state.historyScale}`
  };
  return queueHistoryPlot(renderToken,'event',async()=>{
    const el=$('wakeEventChart');
    hideCurrentTooltip();el.classList.remove('hidden');
    ++state.eventPlotPending;
    try{await Plotly.react(el,traces,layout,plotConfig)}finally{--state.eventPlotPending;}
    if(renderToken!==state.historyRenderToken||state.historyMode!=='event')return;
    $('wakeEventStatus').className='event-loading hidden';$('wakeEventStatus').textContent='';
    bindEventChartInteractions(d,unit);
  });
}
document.querySelectorAll('[data-history-scale]').forEach(b=>b.addEventListener('click',()=>{state.historyScale=b.dataset.historyScale;document.querySelectorAll('[data-history-scale]').forEach(x=>x.classList.toggle('active',x===b));if(state.currentEvent){const token=++state.historyRenderToken;renderEventChart(state.currentEvent,token).catch(showEventRenderError)}}));
$('historySmoothing').addEventListener('change',e=>{
  state.historySmoothing=e.target.value;
  if(state.currentEvent)renderEventChart(state.currentEvent,++state.historyRenderToken).catch(showEventRenderError);
});
$('backToOverview').addEventListener('click',()=>{cancelEventLoad();state.historyMode='overview';state.currentEvent=null;++state.historyRenderToken;showHistoryOverview()});
$('backToMeasurements').addEventListener('click',()=>{++state.historyRenderToken;state.currentEvent=null;selectEventRow(null);setView('measurements');loadMeasurements()});
function closeExportMenu(){
  document.querySelectorAll('.dropdown').forEach(d=>d.classList.remove('open'));
  $('exportBtn').ariaExpanded='false';
}
$('exportBtn').addEventListener('click',()=>{
  const open=$('exportBtn').parentElement.classList.toggle('open');
  $('exportBtn').ariaExpanded=String(open);
});
document.addEventListener('click',e=>{if(!e.target.closest('.dropdown')||e.target.closest('.dropdown-menu a'))closeExportMenu()});
document.addEventListener('keydown',e=>{if(e.key==='Escape'&&$('exportBtn').ariaExpanded==='true'){closeExportMenu();$('exportBtn').focus()}});
$('editMeasurement').addEventListener('click',()=>{const m=state.currentMeasurement;if(!m)return;const f=$('editForm');for(const k of ['name','project','device','serial_number','firmware','hardware_version','notes'])f.elements[k].value=m[k]||'';$('editDialog').showModal()});document.querySelectorAll('[data-close-edit]').forEach(b=>b.addEventListener('click',()=>$('editDialog').close()));$('editForm').addEventListener('submit',async e=>{e.preventDefault();const f=new FormData(e.target),payload=Object.fromEntries(['name','project','device','serial_number','firmware','hardware_version','notes'].map(k=>[k,f.get(k)]));try{await api(`/api/measurements/${state.currentMeasurement.id}`,{method:'PATCH',body:JSON.stringify(payload)});$('editDialog').close();toast('Metadaten gespeichert');await openMeasurement(state.currentMeasurement.id);await loadMeasurements()}catch(err){toast(err.message,true)}});$('deleteMeasurement').addEventListener('click',async()=>{const m=state.currentMeasurement;if(!m||!confirm(`Messung „${m.name}“ wirklich löschen?`))return;try{await api(`/api/measurements/${m.id}`,{method:'DELETE'});toast('Messung gelöscht');await loadMeasurements();setView('measurements')}catch(e){toast(e.message,true)}});
document.querySelectorAll('.nav').forEach(b=>b.addEventListener('click',()=>{const v=b.dataset.view;closeSessionWs();setView(v);if(v==='measurements')loadMeasurements();if(v==='battery')BatteryLifeUI.open()}));
$('batteryFromMeasurement').addEventListener('click',()=>{const id=state.currentMeasurement?.id;setView('battery');BatteryLifeUI.open(id)});

async function init(){setDialogDefaults();renderDevices(state.devices);startDeviceDiscovery();await Promise.allSettled([loadMeasurements(),api('/api/live').then(renderLiveOverview),api('/api/version').then(({version})=>{$('appVersion').textContent=version})]);connectOverview()}
init();

function renderThresholdPreview(){
  const f=$('measurementForm').elements,n=k=>Math.max(0,(f[k].dataset.currentUnit?CurrentInputs.ua(f[k]):f[k].dataset.timeUnit?TimeInputs.value(f[k]):Number(f[k].value))||0);
  const pre=n('pre_trigger_ms')/1000,post=n('post_trigger_ms')/1000,wh=n('wake_min_ms')/1000,sh=n('sleep_min_s');
  const wake=pre+.5,thresholdEdge=wake+1.4,validated=thresholdEdge+wh,sleep=Math.max(wake+2,validated+.5),end=Math.max(sleep+post,sleep+sh)+.5,x=t=>30+t/end*640;
  const line=(t,color,dash='')=>`<line x1="${x(t)}" x2="${x(t)}" y1="20" y2="125" stroke="${color}" opacity=".4" stroke-dasharray="${dash}"/>`;
  const dot=(t,y,color,open=false,label='')=>`<circle cx="${x(t)}" cy="${y}" r="4" stroke="${color}" fill="${open?'none':color}"><title>${label}</title></circle>`;
  const area=(start,stop,color)=>`<rect x="${x(start)}" y="20" width="${x(stop)-x(start)}" height="105" fill="${color}" opacity=".16"/>`;
  const number=value=>value.toLocaleString('de-DE',{maximumFractionDigits:6});
  $('wakeValidationLegend').textContent=`Wake bestätigt: ${TimeInputs.format(f.wake_min_ms)} über Schwelle`;
  $('sleepValidationLegend').textContent=`Sleep bestätigt: ${TimeInputs.format(f.sleep_min_s)} unter Schwelle`;
  $('preTriggerLegend').textContent=`Vorlauf: ${TimeInputs.format(f.pre_trigger_ms)} vor Wake-Beginn`;
  $('postTriggerLegend').textContent=`Nachlauf: ${TimeInputs.format(f.post_trigger_ms)} ab Sleep-Beginn`;
  $('thresholdPreview').innerHTML=`${area(wake-pre,wake,'#60a5fa')}${area(wake,sleep,'#65d98b')}${area(sleep,sleep+post,'#a78bfa')}
    <line x1="30" x2="670" y1="95" y2="95" stroke="#60a5fa" stroke-dasharray="4 4"/><line x1="30" x2="670" y1="50" y2="50" stroke="#f5bd5b" stroke-dasharray="4 4"/>
    <path d="M30 110 H${x(wake)} Q${x(wake+.5)} 110 ${x(wake+.7)} 80 V60 H${x(wake+1)} V70 H${x(thresholdEdge)} V30 H${x(sleep)} V110 H670" stroke="#65d98b" stroke-width="2" fill="none"/>
    ${line(wake,'#f5bd5b')}${line(validated,'#f5bd5b','3 3')}${line(sleep,'#60a5fa')}${line(sleep+sh,'#60a5fa','3 3')}
    ${dot(wake,110,'#f5bd5b',false,'Wake-Start: erste Abweichung vom gelernten Sleep-Muster. Vorlauf davor.')}${dot(validated,30,'#f5bd5b',true,'Wake bestätigt: Wake-Schwelle für Mindestdauer überschritten.')}${dot(sleep,110,'#60a5fa',false,'Sleep-Start: Nachlauf danach.')}${dot(sleep+sh,110,'#60a5fa',true,'Sleep bestätigt: Sleep-Schwelle für Mindestdauer unterschritten.')}
    <g fill="#c8d0d8" font-size="11"><text x="32" y="45">Wake &gt; ${number(n('wake_threshold_ua'))} µA</text><text x="32" y="90">Sleep &lt; ${number(n('sleep_threshold_ua'))} µA</text></g>`;
}
function updateEventSettings(){
  CurrentInputs.sync();TimeInputs.sync();
  const sleep=CurrentInputs.ua($('sleepThresholdUa')),wake=CurrentInputs.ua($('wakeThresholdUa'));
  $('wakeThresholdUa').setCustomValidity(wake<=sleep?'Wake muss höher als Sleep sein.':'');
  renderThresholdPreview();
}
CurrentInputs.install();
TimeInputs.install();
WakeAnalysisUI.install();
MeasurementPreview.install();
BatteryLifeUI.install();
$('measurementForm').addEventListener('input',updateEventSettings);
$('detectionMode').addEventListener('change',updateEventSettings);
updateEventSettings();
