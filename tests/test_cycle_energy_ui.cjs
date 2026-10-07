const {test}=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const vm=require('node:vm');
const path=require('node:path');

test('wake variability displays squared units, zero spread and clears stale values',()=>{
  const elements=new Map();
  const element=id=>{
    if(!elements.has(id))elements.set(id,{textContent:'',value:id.endsWith('Unit')?'days':'1',setCustomValidity(){}});
    return elements.get(id);
  };
  const context=vm.createContext({document:{getElementById:element},fmtCurrent:value=>String(value)});
  vm.runInContext(fs.readFileSync(path.join(__dirname,'../app/static/cycle-energy.js'),'utf8')+'\nglobalThis.ui=CycleEnergyUI;',context);
  context.ui.render({cycle_count:2,wake_variability:{count:2,duration_s:{mean:.3,stddev:.1,variance:.01,cv_pct:33.33},current_ua:{mean:1000,stddev:0,variance:0,cv_pct:0}}},'detail');
  assert.equal(element('detailCycleVariabilityduration_svariance').textContent,'0,01 s²');
  assert.equal(element('detailCycleVariabilitycurrent_uavariance').textContent,'0 µA²');
  assert.equal(element('detailCycleVariabilitycurrent_uacv_pct').textContent,'0 %');
  assert.match(element('detailCycleVariabilityNote').textContent,/2 gültigen Wakes/);
  context.ui.render(null,'detail');
  assert.equal(element('detailCycleVariabilityduration_svariance').textContent,'—');
  assert.equal(element('detailCycleVariabilitycurrent_uacv_pct').textContent,'—');
  assert.match(element('detailCycleVariabilityNote').textContent,/0 gültigen Wakes/);
});
