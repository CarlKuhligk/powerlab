/* Battery energy and measured complete cycles, with adjustable future timing. */
const BatteryLifeModel=(()=>{
  const average=values=>values.reduce((sum,value)=>sum+value,0)/values.length;
  function statistics(values){
    const mean=average(values),variance=values.length>1?values.reduce((n,x)=>n+(x-mean)**2,0)/(values.length-1):null;
    return {mean,variance,stddev:variance==null?null:Math.sqrt(variance),min:values.reduce((a,b)=>Math.min(a,b)),max:values.reduce((a,b)=>Math.max(a,b))};
  }
  function quantile(values,q){
    const sorted=[...values].sort((a,b)=>a-b),at=(sorted.length-1)*q;
    const lower=sorted[Math.floor(at)],upper=sorted[Math.ceil(at)];
    return lower===upper?lower:lower+(upper-lower)*(at-Math.floor(at));
  }
  function weightedQuantile(values,weights,q){
    const ordered=values.map((value,i)=>({value,weight:weights[i]})).filter(s=>s.weight>0).sort((a,b)=>a.value-b.value);
    const total=ordered.reduce((n,s)=>n+s.weight,0);let cumulative=0,previous=null;
    for(const s of ordered){
      const position=(cumulative+s.weight/2)/total;cumulative+=s.weight;
      if(q<=position){
        if(!previous||s.value===previous.value)return s.value;
        return previous.value+(s.value-previous.value)*(q-previous.position)/(position-previous.position);
      }
      previous={position,value:s.value};
    }
    return ordered.at(-1).value;
  }
  function weightedStatistics(values,weights){
    const origin=values[0],mean=origin+values.reduce((n,x,i)=>n+(x-origin)*weights[i],0),correction=1-weights.reduce((n,w)=>n+w*w,0);
    const variance=correction>1e-15?values.reduce((n,x,i)=>n+weights[i]*(x-mean)**2,0)/correction:null;
    return {mean,variance,stddev:variance==null?null:Math.sqrt(variance),min:values.reduce((a,b)=>Math.min(a,b)),max:values.reduce((a,b)=>Math.max(a,b))};
  }
  function profile(summary){
    const sleeps=new Map((summary?.valid_sleep_phases||[]).map(p=>[p.following_wake_sequence,p]));
    const samples=(summary?.valid_wake_phases||[]).map(w=>{
      const s=sleeps.get(w.sequence);
      if(!s||![w.duration_s,s.duration_s,w.energy_uwh,s.energy_uwh].every(Number.isFinite)
          ||w.duration_s<=0||s.duration_s<=0||w.energy_uwh<0||s.energy_uwh<0){
        throw new Error('Für die Laufzeit werden gültige Sleep-/Wake-Zyklen mit nichtnegativem Energieverbrauch benötigt.');
      }
      return {wakeS:w.duration_s,sleepS:s.duration_s,wakeEnergyUwh:w.energy_uwh,
        sleepPowerUw:s.energy_uwh*3600/s.duration_s,sleepEnergyUwh:s.energy_uwh};
    });
    if(!samples.length)throw new Error('Diese Messung enthält noch keinen vollständig gültigen Sleep → Wake → Sleep-Zyklus.');
    return {samples,count:samples.length,voltageV:summary.voltage_v,
      interpolatedCount:summary.interpolated_cycle_count||0,
      statistics:{sleepEnergy:summary.sleep_variability?.energy_uwh||statistics(samples.map(s=>s.sleepEnergyUwh)),
        wakeEnergy:summary.wake_variability?.energy_uwh||statistics(samples.map(s=>s.wakeEnergyUwh)),
        sleepPower:statistics(samples.map(s=>s.sleepPowerUw))},
      wakeS:average(samples.map(s=>s.wakeS)),wakeEnergyUwh:average(samples.map(s=>s.wakeEnergyUwh)),
      sleepPowerUw:samples.reduce((n,s)=>n+s.sleepEnergyUwh,0)*3600/samples.reduce((n,s)=>n+s.sleepS,0),
      medianSleepS:quantile(samples.map(s=>s.sleepS),.5),
      medianDutyPct:quantile(samples.map(s=>100*s.wakeS/(s.wakeS+s.sleepS)),.5),
      medianWakeEnergyUwh:quantile(samples.map(s=>s.wakeEnergyUwh),.5)};
  }
  function evaluate(data,energyWh,mode,value){
    if(!Number.isFinite(energyWh)||energyWh<=0)throw new Error('Bitte eine positive nutzbare Batterieenergie eingeben.');
    if(!['sleep','duty'].includes(mode)||!Number.isFinite(value)||value<0
        ||(mode==='duty'&&(value<=0||value>100)))throw new Error('Sleep-Dauer muss mindestens 0 sein; Duty-Cycle muss über 0 und höchstens 100 % liegen.');
    const sleepS=mode==='sleep'?value:data.wakeS*(100/value-1);
    const powerUw=(data.wakeEnergyUwh*3600+data.sleepPowerUw*sleepS)/(data.wakeS+sleepS);
    const hours=power=>power===0?Infinity:energyWh*1e6/power;
    const scenarios=data.samples.map(s=>{
      const plannedSleep=mode==='sleep'?value:s.wakeS*(100/value-1);
      return hours((s.wakeEnergyUwh*3600+s.sleepPowerUw*plannedSleep)/(s.wakeS+plannedSleep));
    });
    const percentile=q=>data.weights?weightedQuantile(scenarios,data.weights,q):quantile(scenarios,q);
    return {expectedH:hours(powerUw),minH:percentile(0),p05H:percentile(.05),
      lowH:percentile(.1),highH:percentile(.9),p95H:percentile(.95),maxH:percentile(1),
      medianH:percentile(.5),powerUw,sleepS,dutyPct:100*data.wakeS/(data.wakeS+sleepS),scenariosH:scenarios};
  }
  function distribution(data,energyWh,mode,value){
    const point=evaluate(data,energyWh,mode,value);
    if(!point.scenariosH.every(Number.isFinite))return {point,bins:[],meanH:null,stddevH:null,varianceH2:null,reason:'Mindestens ein Szenario hat keinen Verbrauch und damit keine endliche Laufzeit. Eine endliche Verteilung ist nicht möglich.'};
    const weights=data.weights||data.samples.map(()=>1/data.count);
    const stats=weightedStatistics(point.scenariosH,weights);
    const meanH=stats.mean,stddevH=stats.stddev,varianceH2=stats.variance;
    if(point.minH===point.maxH)return {point,meanH,stddevH,varianceH2,bins:[],reason:stddevH==null?'Nur ein gültiger Zyklus: eine Laufzeit, keine Streuungsauswertung.':'Alle gemessenen Szenarien haben dieselbe Laufzeit; die Streuung ist null.'};
    const count=Math.min(30,Math.max(2,Math.ceil(Math.sqrt(data.count))));
    const width=(point.maxH-point.minH)/count,total=weights.reduce((a,b)=>a+b,0);
    const bins=Array.from({length:count},(_,i)=>({leftH:point.minH+i*width,rightH:i===count-1?point.maxH:point.minH+(i+1)*width,share:0}));
    point.scenariosH.forEach((hours,i)=>{const bin=Math.min(count-1,Math.floor((hours-point.minH)/width));bins[bin].share+=weights[i]/total});
    // Smooth the weighted observations in normalized coordinates. Reflections
    // reduce edge bias; numerical normalization keeps the bounded area at one.
    const range=point.maxH-point.minH;
    const positions=point.scenariosH.map(hours=>(hours-point.minH)/range);
    const normalized=weights.map(weight=>weight/total);
    const effectiveCount=1/normalized.reduce((sum,w)=>sum+w*w,0);
    const bandwidth=Math.max(1/240,Math.min(.5,1.06*(stddevH/range)*effectiveCount**(-.2)));
    const density=Array.from({length:241},(_,i)=>{
      const x=i/240;
      const value=positions.reduce((sum,p,j)=>sum+normalized[j]*
        (Math.exp(-.5*((x-p)/bandwidth)**2)+Math.exp(-.5*((x+p)/bandwidth)**2)+Math.exp(-.5*((x-(2-p))/bandwidth)**2)),0);
      return {hours:point.minH+x*range,value};
    });
    const area=density.slice(1).reduce((sum,p,i)=>sum+(p.value+density[i].value)/2/240,0);
    density.forEach(p=>{p.densityPerH=p.value/area/range;delete p.value});
    return {point,meanH,stddevH,varianceH2,bins,density,reason:''};
  }
  function combine(sources,weighting='mean'){
    if(!sources.length||!['mean','cycle_count','custom'].includes(weighting))throw new Error('Bitte Messungen und eine gültige Gewichtung auswählen.');
    if(new Set(sources.map(s=>s.id)).size!==sources.length)throw new Error('Eine Messung darf nur einmal ausgewählt werden.');
    const prepared=sources.map(source=>{
      const data=profile(source.summary),weight=weighting==='mean'?1:weighting==='cycle_count'?data.count:source.weight;
      if(!Number.isFinite(weight)||weight<0)throw new Error('Eigene Gewichte müssen endlich und mindestens 0 sein.');
      return {source,data,weight};
    });
    const total=prepared.reduce((n,s)=>n+s.weight,0);
    if(total<=0)throw new Error('Mindestens eine Messung muss ein positives Gewicht haben.');
    const samples=[],weights=[],details=[];
    for(const {source,data,weight} of prepared){
      const share=weight/total;
      details.push({id:source.id,name:source.name,count:data.count,weight,share,voltageV:data.voltageV});
      if(share)for(const sample of data.samples){samples.push(sample);weights.push(share/data.count)}
    }
    const weightedMean=key=>prepared.reduce((n,s)=>n+s.data[key]*s.weight/total,0);
    const percentile=(key,q=.5)=>weightedQuantile(samples.map(s=>s[key]),weights,q);
    return {samples,weights,sources:details,groups:prepared.filter(s=>s.weight>0).map(s=>({data:s.data,share:s.weight/total})),weighting,count:samples.length,
      activeMeasurementCount:details.filter(s=>s.share>0).length,
      voltageV:[...new Set(details.filter(s=>s.share>0).map(s=>s.voltageV))].join(' / '),
      interpolatedCount:prepared.reduce((n,s)=>n+(s.weight?s.data.interpolatedCount:0),0),
      wakeS:weightedMean('wakeS'),wakeEnergyUwh:weightedMean('wakeEnergyUwh'),sleepPowerUw:weightedMean('sleepPowerUw'),
      medianSleepS:percentile('sleepS'),medianDutyPct:weightedQuantile(samples.map(s=>100*s.wakeS/(s.wakeS+s.sleepS)),weights,.5),
      medianWakeEnergyUwh:percentile('wakeEnergyUwh'),
      statistics:{wakeEnergy:weightedStatistics(samples.map(s=>s.wakeEnergyUwh),weights),
        sleepEnergy:weightedStatistics(samples.map(s=>s.sleepEnergyUwh),weights),sleepPower:weightedStatistics(samples.map(s=>s.sleepPowerUw),weights)}};
  }
  function curve(data,energyWh,mode,min,max,count=121){
    if(!Number.isFinite(min)||!Number.isFinite(max)||min>=max)throw new Error('Das Kurvenmaximum muss größer als das Minimum sein.');
    return Array.from({length:count},(_,i)=>{
      const x=mode==='sleep'&&min>0?min*(max/min)**(i/(count-1)):min+(max-min)*i/(count-1);
      return {x,...evaluate(data,energyWh,mode,x)};
    });
  }
  function uncertainty(data,energyWh,mode,value){
    const original=evaluate(data,energyWh,mode,value),center=original.expectedH;
    const groups=data.groups||[{data,share:1}];
    const result={point:{...original},meanH:center,standardErrorH:null,logVariance:null,density:[],available:false,reason:''};
    if(!Number.isFinite(center)){result.reason='Kein mittlerer Verbrauch: keine endliche Laufzeitschätzung.';return result}
    if(groups.some(g=>g.data.count<2)){result.reason='Für die Unsicherheit sind mindestens zwei gültige Zyklen je Messung mit positivem Einfluss erforderlich.';return result}
    const t=original.sleepS,A=3600*data.wakeEnergyUwh+data.sleepPowerUw*t;
    const gW=-3600/A,gS=-t/A;
    const gD=mode==='sleep'?1/(data.wakeS+t):1/data.wakeS-data.sleepPowerUw*(100/value-1)/A;
    let logVariance=0;
    for(const group of groups){
      const d=group.data,meanSleep=average(d.samples.map(s=>s.sleepS));
      const influences=d.samples.map(s=>gW*(s.wakeEnergyUwh-d.wakeEnergyUwh)+gD*(s.wakeS-d.wakeS)+
        gS*(s.sleepEnergyUwh*3600-d.sleepPowerUw*s.sleepS)/meanSleep);
      logVariance+=group.share**2*statistics(influences).variance/d.count;
    }
    const sigma=Math.sqrt(Math.max(0,logVariance));
    result.available=true;result.logVariance=logVariance;result.standardErrorH=center*sigma;
    const q=z=>center*Math.exp(z*sigma);
    Object.assign(result.point,{minH:q(-4),p05H:q(-1.6448536269514722),lowH:q(-1.2815515655446004),medianH:center,highH:q(1.2815515655446004),p95H:q(1.6448536269514722),maxH:q(4)});
    if(!Number.isFinite(result.point.maxH)){result.available=false;result.reason='Die Unsicherheit ist zu groß für eine endliche Näherung. Weitere repräsentative Messungen sind nötig.';return result}
    if(sigma===0){result.reason='Keine Streuung in den beobachteten Zyklen: geschätzte statistische Unsicherheit null; systematische Fehler sind nicht enthalten.';return result}
    result.density=Array.from({length:321},(_,i)=>{
      const z=-4+8*i/320,hours=q(z);
      return {hours,densityPerH:Math.exp(-z*z/2)/(Math.sqrt(2*Math.PI)*sigma*hours)};
    });
    result.reason='';return result;
  }
  return {profile,combine,evaluate,curve,distribution,uncertainty};
})();

const BatteryLifeUI=(()=>{
  const el=id=>document.getElementById(`battery${id}`);
  const timeUnits={seconds:1,minutes:60,hours:3600};
  let data=null,requestToken=0,renderToken=0,timer=null,renderChain=Promise.resolve();
  let available=[];const selected=new Map(),summaries=new Map();
  const life=hours=>!Number.isFinite(hours)?'Kein Verbrauch im Modell':hours>=8760?`${(hours/8760).toLocaleString('de-DE',{maximumFractionDigits:2})} Jahre`:hours>=48?`${(hours/24).toLocaleString('de-DE',{maximumFractionDigits:2})} Tage`:`${hours.toLocaleString('de-DE',{maximumFractionDigits:2})} Stunden`;
  const finite=value=>Number.isFinite(value)?value:null;
  const mode=()=>el('Mode').value;
  const factor=()=>mode()==='sleep'?timeUnits[el('TimeUnit').value]:1;
  const unit=()=>mode()==='sleep'?({seconds:'s',minutes:'min',hours:'h'}[el('TimeUnit').value]):'%';
  const number=value=>value.toLocaleString('de-DE',{maximumSignificantDigits:5});
  function status(message,error=false){el('Status').textContent=message;el('Status').classList.toggle('event-load-error',error)}
  function clear(){
    ++renderToken;clearTimeout(timer);
    for(const id of ['Runtime','Band','Power','Timing','WakeEnergy'])el(id).textContent='—';
    el('DistributionNote').textContent='';el('MeasuredMedian').textContent='';
    el('PercentileRows').innerHTML='';el('StatisticsRows').innerHTML='';el('Export').disabled=true;
    Plotly.purge(el('Chart'));
  }
  function resetTiming(){
    const isSleep=mode()==='sleep';
    el('TimeUnit').disabled=!isSleep;
    el('ValueLabel').textContent=isSleep?'Sleep-Dauer zwischen Wakes':'Wake-Duty-Cycle';
    el('Value').min=isSleep?'0':'0.000001';el('Value').max=isSleep?'':'100';
    const typical=data?(isSleep?data.medianSleepS/factor():data.medianDutyPct):(isSleep?60/factor():1);
    const selected=Math.max(isSleep?.001:.000001,Math.min(isSleep?Infinity:100,typical));
    el('Value').value=Number(selected.toPrecision(6));
    schedule();
  }
  function settings(){
    for(const id of ['Energy','Value']){
      if(!el(id).value.trim()||!Number.isFinite(Number(el(id).value)))throw new Error('Bitte alle Eingabefelder mit gültigen Zahlen ausfüllen.');
    }
    const energyWh=Number(el('Energy').value)*(el('EnergyUnit').value==='mWh'?.001:1);
    const value=Number(el('Value').value)*factor();
    return {energyWh,value};
  }
  function schedule(){clearTimeout(timer);timer=setTimeout(render,100)}
  function render(){
    if(!data)return;
    const token=++renderToken;
    try{
      const {energyWh,value}=settings();
      const distribution=BatteryLifeModel.uncertainty(data,energyWh,mode(),value);
      const point=distribution.point,hasCurve=distribution.density.length>0;
      const median=mode()==='sleep'?data.medianSleepS:data.medianDutyPct;
      el('MeasuredMedian').textContent=`Gemessener Median: ${number(median/factor())} ${unit()}`;
      el('Runtime').textContent=life(point.expectedH);
      el('Band').textContent=distribution.available?`${life(point.p05H)} bis ${life(point.p95H)}`:'Nicht schätzbar';
      el('Power').textContent=point.powerUw>=1000?`${number(point.powerUw/1000)} mW`:`${number(point.powerUw)} µW`;
      el('Timing').textContent=`Sleep ${fmtDuration(point.sleepS)} · Wake ${fmtEventDuration(data.wakeS*1e6)} · Duty ${number(point.dutyPct)} %`;
      el('WakeEnergy').textContent=fmtEnergy(data.medianWakeEnergyUwh);
      el('DistributionNote').textContent=(distribution.reason||`Standardunsicherheit der Laufzeitschätzung: ${life(distribution.standardErrorH)}. Die blaue Kurve zeigt die angenäherte statistische Unsicherheit der mittleren Laufzeit; der schattierte Bereich ist ein näherungsweises 90-%-Intervall.`)+' Die Berechnung setzt unabhängige, repräsentative Zyklen und unabhängige Messungen voraus. Zusammenhänge innerhalb eines Sleep-/Wake-Paars werden berücksichtigt. Messungsanteile und Batterieenergie gelten als fest. Bei wenigen Zyklen oder großer Streuung ist die Näherung eingeschränkt. Zeitliche Abhängigkeiten, systematische Messfehler, Alterung und Selbstentladung sind nicht enthalten.';
      el('PercentileRows').innerHTML=[['Untere 90-%-Grenze · P5',5,'p05H'],['P10',10,'lowH'],['Laufzeitschätzung · P50',50,'medianH'],['P90',90,'highH'],['Obere 90-%-Grenze · P95',95,'p95H']].map(([label,p,key])=>`<tr><td>${label}</td><td>${p} %</td><td>${distribution.available?life(point[key]):'Nicht schätzbar'}</td></tr>`).join('');
      el('StatisticsRows').innerHTML=[['Wake-Energie','wakeEnergy','µWh'],['Sleep-Energie · gemessene Dauer','sleepEnergy','µWh'],['Sleep-Leistung · auf Dauer normiert','sleepPower','µW']].map(([label,key,u])=>`<tr><td>${label}</td>${[['mean',u],['min',u],['max',u],['stddev',u],['variance',u+'²']].map(([field,unit])=>`<td>${data.statistics[key][field]==null?'—':number(data.statistics[key][field])+' '+unit}</td>`).join('')}</tr>`).join('');
      el('Export').disabled=false;
      status(`${data.sources.length} ausgewählte Messungen · ${data.activeMeasurementCount} mit positivem Einfluss · ${data.count} gültige Zyklen bei ${data.voltageV} V${data.interpolatedCount?` · ${data.interpolatedCount} Zyklen mit ergänzten Datenlücken`:''}. ${data.count<2?'Für eine Streuungsauswertung sind mindestens zwei Zyklen nötig.':''}`);
      const display=point.expectedH>=8760?{factor:8760,label:'Jahre'}:point.expectedH>=2160?{factor:730,label:'Monate'}:point.expectedH>=336?{factor:168,label:'Wochen'}:{factor:24,label:'Tage'};
      const traces=[],shapes=[],annotations=[];
      if(hasCurve)traces.push({x:distribution.density.map(p=>p.hours/display.factor),y:distribution.density.map(p=>p.densityPerH*display.factor*100),type:'scatter',mode:'lines',line:{color:'#6ea8fe',width:2.5,shape:'linear'},fill:'tozeroy',fillcolor:'rgba(110,168,254,.18)',name:'Unsicherheit der mittleren Laufzeit',hovertemplate:`Laufzeit: %{x:.3f} ${display.label}<br>Dichte: %{y:.3f} % / ${display.label}<extra></extra>`});
      if(hasCurve)shapes.push({type:'rect',xref:'x',yref:'paper',x0:point.p05H/display.factor,x1:point.p95H/display.factor,y0:0,y1:1,fillcolor:'rgba(110,168,254,.18)',line:{width:0},layer:'below'});
      const markers=[[point.expectedH,'Laufzeitschätzung aus mittlerer Leistung','#65d98b','solid']];
      if(hasCurve)markers.push([point.p05H,'P5 · Näherung','#a7c9ff','dot'],[point.p95H,'P95 · Näherung','#a7c9ff','dot']);
      for(const [hours,label,color,dash] of markers){
        if(!Number.isFinite(hours))continue;
        shapes.push({type:'line',xref:'x',yref:'paper',x0:hours/display.factor,x1:hours/display.factor,y0:0,y1:1,line:{color,width:label.startsWith('Laufzeitschätzung')?3:1,dash}});
        traces.push({x:[hours/display.factor,hours/display.factor],y:[0,null],type:'scatter',mode:'lines',line:{color,width:2,dash},name:label,hoverinfo:'skip'});
        if(label.startsWith('P5')||label.startsWith('P95'))annotations.push({xref:'x',yref:'paper',x:hours/display.factor,y:.7,text:label.slice(0,3),showarrow:false,font:{color:'#a7c9ff',size:11}});
        if(label.startsWith('Laufzeitschätzung'))annotations.push({xref:'x',yref:'paper',x:hours/display.factor,y:1,text:`Laufzeitschätzung: ${life(hours)}`,showarrow:true,ax:0,ay:-24,arrowcolor:color,font:{color,size:12},bgcolor:'#121820'});
      }
      if(!hasCurve&&Number.isFinite(point.expectedH))traces.push({x:[point.expectedH/display.factor],y:[100],type:'scatter',mode:'markers',marker:{color:'#65d98b',size:10},showlegend:false,hovertemplate:`Laufzeit: %{x:.3f} ${display.label}<extra></extra>`});
      const min=hasCurve?point.minH:point.expectedH,max=hasCurve?point.maxH:point.expectedH,padding=hasCurve?0:Math.max(max*.01,1e-9);
      if(!hasCurve)annotations.push({xref:'paper',yref:'paper',x:.5,y:.85,text:distribution.reason,showarrow:false,font:{color:'#aeb8c2',size:11}});
      const compact=el('Chart').clientWidth>0&&el('Chart').clientWidth<500;
      const layout={paper_bgcolor:'transparent',plot_bgcolor:'transparent',font:{color:'#c8d0d8',size:12},margin:{l:compact?66:82,r:24,t:compact?160:70,b:86},
        xaxis:{title:{text:`Batterielaufzeit [${display.label}]`,standoff:16},range:Number.isFinite(max)?[Math.max(0,min-padding)/display.factor,(max+padding)/display.factor]:undefined,type:'linear',gridcolor:'#252d35',zeroline:false},
        yaxis:{title:{text:hasCurve?`Unsicherheitsdichte [% / ${display.label}]`:'Laufzeitmarkierung',standoff:16},gridcolor:'#252d35',rangemode:'tozero',zeroline:false},
        annotations,legend:{orientation:'h',x:0,y:1.03,yanchor:'bottom',font:{size:10}},shapes,hovermode:'x unified'};
      renderChain=renderChain.catch(()=>{}).then(async()=>{
        if(token!==renderToken||state.view!=='battery')return;
        await Plotly.react(el('Chart'),traces,layout,{responsive:true,displaylogo:false,scrollZoom:false});
      }).catch(error=>{if(token===renderToken)status(`Chart konnte nicht dargestellt werden: ${error.message}`,true)});
    }catch(error){clear();status(error.message,true)}
  }
  function updateShares(){
    document.querySelectorAll('[data-battery-share]').forEach(cell=>{
      const source=data?.sources.find(s=>s.id===cell.dataset.batteryShare);
      cell.textContent=source?`${number(source.share*100)} %`:'—';
    });
    document.querySelectorAll('[data-battery-count]').forEach(cell=>{
      const summary=summaries.get(cell.dataset.batteryCount);cell.textContent=summary?String(summary.cycle_count):'—';
    });
  }
  function renderSources(){
    const search=el('Search').value.trim().toLocaleLowerCase('de-DE'),custom=el('Weighting').value==='custom';
    el('SourceRows').innerHTML=available.filter(m=>!search||`${m.name} ${m.project||''} ${m.device||''}`.toLocaleLowerCase('de-DE').includes(search)).map(m=>{
      const checked=selected.has(m.id),id=escapeHtml(m.id);
      return `<tr><td><input type="checkbox" data-battery-select="${id}" ${checked?'checked':''} aria-label="Messung ${escapeHtml(m.name)} einbeziehen"></td><td><strong>${escapeHtml(m.name)}</strong><small>${escapeHtml(fmtDate(m.started_at||m.created_at))}</small></td><td data-battery-count="${id}">—</td><td><input type="number" min="0" step="any" value="${selected.get(m.id)??1}" data-battery-weight="${id}" ${!checked||!custom?'disabled':''} aria-label="Eigenes Gewicht für ${escapeHtml(m.name)}"></td><td data-battery-share="${id}">—</td></tr>`;
    }).join('')||'<tr><td colspan="5">Keine passende abgeschlossene Messung vorhanden.</td></tr>';
    el('SelectionCount').textContent=`${selected.size} Messungen ausgewählt`;
    document.querySelectorAll('[data-battery-select]').forEach(input=>input.addEventListener('change',()=>{
      if(input.checked)selected.set(input.dataset.batterySelect,1);else selected.delete(input.dataset.batterySelect);
      renderSources();loadMeasurements();
    }));
    document.querySelectorAll('[data-battery-weight]').forEach(input=>input.addEventListener('input',()=>{
      selected.set(input.dataset.batteryWeight,input.value.trim()?Number(input.value):NaN);loadMeasurements();
    }));
    updateShares();
  }
  async function loadMeasurements({reset=false}={}){
    const token=++requestToken;data=null;clear();status('Messdaten werden geladen …');
    updateShares();
    const ids=[...selected.keys()],weighting=el('Weighting').value;
    if(!ids.length){status('Wähle mindestens eine abgeschlossene Messung mit gültigen Zyklen.');return}
    try{
      const results=await Promise.allSettled(ids.map(async id=>{
        if(!summaries.has(id))summaries.set(id,await api(`/api/measurements/${encodeURIComponent(id)}/cycle-energy`));
        return {id,name:available.find(m=>m.id===id)?.name||id,summary:summaries.get(id),weight:selected.get(id)};
      }));
      if(token!==requestToken||state.view!=='battery')return;
      const failed=results.findIndex(r=>r.status==='rejected');
      if(failed>=0)throw new Error(`${available.find(m=>m.id===ids[failed])?.name||ids[failed]}: ${results[failed].reason.message}`);
      const sources=results.map(r=>r.value);
      for(const source of sources){try{BatteryLifeModel.profile(source.summary)}catch(error){throw new Error(`${source.name}: ${error.message}`)}}
      data=BatteryLifeModel.combine(sources,weighting);updateShares();
      if(reset)resetTiming();else schedule();
    }catch(error){if(token===requestToken&&state.view==='battery'){updateShares();status(error.message,true)}}
  }
  async function open(preferredId){
    const token=++requestToken;data=null;clear();status('Messungen werden geladen …');
    try{
      const measurements=await api('/api/measurements?limit=2000');
      if(token!==requestToken||state.view!=='battery')return;
      available=measurements.filter(m=>['completed','failed','cancelled'].includes(m.status));
      for(const id of selected.keys())if(!available.some(m=>m.id===id))selected.delete(id);
      if(preferredId&&available.some(m=>m.id===preferredId))selected.set(preferredId,selected.get(preferredId)??1);
      if(!selected.size&&available.length)selected.set(available[0].id,1);
      renderSources();await loadMeasurements({reset:true});
    }catch(error){if(token===requestToken)status(error.message,true)}
  }
  async function exportPdf(){
    if(!data)return;
    const ids=[...selected.keys()],weighting=el('Weighting').value;
    el('Export').disabled=true;
    try{
      render();const token=renderToken;
      const values=settings();
      if(el('Status').classList.contains('event-load-error'))return;
      await renderChain;
      const graph=el('Chart'),printable=document.createElement('div');
      printable.style.cssText='position:fixed;left:-10000px;top:0;width:1400px;height:700px';
      document.body.appendChild(printable);
      let chart;
      try{
        const layout={...graph.layout,paper_bgcolor:'#ffffff',plot_bgcolor:'#ffffff',font:{color:'#23323e',size:16},
          legend:{...graph.layout.legend,font:{color:'#23323e',size:14}},
          xaxis:{...graph.layout.xaxis,gridcolor:'#d7e0e5',color:'#23323e'},
          yaxis:{...graph.layout.yaxis,gridcolor:'#d7e0e5',color:'#23323e'}};
        const traces=graph.data.map(trace=>({...trace,line:{...trace.line,color:trace.line?.color==='#65d98b'?'#16713b':trace.line?.color==='#6ea8fe'?'#2366ac':trace.line?.color}}));
        await Plotly.newPlot(printable,traces,layout,{displaylogo:false});
        chart=await Plotly.toImage(printable,{format:'png',width:1400,height:700,scale:1});
      }finally{Plotly.purge(printable);printable.remove()}
      if(token!==renderToken||ids.join(',')!==[...selected.keys()].join(',')||state.view!=='battery')throw new Error('Die Eingaben haben sich während des Exports geändert. Bitte erneut exportieren.');
      const response=await fetch('/api/battery-report',{
        method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({energy_wh:values.energyWh,
          mode:mode(),value:values.value,chart_png:chart,
          weighting,sources:ids.map(id=>({measurement_id:id,weight:weighting==='custom'?selected.get(id):1}))})});
      if(!response.ok){const error=await response.json();throw new Error(error.detail||'PDF-Export fehlgeschlagen.')}
      const url=URL.createObjectURL(await response.blob()),link=document.createElement('a');
      link.href=url;link.download='battery_life_combined.pdf';link.click();setTimeout(()=>URL.revokeObjectURL(url),1000);
    }catch(error){status(typeof error.message==='string'?error.message:'PDF-Export fehlgeschlagen.',true)}
    finally{el('Export').disabled=!data||el('Status').classList.contains('event-load-error')}
  }
  function install(){
    el('Export').addEventListener('click',exportPdf);
    el('Search').addEventListener('input',renderSources);
    el('Weighting').addEventListener('change',()=>{renderSources();loadMeasurements()});
    el('ClearSelection').addEventListener('click',()=>{selected.clear();renderSources();loadMeasurements()});
    el('Refresh').addEventListener('click',()=>{summaries.clear();open()});
    el('Mode').addEventListener('change',resetTiming);
    let lastTimeFactor=timeUnits[el('TimeUnit').value];
    el('TimeUnit').addEventListener('change',()=>{
      const next=timeUnits[el('TimeUnit').value];
      el('Value').value=Number((Number(el('Value').value)*lastTimeFactor/next).toPrecision(8));
      lastTimeFactor=next;schedule();
    });
    for(const id of ['Energy','EnergyUnit','Value'])el(id).addEventListener('input',schedule);
  }
  return {open,install};
})();
