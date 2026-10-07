const {test}=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const path=require('node:path');
const vm=require('node:vm');

test('sleep chapter shows recorded consumption without cycles and clears previous phases',()=>{
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
  assert.equal(element('detailSleepPhaseCount').textContent,'1');
  assert.match(element('detailSleepPhaseRows').innerHTML,/Vor Wake #2/);
  assert.match(element('detailSleepPhaseRows').innerHTML,/Mit ergänzten Daten/);
  context.renderSleepAnalysis({recorded_segment_count:2,background_count:1,recorded_duration_s:2,
    average_current_ua:6.4,charge_uc:12.8,energy_uwh:.0117,valid_phases:[]});
  assert.equal(element('detailSleepCurrent').textContent,'6.4');
  assert.equal(element('detailSleepPhaseCount').textContent,'0');
  assert.doesNotMatch(element('detailSleepPhaseRows').innerHTML,/Vor Wake #2/);
  context.renderSleepAnalysis(null);
  assert.equal(element('detailSleepCharge').textContent,'null');
});
