const {test}=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const path=require('node:path');
const vm=require('node:vm');
const source=fs.readFileSync(path.join(__dirname,'../app/static/battery-life.js'),'utf8');
const summary=()=>({voltage_v:3.3,valid_wake_phases:[{sequence:1,duration_s:1,energy_uwh:1},{sequence:2,duration_s:2,energy_uwh:3}],
  valid_sleep_phases:[{following_wake_sequence:1,duration_s:9,energy_uwh:.09},{following_wake_sequence:2,duration_s:18,energy_uwh:.36}]});
const close=(actual,expected)=>assert.ok(Math.abs(actual-expected)<1e-9*Math.max(1,Math.abs(expected)),`${actual} != ${expected}`);

function harness(){
  const elements=new Map(),timers=new Map(),requests=[],plots=[];
  const defaults={Search:'',Weighting:'mean',Mode:'sleep',TimeUnit:'seconds',Energy:'1',EnergyUnit:'Wh',Value:'60'};
  let sourceNodes=[];
  function parseSources(html){
    sourceNodes=[];
    for(const match of html.matchAll(/data-battery-select="([^"]+)"([^>]*)/g))sourceNodes.push({kind:'select',dataset:{batterySelect:match[1]},checked:match[2].includes('checked'),listeners:{},addEventListener(event,fn){this.listeners[event]=fn}});
    for(const match of html.matchAll(/data-battery-(share|count)="([^"]+)"/g))sourceNodes.push({kind:match[1],dataset:{[match[1]==='share'?'batteryShare':'batteryCount']:match[2]},textContent:''});
    for(const match of html.matchAll(/value="([^"]*)" data-battery-weight="([^"]+)"([^>]*)/g))sourceNodes.push({kind:'weight',dataset:{batteryWeight:match[2]},value:match[1],disabled:match[3].includes('disabled'),listeners:{},addEventListener(event,fn){this.listeners[event]=fn}});
  }
  const el=id=>{
    if(!elements.has(id)){
      const classes=new Set();
      let value=defaults[id]??'',html='';
      elements.set(id,{get value(){return value},set value(next){value=String(next)},textContent:'',get innerHTML(){return html},set innerHTML(next){html=next;if(id==='SourceRows')parseSources(next)},style:{},listeners:{},
        setAttribute(){},addEventListener(event,fn){this.listeners[event]=fn},
        classList:{add:c=>classes.add(c),contains:c=>classes.has(c),toggle(c,value){if(value??!classes.has(c))classes.add(c);else classes.delete(c)}}});
    }
    return elements.get(id);
  };
  let timerId=0;
  const context=vm.createContext({document:{getElementById:id=>el(id.replace(/^battery/,'')),querySelectorAll:selector=>sourceNodes.filter(n=>selector===`[data-battery-${n.kind}]`)},state:{view:'battery'},
    api(url){return new Promise((resolve,reject)=>requests.push({url,resolve,reject}))},
    Plotly:{purge:graph=>{graph.data=null},react:async(graph,traces,layout)=>{graph.data=traces;graph.layout=layout;plots.push({traces,layout})}},
    setTimeout(fn){timers.set(++timerId,fn);return timerId},clearTimeout:id=>timers.delete(id),
    escapeHtml:String,fmtDate:String,fmtDuration:String,fmtEventDuration:String,fmtEnergy:String});
  vm.runInContext(source+'\nglobalThis.model=BatteryLifeModel;globalThis.ui=BatteryLifeUI;',context);
  const flush=async()=>{for(const [id,fn] of [...timers]){timers.delete(id);fn()}await new Promise(r=>setImmediate(r))};
  return {el,requests,plots,context,model:context.model,ui:context.ui,flush,node:(kind,id)=>sourceNodes.find(n=>n.kind===kind&&Object.values(n.dataset).includes(id))};
}

test('runtime scales with capacity, timing and observed joint variance',()=>{
  const {model}=harness(),data=model.profile(summary());
  const point=model.evaluate(data,1,'sleep',9);
  close(data.sleepPowerUw,60);
  close(data.statistics.wakeEnergy.variance,2);
  close(data.statistics.sleepPower.variance,648);
  close(point.expectedH,1e6/((2*3600+60*9)/10.5));
  close(point.minH,1e6/((3*3600+72*9)/11));
  close(point.maxH,1e6/((1*3600+36*9)/10));
  close(point.medianH,(point.minH+point.maxH)/2);
  close(model.evaluate(data,2,'sleep',9).expectedH,point.expectedH*2);
  close(model.evaluate(data,1,'duty',point.dutyPct).expectedH,point.expectedH);
  const curve=model.curve(data,1,'sleep',.1,100);
  assert.ok(curve.every((p,i)=>!i||p.expectedH>curve[i-1].expectedH));
  for(const p of curve)assert.ok(p.minH<=p.p05H&&p.p05H<=p.lowH&&p.lowH<=p.medianH&&p.medianH<=p.highH&&p.highH<=p.p95H&&p.p95H<=p.maxH);
  assert.throws(()=>model.profile({valid_wake_phases:[]}));
  assert.throws(()=>model.evaluate(data,1,'duty',0));
  assert.throws(()=>model.curve(data,1,'sleep',10,1));
});

test('calculator centers the bell curve and 90 percent band, including 24 hour sleep',async()=>{
  const h=harness();h.ui.install();
  const opening=h.ui.open('chosen');
  h.requests[0].resolve([{id:'chosen',name:'Chosen',status:'completed'}]);
  await new Promise(r=>setImmediate(r));
  h.requests[1].resolve(summary());await opening;await h.flush();
  assert.equal(h.plots.at(-1).traces.filter(t=>t.fill==='tozeroy').length,1);
  assert.equal(h.plots.at(-1).layout.shapes.filter(s=>s.type==='rect').length,2);
  assert.match(h.el('StatisticsRows').innerHTML,/Varianz|µWh²/);
  assert.match(h.el('PercentileRows').innerHTML,/P95/);
  const seconds=Number(h.el('Value').value);
  h.el('TimeUnit').value='minutes';h.el('TimeUnit').listeners.change();await h.flush();
  close(Number(h.el('Value').value)*60,seconds);
  h.el('TimeUnit').value='hours';h.el('TimeUnit').listeners.change();
  h.el('Value').value='24';h.el('Value').listeners.input();await h.flush();
  const plot=h.plots.at(-1),bell=plot.traces[0];
  assert.equal(h.el('Export').disabled,false);
  assert.match(plot.layout.xaxis.title.text,/Jahre/);
  const expected=h.model.evaluate(h.model.combine([{id:'chosen',summary:summary()}]),1,'sleep',86400).expectedH/8760;
  close((plot.layout.xaxis.range[0]+plot.layout.xaxis.range[1])/2,expected);
  close(bell.x[120],expected);
  assert.equal(bell.y[120],Math.max(...bell.y));
  assert.match(h.el('Band').textContent,/bis/);
  h.el('Energy').value='0';h.el('Energy').listeners.input();await h.flush();
  assert.equal(h.el('Runtime').textContent,'—');
  assert.equal(h.el('Export').disabled,true);
  assert.match(h.el('Status').textContent,/positive/);
  assert.equal(h.el('DistributionNote').textContent,'');
});

test('normal model uses measured scatter and P5 to P95 for its central 90 percent',()=>{
  const {model}=harness(),data=model.profile(summary());
  const result=model.distribution(data,1,'sleep',86400);
  close(result.meanH,result.point.expectedH);
  close(result.stddevH,Math.abs(result.point.maxH-result.point.minH)/Math.sqrt(2));
  close(result.modelPercentiles[5],result.meanH-1.6448536269514722*result.stddevH);
  close(result.modelPercentiles[95],result.meanH+1.6448536269514722*result.stddevH);
  close(model.distribution(data,2,'sleep',86400).stddevH,result.stddevH*2);
  const one=summary();one.valid_wake_phases=one.valid_wake_phases.slice(0,1);one.valid_sleep_phases=one.valid_sleep_phases.slice(0,1);
  const single=model.distribution(model.profile(one),1,'sleep',86400);
  assert.equal(single.stddevH,null);assert.equal(single.curve.length,0);
  assert.match(single.reason,/mindestens zwei/);
  const identical=summary();identical.valid_wake_phases[1]={...identical.valid_wake_phases[0],sequence:2};identical.valid_sleep_phases[1]={...identical.valid_sleep_phases[0],following_wake_sequence:2};
  const zero=model.distribution(model.profile(identical),1,'sleep',86400);
  assert.equal(zero.stddevH,0);assert.equal(zero.curve.length,0);
});

test('late source responses cannot replace the currently selected measurement',async()=>{
  const h=harness();h.ui.install();
  const first=h.ui.open();
  h.requests[0].resolve([{id:'first',name:'First',status:'completed'},{id:'second',name:'Second',status:'completed'}]);await new Promise(r=>setImmediate(r));
  h.node('select','first').checked=false;h.node('select','first').listeners.change();
  h.node('select','second').checked=true;h.node('select','second').listeners.change();
  h.requests[2].resolve(summary());await new Promise(r=>setImmediate(r));await h.flush();
  const count=h.plots.length;
  h.requests[1].reject(new Error('Old source failed'));await first;await h.flush();
  assert.equal(h.plots.length,count);
  assert.doesNotMatch(h.el('Status').textContent,/Old source failed/);
});

test('measurement shares affect means, variance and percentiles; zero weight removes extrema',()=>{
  const {model}=harness();
  const second={voltage_v:3.3,valid_wake_phases:[{sequence:1,duration_s:1,energy_uwh:9}],valid_sleep_phases:[{following_wake_sequence:1,duration_s:9,energy_uwh:.09}]};
  const sources=[{id:'a',name:'A',summary:summary(),weight:7},{id:'b',name:'B',summary:second,weight:3}];
  const equal=model.combine(sources,'mean'),cycles=model.combine(sources,'cycle_count'),custom=model.combine(sources,'custom');
  close(equal.wakeEnergyUwh,5.5);close(equal.sleepPowerUw,48);
  close(equal.statistics.wakeEnergy.variance,20.4);
  close(cycles.wakeEnergyUwh,13/3);close(cycles.statistics.wakeEnergy.variance,52/3);
  close(custom.wakeEnergyUwh,4.1);close(custom.sources[0].share,.7);
  const mostlyA=model.combine([{...sources[0],weight:99},{...sources[1],weight:1}],'custom');
  assert.notEqual(model.evaluate(mostlyA,1,'sleep',9).medianH,model.evaluate(equal,1,'sleep',9).medianH);
  const onlyA=model.combine([{...sources[0],weight:1},{...sources[1],weight:0}],'custom');
  assert.equal(onlyA.count,2);close(onlyA.statistics.wakeEnergy.max,3);
  assert.throws(()=>model.combine(sources.map(s=>({...s,weight:0})),'custom'));
});

test('changing weights recalculates cached measurements without resetting chosen timing',async()=>{
  const h=harness();h.ui.install();
  const open=h.ui.open();h.requests[0].resolve([{id:'a',name:'A',status:'completed'},{id:'b',name:'B',status:'completed'}]);
  await new Promise(r=>setImmediate(r));h.requests[1].resolve(summary());await open;await h.flush();
  h.el('Value').value='15';h.el('Value').listeners.input();await h.flush();
  h.node('select','b').checked=true;h.node('select','b').listeners.change();
  h.requests[2].resolve(summary());await new Promise(r=>setImmediate(r));await h.flush();
  assert.equal(h.el('Value').value,'15');
  h.el('Weighting').value='custom';h.el('Weighting').listeners.change();await new Promise(r=>setImmediate(r));await h.flush();
  h.node('weight','a').value='7';h.node('weight','a').listeners.input();
  await new Promise(r=>setImmediate(r));await h.flush();
  close(parseFloat(h.node('share','a').textContent.replace(',','.')),87.5);
  assert.equal(h.requests.length,3);
  h.el('ClearSelection').listeners.click();assert.equal(h.el('Export').disabled,true);
  assert.match(h.el('Status').textContent,/mindestens eine/);
});
