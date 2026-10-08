const {test}=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const vm=require('node:vm');
const path=require('node:path');

test('wake analysis displays squared units, zero spread and clears stale values',()=>{
  const elements=new Map();
  const element=id=>{
    if(!elements.has(id))elements.set(id,{textContent:''});
    return elements.get(id);
  };
  const context=vm.createContext({document:{getElementById:element},fmtCurrent:value=>String(value),fmtEnergy:value=>String(value),fmtDuration:value=>String(value)});
  vm.runInContext(fs.readFileSync(path.join(__dirname,'../app/static/wake-analysis.js'),'utf8')+'\nglobalThis.ui=WakeAnalysisUI;',context);
  context.ui.render({cycle_count:2,average_wake_duration_s:.3,average_wake_current_ua:1000,average_wake_energy_uwh:.1,
    wake_variability:{count:2,duration_s:{mean:.3,stddev:.1,variance:.01,cv_pct:33.33},current_ua:{mean:1000,stddev:0,variance:0,cv_pct:0}}},'detail');
  assert.equal(element('detailWakeAnalysisDuration').textContent,'0.3');
  assert.equal(element('detailWakeAnalysisCurrent').textContent,'1000');
  assert.equal(element('detailWakeAnalysisEnergy').textContent,'0.1');
  assert.equal(element('detailWakeAnalysisVariabilityduration_svariance').textContent,'0,01 s²');
  assert.equal(element('detailWakeAnalysisVariabilitycurrent_uavariance').textContent,'0 µA²');
  assert.equal(element('detailWakeAnalysisVariabilitycurrent_uacv_pct').textContent,'0 %');
  assert.match(element('detailWakeAnalysisVariabilityNote').textContent,/2 gültigen Wakes/);
  context.ui.render(null,'detail');
  assert.equal(element('detailWakeAnalysisEnergy').textContent,'null');
  assert.equal(element('detailWakeAnalysisCount').textContent,'0 gültige Wakes');
  assert.equal(element('detailWakeAnalysisVariabilityduration_svariance').textContent,'—');
  assert.equal(element('detailWakeAnalysisVariabilitycurrent_uacv_pct').textContent,'—');
  assert.match(element('detailWakeAnalysisVariabilityNote').textContent,/0 gültigen Wakes/);
});
