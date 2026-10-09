/* Detector tuning on a temporary PPK2 stream; settings live in the measurement form. */
const MeasurementPreview=(()=>{
  const fields=['port','meter_mode','voltage_mv','detection_mode','sleep_threshold_ua','wake_threshold_ua','sleep_min_s','wake_min_ms','pre_trigger_ms','post_trigger_ms','spectral_margin_db'];
  let id=null,ws=null,frame=null,generation=0,startTask=null,stopTask=null;
  let updateChain=Promise.resolve(),updateTimer=null,lastRequestedKey=null,updateError=null;
  let pendingRender=false,rendering=false,scale='log',follow=true,range=null;
  const key=settings=>JSON.stringify(fields.map(n=>settings[n]));
  const status=text=>$('measurementPreviewStatus').textContent=text;
  function payload(){
    const settings=measurementPayload(),out={};
    fields.forEach(n=>out[n]=settings[n]);
    if(!out.port)throw new Error('Bitte zuerst einen PPK2 auswählen.');
    if(fields.filter(n=>!['port','meter_mode','detection_mode'].includes(n)).some(n=>!Number.isFinite(out[n])))throw new Error('Gültige Triggerparameter eingeben.');
    if(out.sleep_threshold_ua<=0||out.wake_threshold_ua<=out.sleep_threshold_ua)throw new Error('Die Wake-Schwelle muss über der Sleep-Schwelle liegen. Beide Stromschwellen müssen positiv sein.');
    return out;
  }
  function controls(){
    $('measurementPreviewStart').disabled=!!startTask||!!stopTask;
    $('measurementPreviewStart').textContent=id?'Vorschau neu starten':'Vorschau starten';
    $('measurementPreviewStop').disabled=!id;
    $('measurementPreviewPause').disabled=!id||!!frame?.paused||!!frame?.error;
    $('measurementDialog').classList.toggle('with-preview',!!id);
    $('measurementPreviewPlots').classList.toggle('hidden',!id);
    $('measurementPreviewScorePanel').classList.toggle('hidden',$('detectionMode').value!=='spectral_compare');
  }
  async function start(){
    if(startTask)return startTask;
    await stop();
    const settings=payload(),token=++generation;
    status('PPK2-Vorschau startet …');
    startTask=(async()=>{
      const result=await api('/api/measurement-previews',{method:'POST',body:JSON.stringify(settings)});
      if(token!==generation){await api(`/api/measurement-previews/${result.preview_id}`,{method:'DELETE'});return;}
      id=result.preview_id;frame=null;lastRequestedKey=key(settings);updateError=null;follow=true;range=null;
      const proto=location.protocol==='https:'?'wss:':'ws:';
      const socket=new WebSocket(`${proto}//${location.host}/ws/measurement-previews/${id}`);ws=socket;
      socket.onmessage=event=>{
        if(ws!==socket||token!==generation)return;
        const incoming=JSON.parse(event.data),spectral=incoming.spectrogram;
        const retained=spectral.reset?[]:frame?.spectrogram?.frames||[];
        const cursor=retained.at(-1)?.end_sample??-1;
        const frames=[...retained,...spectral.frames.filter(f=>f.end_sample>cursor)].filter(f=>f.t_s>=incoming.start_s).slice(-480);
        frame={...incoming,spectrogram:{...spectral,frames}};
        controls();queueRender();
      };
      socket.onclose=()=>{
        if(ws===socket){status(frame?.error||'Verbindung zur Vorschau beendet. Vorschau neu starten.');$('measurementPreviewPause').disabled=true;}
      };
      controls();
    })();
    controls();
    try{await startTask;}finally{startTask=null;controls();}
  }
  async function stop(){
    if(stopTask)return stopTask;
    ++generation;clearTimeout(updateTimer);updateTimer=null;
    stopTask=(async()=>{
      if(startTask)await startTask.catch(()=>{});
      await updateChain.catch(()=>{});
      if(id)await api(`/api/measurement-previews/${id}`,{method:'DELETE'});
      const socket=ws;ws=null;socket?.close();id=null;frame=null;lastRequestedKey=null;updateError=null;
      status('Die Vorschau erfasst temporäre Daten des ausgewählten PPK2. Mit „Messung anlegen“ wird die Konfiguration für die Aufzeichnung übernommen.');
    })();
    controls();
    try{await stopTask;}finally{stopTask=null;controls();}
  }
  async function pause(){
    if(!id)return;
    $('measurementPreviewPause').disabled=true;
    status('Vorschau wird angehalten …');
    await api(`/api/measurement-previews/${id}/pause`,{method:'POST'});
  }
  function flushUpdate(){
    clearTimeout(updateTimer);updateTimer=null;
    if(!id)return updateChain;
    const settings=payload(),requestedKey=key(settings),previewId=id;
    if(frame&&(settings.port!==frame.settings.port||settings.meter_mode!==frame.settings.meter_mode||settings.voltage_mv!==frame.settings.voltage_mv))throw new Error('Gerät, Modus oder Spannung geändert. Bitte die Vorschau neu starten.');
    if(requestedKey===lastRequestedKey&&!updateError)return updateChain;
    lastRequestedKey=requestedKey;updateError=null;
    status('Triggerparameter werden anhand der letzten 60 s erneut ausgewertet …');
    updateChain=updateChain.catch(()=>{}).then(async()=>{
      try{await api(`/api/measurement-previews/${previewId}`,{method:'PATCH',body:JSON.stringify(settings)});}
      catch(error){updateError=error;status(error.message);throw error;}
    });
    return updateChain;
  }
  function changed(event){
    controls();
    if(!id)return;
    const name=event.target.name||event.target.closest('.current-input')?.querySelector('input')?.name||event.target.previousElementSibling?.name;
    if(!fields.includes(name))return;
    if(['port','meter_mode','voltage_mv'].includes(name)){
      stop().then(()=>status('Geräteeinstellungen geändert. Vorschau mit den neuen Werten starten.')).catch(error=>status(error.message));return;
    }
    status('Messparameter geändert · Erkennung wird neu ausgewertet …');queueRender();
    clearTimeout(updateTimer);
    updateTimer=setTimeout(()=>{try{flushUpdate().catch(()=>{});}catch(error){status(error.message);}},400);
  }
  function setCurrent(name,value){
    if(!Number.isFinite(value)||value<=0)return;
    const input=$('measurementForm').elements[name];
    const factor={nA:.001,'µA':1,mA:1000}[input.dataset.currentUnit]||1;
    input.value=String(Number((value/factor).toPrecision(7)));
    input.dispatchEvent(new Event('input',{bubbles:true}));
  }
  function thresholdShapes(settings){
    return [['sleep_threshold_ua','#60a5fa'],['wake_threshold_ua','#f5bd5b']].map(([name,color])=>({
      type:'line',xref:'paper',x0:0,x1:1,yref:'y',y0:settings[name],y1:settings[name],editable:true,
      line:{color,width:2,dash:'dash'},label:{text:name.startsWith('sleep')?'Sleep-Schwelle':'Wake-Schwelle',font:{color,size:10}}}));
  }
  function bindCurrent(){
    const el=$('measurementPreviewCurrent');if(el.dataset.bound==='1')return;el.dataset.bound='1';
    el.on('plotly_relayout',event=>{
      if(rendering)return;
      for(let i=0;i<2;i++){
        const value=event[`shapes[${i}].y0`];
        if(value!==undefined)setCurrent(i===0?'sleep_threshold_ua':'wake_threshold_ua',Number(value));
      }
      const next=event['xaxis.range']||[event['xaxis.range[0]'],event['xaxis.range[1]']];
      if(next.every(v=>v!==undefined)){follow=false;range=next.map(Number);}
    });
    el.addEventListener('pointerdown',event=>{
      const tool=$('measurementPreviewTool').value;if(tool==='navigate')return;
      const axis=el._fullLayout?.yaxis,rect=el.getBoundingClientRect();if(!axis)return;
      const x=event.clientX-rect.left,y=event.clientY-rect.top-axis._offset;
      if(x<el._fullLayout.xaxis._offset||x>el._fullLayout.xaxis._offset+el._fullLayout.xaxis._length||y<0||y>axis._length)return;
      setCurrent(tool==='wake'?'wake_threshold_ua':'sleep_threshold_ua',axis.p2d(y));
    },true);
  }
  async function render(data){
    const token=generation;
    const settings=payload(),matches=key(settings)===key(data.settings),markers=matches?data.state_markers:[];
    const points=data.summary_points;
    const x=[],mean=[],lo=[],hi=[];
    points.forEach((p,i)=>{
      if(i&&p.sample_index>points[i-1].end_sample+1){x.push(null);mean.push(null);lo.push(null);hi.push(null);}
      x.push((p.t_s+p.end_s)/2);mean.push(p.current_ua);lo.push(p.min_ua);hi.push(p.max_ua);
    });
    const transform=values=>values.map(v=>v===null?null:scale==='log'?Math.max(.001,v):v);
    const extent=[...mean.filter(v=>v!==null),...hi.filter(v=>v!==null),settings.sleep_threshold_ua,settings.wake_threshold_ua];
    const minimum=Math.min(...lo.filter(v=>v!==null),settings.sleep_threshold_ua),maximum=Math.max(...extent,1);
    const chartWidth=$('measurementPreviewCurrent').clientWidth,compactChart=chartWidth<480;
    const common={autosize:true,width:Math.max(220,chartWidth),paper_bgcolor:'transparent',plot_bgcolor:'transparent',font:{family:'Segoe UI, Inter, system-ui, sans-serif',color:'#c8d0d8',size:compactChart?10:11},margin:{l:compactChart?52:75,r:compactChart?12:25,t:15,b:50},showlegend:false};
    const xaxis={title:{text:compactChart?'Vorschau-Zeit [s]':'Zeit seit Vorschau-Start [s]'},range:follow?[data.start_s,Math.max(.25,data.latest_s)]:range,gridcolor:'#222a31'};
    const traces=[{type:'scatter',mode:'lines',x,y:transform(lo),line:{width:0},hoverinfo:'skip'},
      {type:'scatter',mode:'lines',x,y:transform(hi),line:{width:0},fill:'tonexty',fillcolor:'#65d98b22',hoverinfo:'skip'},
      {type:'scatter',mode:'lines',x,y:transform(mean),line:{color:'#65d98b',width:1.4},name:'Strom',hovertemplate:'%{x:.4f} s<br>%{y:.4g} µA<extra></extra>'}];
    // Trigger guides are traces, so only the two threshold shapes can move.
    for(const kind of new Set(markers.map(m=>m.kind))){
      const group=markers.filter(m=>m.kind===kind);
      traces.push({type:'scatter',mode:'lines',x:group.flatMap(m=>[m.t_s,m.t_s,null]),
        y:group.flatMap(()=>[scale==='log'?Math.max(.001,minimum)/2:Math.min(0,minimum),maximum*2,null]),
        line:{color:stateMarkerColor(group[0]),width:1,dash:kind.endsWith('validated')?'dot':kind.startsWith('fft_')?'dash':'solid'},opacity:.3,hoverinfo:'skip'});
    }
    if(markers.length)traces.push({type:'scatter',mode:'markers',x:markers.map(m=>m.t_s),y:transform(markers.map(m=>m.current_ua)),
      text:markers.map(stateMarkerLabel),marker:{color:markers.map(stateMarkerColor),symbol:markers.map(m=>m.kind.endsWith('validated')?'circle-open':'circle'),size:8},hovertemplate:'%{text}<br>%{x:.6f} s<extra></extra>'});
    await Plotly.react('measurementPreviewCurrent',traces,{...common,xaxis,
      yaxis:{title:{text:'Strom [µA]'},type:scale,range:scale==='log'?[Math.log10(Math.max(.001,minimum))-.3,Math.log10(maximum)+.3]:[Math.min(0,minimum),maximum*1.15],gridcolor:'#222a31'},
      shapes:thresholdShapes(settings),
      dragmode:$('measurementPreviewTool').value==='navigate'?'zoom':false,
      uirevision:`preview-${id}-${scale}`},{...plotConfig,edits:{shapePosition:true}});
    if(token!==generation)return;
    bindCurrent();
    const spectral=data.spectrogram,frames=spectral.frames,tx=[],columns=[];
    frames.forEach((f,i)=>{if(i&&f.t_s-frames[i-1].t_s>spectral.hop_s*1.01){tx.push((f.t_s+frames[i-1].t_s)/2);columns.push(null);}tx.push(f.t_s);columns.push(f.psd_db);});
    await Plotly.react('measurementPreviewFft',[{type:'heatmap',x:tx,y:spectral.frequencies_hz,
      z:spectral.frequencies_hz.map((_,i)=>columns.map(c=>c?c[i]:null)),colorscale:'Viridis',zmin:-100,zmax:60,connectgaps:false,
      colorbar:{title:{text:'dB re<br>1 µA²/Hz',font:{size:compactChart?9:11}},thickness:compactChart?9:12},hovertemplate:'%{x:.3f} s<br>%{y:.1f} Hz<br>%{z:.1f} dB<extra></extra>'}],
      {...common,margin:{l:compactChart?52:75,r:compactChart?65:100,t:10,b:50},xaxis,yaxis:{title:{text:'Frequenz [Hz]'},type:'log'},shapes:sleepStartShapes(markers)},plotConfig);
    if(token!==generation)return;
    if(settings.detection_mode==='spectral_compare'){
      const scores=data.spectral_scores,margin=settings.spectral_margin_db;
      await Plotly.react('measurementPreviewScore',[{type:'scatter',mode:'lines',x:scores.map(p=>p.t_s),y:scores.map(p=>p.score_db),connectgaps:false,line:{color:'#f472b6'},hovertemplate:'%{x:.3f} s<br>%{y:.1f} dB Abweichung<extra></extra>'},
        {type:'scatter',mode:'lines',x:[data.start_s,data.latest_s],y:[margin/2,margin/2],line:{color:'#22d3ee',width:1,dash:'dot'},hovertemplate:'FFT Sleep unter %{y:.1f} dB<extra></extra>'}],
        {...common,xaxis,yaxis:{title:{text:'Abweichung [dB]'},range:[Math.min(-5,...scores.filter(p=>p.score_db!==null).map(p=>p.score_db))-3,Math.max(margin,...scores.filter(p=>p.score_db!==null).map(p=>p.score_db),10)+5]},shapes:[{type:'line',xref:'paper',x0:0,x1:1,y0:margin,y1:margin,editable:true,line:{color:'#f472b6',dash:'dash'},label:{text:'FFT Wake'}},
          ]},
        {...plotConfig,edits:{shapePosition:true}});
      const el=$('measurementPreviewScore');
      if(el.dataset.bound!=='1'){el.dataset.bound='1';el.on('plotly_relayout',event=>{
        if(rendering||event['shapes[0].y0']===undefined)return;
        const input=$('measurementForm').elements.spectral_margin_db;input.value=String(Math.min(60,Math.max(3,Number(event['shapes[0].y0']))));input.dispatchEvent(new Event('input',{bubbles:true}));
      });}
    }
    const spectralStatus=data.spectral_comparison;
    if(token!==generation)return;
    status(data.error||(!matches?'Messparameter geändert · Erkennung wird neu ausgewertet …':
      `${data.paused?'Angehalten':'Live'} · Zustandserkennung: ${data.state}${spectralStatus?` · FFT: ${spectralStatus.state}${spectralStatus.score_db==null?` (${Math.round(spectralStatus.training_s)}/${spectralStatus.reference_s} s Referenz)`:` (${spectralStatus.score_db.toFixed(1)} dB)`}`:''} · ${markers.filter(m=>m.kind==='wake_validated').length} bestätigte Wake-Trigger im sichtbaren Bereich`));
  }
  async function queueRender(){
    pendingRender=true;if(rendering)return;
    rendering=true;
    try{while(pendingRender){pendingRender=false;if(frame&&id&&$('measurementDialog').open)await render(frame);}}
    catch(error){status(error.message);}
    finally{rendering=false;}
  }
  async function beforeSave(){
    if(id){if(ws?.readyState===WebSocket.OPEN&&!frame?.error){await flushUpdate();if(updateError)throw updateError;}await stop();}
    else if(startTask)await stop();
  }
  async function reset(){await stop();scale='log';follow=true;range=null;$('measurementPreviewTool').value='navigate';}
  function install(){
    const onClick=(button,action)=>$(button).addEventListener('click',()=>action().catch(error=>{status(error.message);toast(error.message,true);}));
    onClick('measurementPreviewStart',start);onClick('measurementPreviewStop',stop);onClick('measurementPreviewPause',pause);
    $('measurementForm').addEventListener('input',changed);$('measurementForm').addEventListener('change',changed);
    $('measurementDialog').addEventListener('close',()=>{
      const form=$('measurementForm');form.dataset.dialogToken=String(Number(form.dataset.dialogToken||0)+1);
      stop().catch(error=>toast(error.message,true));
    });
    $('measurementPreviewScale').addEventListener('change',event=>{scale=event.target.value;queueRender();});
    $('measurementPreviewTool').addEventListener('change',()=>queueRender());
    $('measurementPreviewFollow').addEventListener('click',()=>{follow=true;range=null;queueRender();});
    controls();
  }
  return {install,reset,stop,beforeSave};
})();
