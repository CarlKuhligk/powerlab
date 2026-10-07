// Reads the pre-generated independent NumPy references from stdin.
const fs=require('node:fs'),vm=require('node:vm'),path=require('node:path');
const context=vm.createContext({});
vm.runInContext(fs.readFileSync(path.join(__dirname,'../app/static/battery-life.js'),'utf8')+'\nglobalThis.model=BatteryLifeModel;',context);
const cases=JSON.parse(fs.readFileSync(0,'utf8'));
let checks=0;
function close(actual,expected,label){
  checks++;
  if(expected===null){if(actual!==null)throw Error(`${label}: ${actual} != null`);return}
  if(Array.isArray(expected)){expected.forEach((v,i)=>close(actual[i],v,`${label}[${i}]`));return}
  if(typeof expected==='object'){for(const [k,v] of Object.entries(expected))close(actual[k],v,`${label}.${k}`);return}
  if(!Number.isFinite(actual)||Math.abs(actual-expected)>1e-10+2e-10*Math.abs(expected))throw Error(`${label}: ${actual} != ${expected}`);
}
for(const c of cases){
  const data=context.model.combine(c.sources,c.weighting),e=c.expected;
  for(const [a,b] of [['count','count'],['wakeS','wake_s'],['wakeEnergyUwh','wake_energy_uwh'],['sleepPowerUw','sleep_power_uw']])close(data[a],e[b],`${c.name}.${a}`);
  close(data.sources.map(s=>s.share),e.shares,`${c.name}.shares`);
  for(const [a,b] of [['wakeEnergy','wake_energy'],['sleepEnergy','sleep_energy'],['sleepPower','sleep_power']])close(data.statistics[a],e.statistics[b],`${c.name}.${a}`);
  const d=context.model.distribution(data,c.energy_wh,c.mode,c.value),p=d.point;
  for(const [a,b] of [['expectedH','expected_h'],['powerUw','power_uw'],['sleepS','sleep_s'],['dutyPct','duty_pct'],['scenariosH','scenarios_h']])close(p[a],e[b],`${c.name}.${a}`);
  for(const [a,b] of [['minH','0'],['p05H','5'],['lowH','10'],['medianH','50'],['highH','90'],['p95H','95'],['maxH','100']])close(p[a],e.percentiles[b],`${c.name}.${a}`);
  close(d.meanH,e.scenarios_h.reduce((sum,h,i)=>sum+h*data.weights[i],0),`${c.name}.scenario-mean`);
  for(const [a,b] of [['stddevH','stddev_h'],['varianceH2','variance_h2'],['bins','bins']])close(d[a],e[b],`${c.name}.${a}`);
  if(c.name.startsWith('constant')||c.name.startsWith('single')){
    if(d.bins.length)throw Error(`${c.name}: fabricated spread for constant/single cycle`);
  }else{
    close(d.bins[0].leftH,e.percentiles['0'],`${c.name}.lower-bound`);
    close(d.bins.at(-1).rightH,e.percentiles['100'],`${c.name}.upper-bound`);
    close(d.bins.reduce((a,b)=>a+b.share,0),1,`${c.name}.total-share`);
  }
}
console.log(`${cases.length} cases, ${checks} independent numeric comparisons passed.`);
