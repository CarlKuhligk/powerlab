// Run with Node.js: node --test tests/test_ui_navigation.cjs
const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

function deferred(){let resolve;const promise=new Promise(r=>resolve=r);return {promise,resolve};}
function harness(){
  const elements=new Map(),timers=new Map(),requests=[],sockets=[],plots=[],downloads=[],revokedUrls=[];
  let timerId=0,now=10000;
  function element(id){
    if(!elements.has(id)){
      const classes=new Set(),listeners={};
      elements.set(id,{id,value:'',dataset:{},textContent:'',innerHTML:'',listeners,style:{},children:[],parentNode:null,closest:selector=>element(`${id}-${selector}`),getBoundingClientRect:()=>({width:240,height:80}),
        querySelector:selector=>element(`${id}-${selector}`),
        insertBefore(child,before){child.remove();const index=before?this.children.indexOf(before):this.children.length;this.children.splice(index,0,child);child.parentNode=this},
        remove(){if(this.parentNode){const children=this.parentNode.children;children.splice(children.indexOf(this),1);this.parentNode=null}},
        classList:{add:c=>classes.add(c),remove:c=>classes.delete(c),contains:c=>classes.has(c),toggle(c,on){if(on??!classes.has(c))classes.add(c);else classes.delete(c)}},
        addEventListener(name,fn){listeners[name]=fn},on(name,fn){listeners[name]=fn},removeAllListeners(name){delete listeners[name]}});
    }
    return elements.get(id);
  }
  const navs=['live','measurements'].map(name=>{const el=element(`nav-${name}`);el.dataset.view=name;return el});
  let createdElementId=0;
  const document={getElementById:element,addEventListener(){},body:{appendChild(){}},createElement(tag){if(tag==='article')return element(`article-${++createdElementId}`);const link={click(){downloads.push({href:link.href,download:link.download})},remove(){}};return link},querySelectorAll(selector){
    if(selector==='.nav')return navs;
    if(selector==='.view')return ['liveView','sessionView','measurementsView','detailView'].map(element);
    return [];
  },querySelector(selector){return navs.find(el=>selector.includes(`"${el.dataset.view}"`))}};
  class MockSocket{
    constructor(url){sockets.push(this);this.url=url;this.closed=false;}
    close(){this.closed=true;this.onclose?.();}
  }
  const context=vm.createContext({document,console,AbortController,URLSearchParams,URL:{createObjectURL:()=> 'blob:test',revokeObjectURL:url=>revokedUrls.push(url)},WebSocket:MockSocket,
    location:{protocol:'http:',host:'localhost'},Date:class extends Date{static now(){return now}},
    setTimeout(fn){const id=++timerId;timers.set(id,fn);return id},clearTimeout:id=>timers.delete(id),setInterval(){},
    fetch(url,options){const d=deferred();requests.push({url,options,resolve:data=>d.resolve({ok:true,status:200,json:async()=>data,blob:async()=>data}),reject:message=>d.resolve({ok:false,status:409,json:async()=>({detail:message})})});return d.promise},
    Plotly:{react:async(...args)=>{plots.push(args)},purge(){}},WakeAnalysisUI:{render(){}},confirm:()=>true,prompt:()=>null,
  });
  const source=fs.readFileSync(path.join(__dirname,'../app/static/app.js'),'utf8').replace(/^init\(\);\s*$/m,'').replace(/^CurrentInputs.install\(\);[\s\S]*$/m,'');
  vm.runInContext(`${source}\nglobalThis.ui={state,setView,openSession,openMeasurement,openEvent,connectSessionLive,refreshLiveSeries,renderLiveOverview,renderMeasurementRows,deleteSelectedMeasurements,exportSelectedMeasurements,smoothEventCurrent,updateScheduleFields,scheduleStopText,measurementPayload,renderDevices,startDeviceDiscovery};`,context);
  return {...context.ui,element,navs,timers,requests,sockets,plots,downloads,revokedUrls,context,advance:ms=>now+=ms};
}
function liveSession(h){
  h.state.sessionMeasurement={id:'running',status:'recording'};
  h.setView('session');h.connectSessionLive('running');
  h.state.lastLiveSnapshot={measurement_id:'running',running:true};
  return h.sockets.at(-1);
}
const settle=()=>new Promise(r=>setImmediate(r));

for(const status of ['recording','starting','scheduled']){
  test(`opening a ${status} measurement renders the session before live data arrives`,async()=>{
    const h=harness();
    const m={id:'session-test',name:'Test measurement',status,started_at:status==='recording'?'1970-01-01T00:00:00Z':null,
      scheduled_start_at:'2026-10-07T10:00:00Z',stop_mode:'manual',voltage_mv:3300,sample_rate_hz:100000};
    const pending=h.openSession(m.id);
    assert.equal(h.requests[0].url,`/api/measurements/${m.id}`);
    h.requests[0].resolve(m);await pending;
    assert.equal(h.element('toast').textContent,'');
    assert.equal(h.state.view,'session');
    assert.equal(h.element('sessionName').textContent,m.name);
    assert.ok(h.element('sessionSetup').innerHTML.includes('3.300 V'));
    assert.equal(h.element('sessionTimeMain').textContent,status==='scheduled'?'Geplant':status==='starting'?'Startet …':'10.0 s');
    assert.equal(h.element('sessionLiveArea').classList.contains('hidden'),status==='scheduled');
    assert.equal(h.sockets.length,status==='scheduled'?0:1);
    if(status!=='scheduled')assert.equal(h.sockets[0].url,`ws://localhost/ws/live/${m.id}`);
  });
}

test('device WebSocket pushes connection, removal and reconnection with a new COM port',()=>{
  const h=harness(),select=h.element('portSelect');h.startDeviceDiscovery();
  const ws=h.sockets[0],push=ppk2=>ws.onmessage({data:JSON.stringify({ppk2})});
  assert.equal(ws.url,'ws://localhost/ws/devices');
  push([]);assert.equal(h.element('deviceCount').textContent,0);
  assert.match(select.innerHTML,/Kein PPK2/);
  push([{port:'COM4',device_id:'KIT-A'}]);
  assert.equal(select.value,'COM4');assert.equal(h.element('deviceCount').textContent,1);
  push([{port:'COM7',device_id:'KIT-B'}]);
  assert.equal(select.value,'');assert.match(select.innerHTML,/nicht angeschlossen/);
  push([{port:'COM7',device_id:'KIT-B'},{port:'COM9',device_id:'KIT-A'}]);
  assert.equal(select.value,'COM9');assert.equal(h.element('deviceCount').textContent,2);
  assert.equal(h.requests.length,0);
});

test('device WebSocket preserves user selection, updates busy state and reconnects without HTTP polling',()=>{
  const h=harness(),select=h.element('portSelect'),intervals=[];
  h.context.setInterval=(fn,ms)=>intervals.push({fn,ms});
  h.startDeviceDiscovery();h.startDeviceDiscovery();assert.equal(h.sockets.length,1);
  const ws=h.sockets[0],ports=[{port:'COM4',device_id:'KIT-A'},{port:'COM7',device_id:'KIT-B'}];
  ws.onmessage({data:JSON.stringify({ppk2:ports})});select.value='COM7';
  ws.onmessage({data:JSON.stringify({ppk2:[{...ports[1],busy:true}]})});
  assert.equal(select.value,'COM7');assert.match(select.innerHTML,/AKTIV/);
  ws.close();assert.equal(select.value,'COM7');assert.equal(h.timers.size,1);
  const retry=[...h.timers.values()][0];retry();
  assert.equal(h.sockets.length,2);assert.equal(h.timers.size,0);
  ws.onmessage({data:JSON.stringify({ppk2:[]})});assert.equal(select.value,'COM7');
  h.sockets[1].onmessage({data:JSON.stringify({ppk2:[{port:'COM9',device_id:'KIT-B'}]})});
  assert.equal(select.value,'COM9');assert.equal(h.requests.length,0);assert.equal(intervals.length,0);
  h.sockets[1].onerror();assert.equal(h.sockets[1].closed,true);assert.equal(h.timers.size,1);
});

test('scheduler enables the event count only for counter modes and sends an integer target',()=>{
  const h=harness(),form=h.element('measurementForm');
  form.elements=new Proxy({}, {get:(target,key)=>h.element(key)});
  form.elements.start_mode.value='now';
  form.elements.stop_mode.value='wake_count';
  form.elements.event_count.value='5';
  h.context.FormData=class {constructor(form){this.form=form}get(key){return this.form.elements[key].value}};
  h.updateScheduleFields();
  assert.equal(h.element('eventCountLabel').hidden,false);
  assert.equal(h.element('eventCount').required,true);assert.equal(h.element('eventCount').disabled,false);
  assert.equal(h.element('durationValue').disabled,true);assert.equal(h.element('scheduledEndAt').disabled,true);
  let payload=h.measurementPayload();
  assert.equal(payload.event_count,5);assert.equal(payload.duration_s,null);assert.equal(payload.scheduled_end_at,null);
  assert.match(h.scheduleStopText(payload),/5 bestätigte Wakes → Sleep-Start/);
  form.elements.stop_mode.value='sleep_count';h.updateScheduleFields();
  payload=h.measurementPayload();assert.equal(payload.event_count,5);
  assert.match(h.element('eventCountHelp').textContent,/erste bestätigte Sleep/);
  assert.match(h.scheduleStopText(payload),/Sleeps → Wake-Start/);
  form.elements.stop_mode.value='manual';h.updateScheduleFields();
  assert.equal(h.element('eventCountLabel').hidden,true);assert.equal(h.element('eventCount').required,false);
  assert.equal(h.measurementPayload().event_count,null);
});

test('live updates preserve cards and their fields while advancing elapsed and remaining time',()=>{
  const h=harness(),start='2026-10-06T10:00:00Z';
  const run={measurement_id:'running',name:'Test',started_at:start,planned_end_at:'2026-10-06T11:00:00Z',state:'WAITING_SLEEP'};
  const render=time=>h.renderLiveOverview({server_time:time,running:[run],scheduled:[]});
  render('2026-10-06T10:01:00Z');
  const card=h.element('runningCards').children[0],elapsed=card.querySelector('[data-field="elapsed"]'),remaining=card.querySelector('[data-field="remaining"]');
  const phase=card.querySelector('[data-field="phase"]');
  assert.equal(card.querySelector('[data-field="status"]').textContent,'RECORDING');
  assert.equal(phase.textContent,'Sleep-Suche');assert.equal(phase.className,'status-badge search-state');
  assert.equal(elapsed.textContent,'1m 00s');assert.equal(remaining.textContent,'59m 00s');
  const click=card.listeners.click;
  run.state='SLEEP';run.name='<Test & Name>';
  render('2026-10-06T10:01:01Z');
  assert.equal(h.element('runningCards').children[0],card);
  assert.equal(card.querySelector('[data-field="elapsed"]'),elapsed);
  assert.equal(card.listeners.click,click);
  assert.equal(elapsed.textContent,'1m 01s');assert.equal(remaining.textContent,'58m 59s');
  assert.equal(card.querySelector('[data-field="status"]').textContent,'RECORDING');
  assert.equal(card.querySelector('[data-field="phase"]'),phase);
  assert.equal(phase.textContent,'Sleep');assert.equal(phase.className,'status-badge sleep-state');
  run.state='ACTIVE';render('2026-10-06T10:01:02Z');
  assert.equal(phase.textContent,'Wake');assert.equal(phase.className,'status-badge wake-state');
  assert.equal(h.element('runningCards').children[0],card);
  run.state='CALIBRATING';render('2026-10-06T10:01:03Z');
  assert.equal(phase.textContent,'Kalibrierung');
  run.state=undefined;render('2026-10-06T10:01:04Z');
  assert.equal(phase.className,'status-badge hidden');
  assert.equal(card.querySelector('[data-field="name"]').textContent,'<Test & Name>');
  render('2026-10-06T11:00:01Z');assert.equal(remaining.textContent,'0 s');
  run.planned_end_at=null;render('2026-10-06T11:00:02Z');
  assert.equal(card.querySelector('[data-field="remainingRow"]').classList.contains('hidden'),true);
  run.started_at=null;render('2026-10-06T11:00:03Z');assert.equal(elapsed.textContent,'—');
});

test('overview reconciles additions, ordering, removals and scheduled-to-running transitions',()=>{
  const h=harness(),a={id:'a',name:'Planned',stop_mode:'manual'},b={id:'b',name:'Other',stop_mode:'duration',requested_duration_s:60};
  h.renderLiveOverview({running:[],scheduled:[a,b]});
  const [first,second]=h.element('scheduledCards').children;
  h.renderLiveOverview({running:[],scheduled:[b,a]});
  assert.equal(h.element('scheduledCards').children[0],second);assert.equal(h.element('scheduledCards').children[1],first);
  a.name='Updated';h.renderLiveOverview({running:[{measurement_id:'b'}],scheduled:[a]});
  assert.equal(h.element('scheduledCards').children.length,1);assert.equal(h.element('scheduledCards').children[0],first);
  assert.equal(first.querySelector('[data-field="name"]').textContent,'Updated');
  assert.equal(h.element('runningCards').children[0].dataset.session,'b');
  h.renderLiveOverview(null);
  assert.equal(h.element('runningCards').children.length,0);assert.equal(h.element('scheduledCards').children.length,0);
});

test('large-event preview is visibly aggregated and never smooths or fabricates digital signals',async()=>{
  const h=harness();h.state.view='detail';h.state.currentMeasurement={id:'saved'};
  h.state.historySmoothing='strong';h.state.historyScale='linear';
  const pending=h.openEvent(7);
  h.requests[0].resolve({event:{id:7,sequence:1,digital_mask_seen:255},aggregated:true,
    display_notice:'Block-Minima/Maxima, Zeitpositionen angenähert',t_us:[0,1000000,2000000],current_ua:[4,1000,4],digital:[0,0,0]});
  await pending;
  assert.equal(h.plots.at(-1)[1].length,1);
  assert.equal(h.plots.at(-1)[1][0].name,'Block-Minima/Maxima');
  assert.deepEqual(Array.from(h.plots.at(-1)[1][0].y),[.004,1,.004]);
  assert.equal(h.element('wakeEventStatus').textContent,'');
  assert.ok(h.element('wakeEventStatus').className.includes('hidden'));
  assert.match(h.element('wakeEventSubtitle').innerHTML,/aria-describedby="eventDisplayHint"/);
  assert.match(h.element('wakeEventSubtitle').innerHTML,/Block-Minima\/Maxima, Zeitpositionen angenähert/);
  h.element('wakeEventChart').listeners.plotly_hover({points:[{curveNumber:0,pointNumber:1,x:1000000}],event:{clientX:100,clientY:200}});
  assert.match(h.element('currentChartTooltip').innerHTML,/Zeitposition angenähert/);
  assert.doesNotMatch(h.element('currentChartTooltip').innerHTML,/Rohwert|Geglättet/);
});

test('an aborted session shows the persisted failure and opens history',async()=>{
  const h=harness(),ws=liveSession(h);
  const pending=ws.onmessage({data:JSON.stringify({running:false})});
  h.requests[0].resolve({id:'running',status:'failed',error:'USB getrennt'});
  await pending;
  assert.equal(h.element('toast').textContent,'Messung abgebrochen: USB getrennt');
  assert.ok(h.element('toast').className.includes('error'));
  h.requests[1].resolve({id:'running',status:'failed',error:'USB getrennt',events:[]});
  h.requests[2].resolve({});await settle();
  assert.equal(h.state.view,'detail');
  assert.equal(h.element('detailError').textContent,'Abbruchgrund: USB getrennt');
  assert.equal(h.element('detailError').classList.contains('hidden'),false);
});

function noisySignal(count){
  let seed=12345;
  const uniform=()=>{seed=(Math.imul(seed,1664525)+1013904223)>>>0;return (seed+1)/4294967297};
  return Array.from({length:count},()=>Math.sqrt(-2*Math.log(uniform()))*Math.cos(2*Math.PI*uniform()));
}
function rms(values){return Math.sqrt(values.reduce((sum,v)=>sum+v*v,0)/values.length)}

test('adaptive smoothing reduces noise while retaining steps and narrow high peaks',()=>{
  const h=harness(),noise=noisySignal(2000),times=noise.map((_,i)=>i*10);
  const signal=noise.map((v,i)=>v+(i>=1000?100:10));signal[500]=250;
  const original=signal.slice(),output=h.smoothEventCurrent(signal,times,'medium');
  assert.deepEqual(signal,original);
  assert.ok(rms(output.slice(100,400).map(v=>v-10))<rms(noise.slice(100,400))*.55);
  assert.ok(Math.abs(output[500]-250)<.01);
  assert.ok(output[999]<15);assert.ok(output[1000]>95);
  const strong=h.smoothEventCurrent(signal,times,'strong'),light=h.smoothEventCurrent(signal,times,'light');
  assert.ok(rms(strong.slice(100,400).map(v=>v-10))<rms(light.slice(100,400).map(v=>v-10)));
});

test('smoothing keeps constants, short arrays and missing values and respects time gaps',()=>{
  const h=harness();
  assert.deepEqual(Array.from(h.smoothEventCurrent([5,5,5,5,5],[0,10,20,30,40],'strong')),[5,5,5,5,5]);
  assert.deepEqual(Array.from(h.smoothEventCurrent([1,2],[0,10],'medium')),[1,2]);
  const values=noisySignal(500),times=values.map((_,i)=>i*10);
  values[250]=null;
  const output=h.smoothEventCurrent(values,times,'strong');assert.equal(output[250],null);
  const withGap=noisySignal(600),gapTimes=withGap.map((_,i)=>i*10+(i>=300?10000:0));
  const first=h.smoothEventCurrent(withGap,gapTimes,'strong');
  const changed=withGap.map((v,i)=>i>=300?v+1000:v),second=h.smoothEventCurrent(changed,gapTimes,'strong');
  assert.ok(Math.abs(first[299]-second[299])<1e-8);
});

test('smoothing supports uneven timestamps without mistaking a slope for noise',()=>{
  const h=harness(),noise=noisySignal(1000),times=[];let t=0;
  for(let i=0;i<noise.length;i++){times.push(t);t+=i%2?10:20}
  const trend=times.map(t=>10+t*.0001),values=trend.map((v,i)=>v+noise[i]);
  const output=h.smoothEventCurrent(values,times,'medium');
  assert.ok(rms(output.slice(50,950).map((v,i)=>v-trend[i+50]))<rms(noise.slice(50,950))*.55);
});

test('smoothing control renders filtered values, exposes raw hover values and preserves zoom revision',async()=>{
  const h=harness();h.state.view='detail';h.state.currentMeasurement={id:'saved'};
  const pending=h.openEvent(7),raw=noisySignal(200).map(v=>v+10);
  h.requests[0].resolve({event:{id:7,sequence:1,digital_mask_seen:1},t_us:raw.map((_,i)=>i*10),current_ua:raw,digital:raw.map((_,i)=>i%2)});
  await pending;await h.state.historyPlotPromise;
  const initial=h.plots.at(-1),original=raw.slice();
  h.element('historySmoothing').listeners.change({target:{value:'medium'}});await h.state.historyPlotPromise;
  const smoothed=h.plots.at(-1);
  assert.equal(smoothed[2].uirevision,initial[2].uirevision);
  assert.notDeepEqual(smoothed[1][0].y,initial[1][0].y);
  assert.equal(smoothed[1][0].customdata[0][0],raw[0]);
  assert.equal(smoothed[1][0].hoverinfo,'none');
  h.element('wakeEventChart').listeners.plotly_hover({points:[{curveNumber:0,pointNumber:0,x:0}],event:{clientX:100,clientY:200}});
  assert.match(h.element('currentChartTooltip').innerHTML,/Rohwert/);
  assert.equal(h.element('currentChartTooltip').style.left,'116px');
  assert.equal(h.element('currentChartTooltip').style.top,'216px');
  assert.deepEqual(smoothed[1][1],initial[1][1]);assert.deepEqual(raw,original);
  h.element('historySmoothing').listeners.change({target:{value:'off'}});await h.state.historyPlotPromise;
  assert.deepEqual(h.plots.at(-1)[1][0].y,initial[1][0].y);
  assert.equal(h.requests.length,1);
});

test('event loading status clears after rendering and render failures remain visible',async()=>{
  const h=harness();h.state.view='detail';h.state.currentMeasurement={id:'saved'};
  const plotted=deferred();h.context.Plotly.react=async()=>{await plotted.promise};
  const pending=h.openEvent(7);
  assert.match(h.element('wakeEventStatus').textContent,/Details werden geladen/);
  assert.equal(h.element('wakeEventChart').innerHTML,'');
  h.requests[0].resolve({event:{id:7,sequence:1},t_us:[0,10],current_ua:[4,5]});await settle();
  assert.match(h.element('wakeEventStatus').textContent,/Details werden geladen/);
  plotted.resolve();await pending;
  assert.match(h.element('wakeEventStatus').className,/hidden/);
  assert.equal(h.element('wakeEventStatus').textContent,'');
  h.context.Plotly.react=async()=>{throw new Error('render unavailable')};
  const failed=h.openEvent(8);h.requests[1].resolve({event:{id:8,sequence:2},t_us:[0],current_ua:[4]});await failed;
  assert.match(h.element('wakeEventStatus').innerHTML,/render unavailable/);
  assert.equal(h.state.historyMode,'event-error');
});

test('current charts scale axes at nA, uA, mA and A boundaries without changing raw hover values',async()=>{
  for(const [raw,unit,scaled] of [[.25,'nA',250],[999,'µA',999],[1000,'mA',1],[1e6,'A',1],[-2000,'mA',-2]]){
    const h=harness();h.state.view='detail';h.state.currentMeasurement={id:'saved'};h.state.historyScale='linear';
    const pending=h.openEvent(7);h.requests[0].resolve({event:{id:7,sequence:1},t_us:[0],current_ua:[raw]});await pending;
    assert.equal(h.plots[0][2].yaxis.title.text,`Strom [${unit}]`);
    assert.equal(h.plots[0][1][0].y[0],scaled);
    assert.equal(h.plots[0][1][0].customdata[0],raw);
    h.element('wakeEventChart').listeners.plotly_hover({points:[{curveNumber:0,pointNumber:0,x:0}],event:{clientX:1100,clientY:780}});
    assert.ok(h.element('currentChartTooltip').innerHTML.includes(unit));
    assert.equal(h.element('currentChartTooltip').style.left,'844px');
    assert.equal(h.element('currentChartTooltip').style.top,'684px');
    h.element('wakeEventChart').listeners.pointerleave();
    assert.ok(h.element('currentChartTooltip').classList.contains('hidden'));
  }
});

test('event zoom updates current unit while preserving time range and smoothing selection',async()=>{
  const h=harness();h.state.view='detail';h.state.currentMeasurement={id:'saved'};h.state.historyScale='linear';
  const pending=h.openEvent(7);h.requests[0].resolve({event:{id:7,sequence:1},t_us:[0,10,20,30],current_ua:[2000,.1,.2,.3]});await pending;
  assert.equal(h.plots.at(-1)[2].yaxis.title.text,'Strom [mA]');
  h.element('wakeEventChart').listeners.plotly_relayout({'xaxis.range':[10,30]});
  h.timers.get(h.state.eventZoomTimer)();
  assert.match(h.requests[1].url,/start_s=0.00001&end_s=0.00003/);
  h.requests[1].resolve({event:{id:7,sequence:1},t_us:[10,20,30],current_ua:[.1,.2,.3]});
  await settle();await h.state.historyPlotPromise;
  assert.equal(h.plots.at(-1)[2].yaxis.title.text,'Strom [nA]');
  assert.deepEqual(Array.from(h.plots.at(-1)[2].xaxis.range),[10,30]);
  h.element('wakeEventChart').listeners.plotly_relayout({'xaxis.autorange':true});
  h.timers.get(h.state.eventZoomTimer)();
  h.requests[2].resolve({event:{id:7,sequence:1},t_us:[0,10,20,30],current_ua:[2000,.1,.2,.3]});
  await settle();await h.state.historyPlotPromise;
  assert.equal(h.plots.at(-1)[2].yaxis.title.text,'Strom [mA]');
  assert.deepEqual(Array.from(h.state.eventVisibleRange),[0,30]);
});

test('live trace, peak markers and axis share the same scaled unit',async()=>{
  const h=harness();liveSession(h);h.state.liveScale='linear';
  const pending=h.refreshLiveSeries();h.requests[0].resolve({latest_s:1,history_points:[{t_s:0,current_ua:1000}],live_points:[{t_s:1,current_ua:2000}],events:[{t_s:.5,peak_ua:3000,sequence:1}]});await pending;
  const [history,live,peak]=h.plots[0][1];
  assert.equal(h.plots[0][2].yaxis.title.text,'Strom [mA]');
  assert.equal(history.y[0],1);assert.equal(live.y[0],2);assert.equal(peak.y[0],3);
  assert.equal(history.customdata[0][0],1000);
  assert.equal(peak.customdata[0][2],'3.000 mA');
});

test('select all follows the search while preserving hidden selections and excluding active sessions',()=>{
  const h=harness();
  h.state.measurements=[{id:'one',name:'Alpha',status:'completed'},{id:'two',name:'Beta',status:'completed'},{id:'live',name:'Alpha',status:'recording'}];
  h.renderMeasurementRows();
  assert.equal(h.element('exportSelectedMeasurements').disabled,true);
  h.element('historySearch').value='Alpha';
  h.element('selectAllMeasurements').listeners.change({target:{checked:true}});
  assert.deepEqual([...h.state.selectedMeasurementIds],['one']);
  h.element('historySearch').value='Beta';h.renderMeasurementRows();
  assert.equal(h.element('measurementSelectionCount').textContent,'1 ausgewählt (1 ausgeblendet)');
  assert.equal(h.element('selectAllMeasurements').checked,false);
  h.element('selectAllMeasurements').listeners.change({target:{checked:true}});
  h.element('historySearch').value='';h.renderMeasurementRows();
  assert.equal(h.element('selectAllMeasurements').checked,true);
  h.state.selectedMeasurementIds.delete('one');h.renderMeasurementRows();
  assert.equal(h.element('selectAllMeasurements').indeterminate,true);
  h.state.measurements=h.state.measurements.filter(m=>m.id!=='two');h.renderMeasurementRows();
  assert.equal(h.state.selectedMeasurementIds.size,0);
});

test('bulk deletion keeps failed selections and continues after a failure',async()=>{
  const h=harness();h.state.measurements=[{id:'one',status:'completed'},{id:'two',status:'completed'}];
  h.state.selectedMeasurementIds.add('one');h.state.selectedMeasurementIds.add('two');
  const pending=h.deleteSelectedMeasurements();
  assert.equal(h.element('deleteSelectedMeasurements').disabled,true);
  assert.equal(h.requests[0].options.method,'DELETE');
  h.requests[0].reject('Cannot delete');await settle();
  h.requests[1].resolve(null);await settle();
  h.requests[2].resolve([{id:'one',status:'completed'}]);await pending;
  assert.deepEqual([...h.state.selectedMeasurementIds],['one']);
  assert.match(h.element('toast').textContent,/1 gelöscht, 1 fehlgeschlagen/);
  assert.equal(h.state.measurementBulkBusy,false);
});

test('canceling bulk deletion sends no requests',async()=>{
  const h=harness();h.state.selectedMeasurementIds.add('one');h.context.confirm=()=>false;
  await h.deleteSelectedMeasurements();assert.equal(h.requests.length,0);assert.equal(h.state.selectedMeasurementIds.size,1);
});

test('bulk export sends the selected IDs and format and creates one download',async()=>{
  const h=harness();h.state.measurements=[{id:'one',status:'completed'},{id:'two',status:'completed'}];
  h.state.selectedMeasurementIds.add('two');h.element('bulkExportKind').value='csv';
  const pending=h.exportSelectedMeasurements();
  assert.deepEqual(JSON.parse(h.requests[0].options.body),{measurement_ids:['two'],kind:'csv'});
  await h.exportSelectedMeasurements();assert.equal(h.requests.length,1);
  h.requests[0].resolve('zip');await pending;
  assert.deepEqual(h.downloads,[{href:'blob:test',download:'powerlab_measurements.zip'}]);
  assert.deepEqual([...h.state.selectedMeasurementIds],['two']);
  assert.equal(h.state.measurementBulkBusy,false);
});

test('a failed export restores actions without starting a download',async()=>{
  const h=harness();h.state.measurements=[{id:'one',status:'completed'}];h.state.selectedMeasurementIds.add('one');
  const pending=h.exportSelectedMeasurements();h.requests[0].reject('Export unavailable');await pending;
  assert.equal(h.downloads.length,0);assert.equal(h.state.measurementBulkBusy,false);
  assert.equal(h.element('exportSelectedMeasurements').disabled,false);
  assert.equal(h.element('toast').textContent,'Export unavailable');
});

test('leaving a session closes its socket and rejects queued close callbacks',()=>{
  const h=harness(),ws=liveSession(h),queuedClose=ws.onclose;
  h.navs[1].listeners.click();queuedClose();
  assert.equal(h.state.view,'measurements');
  assert.equal(ws.closed,true);
  assert.equal(h.state.sessionMeasurement,null);
  assert.equal(h.timers.size,0);
  assert.equal(h.requests.length,1); // Only the list refresh.
});

test('stop shows pending status, rejects duplicate clicks and restores action after an error',async()=>{
  const h=harness();liveSession(h);
  const pending=h.element('sessionStopBtn').listeners.click();
  assert.equal(h.element('sessionStopBtn').disabled,true);
  assert.equal(h.element('sessionStatus').textContent,'STOPPING');
  assert.equal(h.element('sessionStopBtn').textContent,'Stoppt …');
  await h.element('sessionStopBtn').listeners.click();
  assert.equal(h.requests.length,1);
  h.requests[0].reject('stop failed');await pending;
  assert.equal(h.element('sessionStopBtn').disabled,false);
  assert.equal(h.state.sessionStopPendingId,null);
  assert.equal(h.element('sessionStatus').textContent,'RECORDING');
  assert.equal(h.element('toast').textContent,'stop failed');
});

test('a reconnect scheduled before navigation cannot reopen the old session',()=>{
  const h=harness(),ws=liveSession(h);ws.onclose();
  const retry=[...h.timers.values()][0];
  h.setView('measurements');retry();
  assert.equal(h.sockets.length,1);
  assert.equal(h.timers.size,0);
});

test('a completed-session response cannot navigate after the user leaves',async()=>{
  const h=harness(),ws=liveSession(h);
  const pending=ws.onmessage({data:JSON.stringify({running:false})});
  h.setView('measurements');h.requests[0].resolve({id:'running',status:'completed'});
  await pending;
  assert.equal(h.state.view,'measurements');
  assert.equal(h.requests.length,1);
});

test('a delayed measurement load cannot replace the list or a newer selection',async()=>{
  const h=harness();h.setView('measurements');
  const first=h.openMeasurement('first'),second=h.openMeasurement('second');
  h.requests[2].resolve({id:'second',events:[]});h.requests[3].resolve({});await second;
  h.requests[0].resolve({id:'first',events:[]});h.requests[1].resolve({});await first;
  assert.equal(h.state.currentMeasurement.id,'second');
  const third=h.openMeasurement('third');h.setView('measurements');
  h.requests[4].resolve({id:'third'});h.requests[5].resolve({});await third;
  assert.equal(h.state.view,'measurements');
  assert.equal(h.state.currentMeasurement.id,'second');
});

test('wake-event chart stays selected despite callbacks from the old live socket',async()=>{
  const h=harness(),ws=liveSession(h),oldMessage=ws.onmessage,oldClose=ws.onclose;
  const detail=h.openMeasurement('running');
  h.requests[0].resolve({id:'running',events:[]});h.requests[1].resolve({});await detail;
  const event=h.openEvent(7);
  h.requests[2].resolve({event:{id:7,sequence:1},t_us:[0,10],current_ua:[1,2]});await event;
  await oldMessage({data:JSON.stringify({running:false})});oldClose();await settle();
  assert.equal(h.state.view,'detail');
  assert.equal(h.state.historyMode,'event');
  assert.equal(h.element('wakeEventCard').classList.contains('hidden'),false);
  assert.equal(h.element('historyOverviewCard').classList.contains('hidden'),true);
  assert.equal(h.requests.length,3);
  assert.equal(h.timers.size,0);
});

test('scheduled-session updates cannot reopen sessions after navigation',async()=>{
  const h=harness();h.state.sessionMeasurement={id:'scheduled',status:'scheduled'};h.setView('session');
  h.renderLiveOverview({running:[{measurement_id:'scheduled'}],scheduled:[]});
  h.setView('measurements');h.requests[0].resolve({status:'recording'});await settle();
  h.renderLiveOverview({running:[{measurement_id:'scheduled'}],scheduled:[]});
  assert.equal(h.state.view,'measurements');assert.equal(h.requests.length,1);
});

test('slow full-history fetch survives repeated live snapshots and renders the whole range',async()=>{
  const h=harness();liveSession(h);
  const pending=h.refreshLiveSeries();h.advance(1500);
  await h.refreshLiveSeries();await h.refreshLiveSeries();
  assert.equal(h.requests.length,1);
  h.requests[0].resolve({latest_s:120,history_points:[{t_s:0,current_ua:4},{t_s:120,current_ua:5}],live_points:[{t_s:110,current_ua:6}]});await pending;
  assert.deepEqual(Array.from(h.plots[0][2].xaxis.range),[0,120]);
  assert.deepEqual(Array.from(h.plots[0][1][0].x),[0,120]);
  assert.equal(h.state.liveSeriesInFlight,null);
});

test('programmatic relayout during slow Plotly rendering does not pause live follow',async()=>{
  const h=harness();liveSession(h);const plotted=deferred();
  // Bind the existing chart interaction handler once.
  vm.runInContext('bindLiveChartInteractions()',h.context);
  h.context.Plotly.react=async()=>{h.advance(1000);h.element('liveChart').listeners.plotly_relayout({'xaxis.range':[0,120]});await plotted.promise};
  const pending=h.refreshLiveSeries();h.requests[0].resolve({latest_s:120,history_points:[]});await settle();
  assert.equal(h.state.liveFollow,true);plotted.resolve();await pending;
});

test('live history breaks independent segments and never interpolates sparse wake summaries',async()=>{
  const h=harness();liveSession(h);
  h.state.liveScale='linear';
  const pending=h.refreshLiveSeries();
  h.requests[0].resolve({latest_s:47,history_points:[
    {t_s:0,current_ua:4,kind:'sleep',line_key:'sleep-1'},
    {t_s:10,current_ua:4,kind:'sleep',line_key:'sleep-1'},
    {t_s:11,current_ua:20,kind:'event_start',event_id:1},
    {t_s:31,current_ua:220000,kind:'event_peak',event_id:1},
    {t_s:41,current_ua:4,kind:'event_end',event_id:1},
    {t_s:42,current_ua:4,kind:'wake_envelope',line_key:'wake-2'},
    {t_s:43,current_ua:200000,kind:'wake_envelope',line_key:'wake-2'},
    {t_s:44,current_ua:4,kind:'wake_envelope',line_key:'wake-2'},
    {t_s:45,current_ua:50,kind:'sleep',line_key:'sleep-2'},
    {t_s:47,current_ua:50,kind:'sleep',line_key:'sleep-2'},
  ],events:[{t_s:11,sequence:1,peak_ua:220000}]});
  await pending;
  const trace=h.plots[0][1][0];
  assert.equal(trace.connectgaps,false);
  assert.ok(!trace.x.includes(31));
  const segments=[];let segment=[];
  for(let i=0;i<trace.x.length;i++){
    if(trace.x[i]===null){if(segment.length)segments.push(segment);segment=[];assert.equal(trace.y[i],null)}
    else segment.push(trace.x[i]);
  }
  if(segment.length)segments.push(segment);
  assert.deepEqual(segments,[[0,10],[42,43,44],[45,47]]);
  assert.equal(h.plots[0][1][1].mode,'markers');
  h.state.liveScale='log';h.advance(1500);
  const logPending=h.refreshLiveSeries();h.requests[1].resolve(h.state.liveLastSeries);await logPending;
  const logTrace=h.plots[1][1][0];
  assert.equal(logTrace.y[logTrace.x.indexOf(null)],null);
});

test('a late history response does not redraw a session the user left',async()=>{
  const h=harness();liveSession(h);const pending=h.refreshLiveSeries();
  h.setView('measurements');h.requests[0].resolve({latest_s:120,history_points:[]});await pending;
  assert.equal(h.plots.length,0);
});

test('selecting a wake event invalidates a pending refresh of the measurement overview',async()=>{
  const h=harness();h.setView('detail');h.state.currentMeasurement={id:'saved'};
  const refresh=h.openMeasurement('saved');const event=h.openEvent(7);
  h.requests[2].resolve({event:{id:7,sequence:1},t_us:[0],current_ua:[2]});await event;
  h.requests[0].resolve({id:'saved',events:[]});h.requests[1].resolve({});await refresh;
  assert.equal(h.state.historyMode,'event');assert.equal(h.state.currentEvent.event.id,7);
});

test('returning to live follow cancels a pending zoom request and fetches the full history',async()=>{
  const h=harness();liveSession(h);h.state.liveFollow=false;h.state.liveFullRange=[110,120];
  vm.runInContext('scheduleLiveRangeRefresh([110,120])',h.context);
  h.element('liveFollowBtn').listeners.click();
  assert.equal(h.timers.size,0);assert.equal(h.state.liveFollow,true);
  assert.equal(h.requests.length,1);assert.equal(h.requests[0].url.includes('start_s'),false);
  h.requests[0].resolve({latest_s:120,history_points:[]});await settle();
});
