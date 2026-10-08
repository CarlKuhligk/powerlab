const {test}=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const path=require('node:path');
const vm=require('node:vm');

test('sleep chapter shows recorded consumption without cycles and clears stale values',()=>{
  const elements=new Map();
  const element=id=>{if(!elements.has(id))elements.set(id,{textContent:'',innerHTML:''});return elements.get(id)};
  const app=fs.readFileSync(path.join(__dirname,'../app/static/app.js'),'utf8');
  const source=app.slice(app.indexOf('function renderSleepAnalysis('),app.indexOf('function renderMeasurementDetail('));
  const context=vm.createContext({$:element,escapeHtml:String,fmtDuration:String,fmtCurrent:String,fmtCharge:String,fmtEnergy:String});
  vm.runInContext(source,context);
  context.renderSleepAnalysis({recorded_segment_count:3,background_count:1,recorded_duration_s:3,
    average_current_ua:5.6,charge_uc:16.8,energy_uwh:.0154,valid_phases:[{
      following_wake_sequence:2,start_s:0,duration_s:2,mean_ua:6.4,charge_uc:12.8,energy_uwh:.0117,interpolated_samples:4}]});
  assert.equal(element('detailSleepCharge').textContent,'16.8');
  assert.equal(elements.has('detailSleepPhaseRows'),false);
  context.renderSleepAnalysis({recorded_segment_count:2,background_count:1,recorded_duration_s:2,
    average_current_ua:6.4,charge_uc:12.8,energy_uwh:.0117,valid_phases:[]});
  assert.equal(element('detailSleepCurrent').textContent,'6.4');
  assert.equal(elements.has('detailSleepPhaseRows'),false);
  context.renderSleepAnalysis(null);
  assert.equal(element('detailSleepCharge').textContent,'null');
});

test('protocol merges sleep and wake phases chronologically without counting checkpoints or background as wakes',()=>{
  const elements=new Map();
  const element=id=>{if(!elements.has(id))elements.set(id,{textContent:'',innerHTML:'',open:true});return elements.get(id)};
  const app=fs.readFileSync(path.join(__dirname,'../app/static/app.js'),'utf8');
  const source=app.slice(app.indexOf('function renderEventProtocol('),app.indexOf('function renderMeasurementDetail('));
  const context=vm.createContext({$:element,document:{querySelectorAll:()=>[]},escapeHtml:String,
    fmtDuration:String,fmtEventDuration:String,fmtCurrent:String,fmtCharge:String,fmtEnergy:String});
  vm.runInContext(source,context);
  const m={sample_rate_hz:10,voltage_mv:3300,duration_s:5,
    events:[{id:12,sequence:2,trigger_sample:20,duration_us:400000,charge_uc:400,mean_ua:1000,peak_ua:1500},
      {id:14,sequence:4,trigger_sample:40,duration_us:400000,charge_uc:400,mean_ua:1000,peak_ua:1500}],
    background_events:[{id:11,sequence:1,event_kind:'background',trigger_sample:10,duration_us:200000,charge_uc:4}],
    sleep_analysis:{valid_phases:[{following_wake_sequence:2,start_s:0,duration_s:2,mean_ua:6.4,charge_uc:12.8,energy_uwh:.0117,interpolated_samples:4}]},
    cycle_energy:{valid_wake_phases:[{sequence:2,start_s:2,duration_s:.4,charge_uc:400.1,energy_uwh:.367,interpolated_samples:1}]}};
  const overview={state_markers:[{kind:'sleep_start',t_s:0},{kind:'sleep_validated',t_s:.1},
    {kind:'wake_start',t_s:2},{kind:'sleep_start',t_s:2.4},{kind:'wake_start',t_s:4},
    {kind:'sleep_start',t_s:4.4},{kind:'fft_sleep_start',t_s:4.5}],
    analysis:{timeline_duration_s:5}};
  context.renderEventProtocol(m,overview);
  const html=element('eventRows').innerHTML;
  assert.equal(element('wakeEventCount').textContent,'3 Sleep / 2 Wake / 1 Hintergrund');
  assert.equal(element('eventProtocol').open,false);
  assert.ok(html.indexOf('Sleep #1')<html.indexOf('Hintergrund #1'));
  assert.ok(html.indexOf('Hintergrund #1')<html.indexOf('Wake #2'));
  assert.ok(html.indexOf('Wake #2')<html.indexOf('Sleep #2'));
  assert.match(html,/Mit ergänzten Daten/);
  assert.match(html,/400.1/);
  assert.match(html,/Sleep #3/);
  assert.match(html,/Ohne gültigen Zyklus/);
  assert.doesNotMatch(html,/Sleep #4/);
  assert.match(html,/<td>4<\/td><td>2<\/td><td>400000<\/td>/);
  context.renderEventProtocol({},{});
  assert.equal(element('wakeEventCount').textContent,'0 Sleep / 0 Wake / 0 Hintergrund');
  assert.match(element('eventRows').innerHTML,/Keine Sleep- oder Wake-Phasen/);
  assert.doesNotMatch(element('eventRows').innerHTML,/Sleep #1|Wake #2/);
  context.renderEventProtocol({sleep_analysis:m.sleep_analysis},{});
  assert.match(element('eventRows').innerHTML,/Sleep #1/);
});
