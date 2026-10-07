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
  for(const [a,b] of [['meanH','mean_h'],['stddevH','stddev_h'],['varianceH2','variance_h2'],['modelPercentiles','percentiles_h']])close(d[a],e[b],`${c.name}.${a}`);
  if(c.name.startsWith('constant')||c.name.startsWith('single')){
    if(d.curve.length)throw Error(`${c.name}: fabricated bell curve for constant/single cycle`);
  }else{
    close(d.curve[120].hours,e.expected_h,`${c.name}.curve-center`);
    close(d.curve[120].densityPerHour,1/(e.stddev_h*Math.sqrt(2*Math.PI)),`${c.name}.density-peak`);
    // Simpson integration verifies the shaded interval has 90% Gaussian mass.
    const lo=d.modelPercentiles['5'],hi=d.modelPercentiles['95'],step=(hi-lo)/1000;
    let integral=0;
    for(let i=0;i<=1000;i++){
      const z=(lo+i*step-d.meanH)/d.stddevH;
      integral+=(i===0||i===1000?1:i%2?4:2)*Math.exp(-z*z/2)/(d.stddevH*Math.sqrt(2*Math.PI));
    }
    close(integral*step/3,.9,`${c.name}.central-probability`);
  }
}
console.log(`${cases.length} cases, ${checks} independent numeric comparisons passed.`);
