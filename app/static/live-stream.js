/* Weighted acquisition summaries for a bounded, continuously growing overview. */
function compactLiveSummaries(points,budget){
  if(points.length<=budget)return points;
  let width=Math.max(1,Math.ceil((points.at(-1).end_sample-points[0].sample_index+1)/budget));
  for(;;width*=2){
    const out=[];
    for(const point of points){
      const previous=out.at(-1);
      if(previous&&previous.line_key===point.line_key&&previous.end_sample+1===point.sample_index&&Math.floor(previous.sample_index/width)===Math.floor(point.sample_index/width)){
        const count=previous.sample_count+point.sample_count;
        const sum=(previous.sum_ua??previous.current_ua*previous.sample_count)+(point.sum_ua??point.current_ua*point.sample_count);
        out[out.length-1]={...previous,end_sample:point.end_sample,end_s:point.end_s,
          display_end_s:point.display_end_s??point.end_s,last_ua:point.last_ua,
          sample_count:count,sum_ua:sum,current_ua:sum/count,
          min_ua:Math.min(previous.min_ua,point.min_ua),max_ua:Math.max(previous.max_ua,point.max_ua)};
      }else out.push({...point});
    }
    if(out.length<=budget)return out;
    if(width>points.at(-1).end_sample+1){
      // Too many gaps: retain isolated blocks and the global extrema.
      let lo=0,hi=0;
      out.forEach((p,i)=>{if(p.min_ua<out[lo].min_ua)lo=i;if(p.max_ua>out[hi].max_ua)hi=i;});
      const selected=new Set([0,out.length-1,lo,hi]);
      for(let i=0;i<budget&&selected.size<budget;i++)selected.add(Math.round(i*(out.length-1)/(budget-1)));
      return [...selected].sort((a,b)=>a-b).map(i=>({...out[i],line_key:`${out[i].line_key}-isolated-${i}`}));
    }
  }
}

function mergeLiveFrame(previous,frame){
  const incoming=frame.series;
  const reset=frame.reset||!previous;
  const points=compactLiveSummaries([...(reset?[]:previous.summary_points||[]),...(incoming.summary_points||[])],incoming.max_points||1500);
  const spectral=incoming.spectrogram;
  let spectrogram=reset?null:previous?.spectrogram;
  if(spectral){
    const retained=reset||spectral.reset?[]:spectrogram?.frames||[];
    const cursor=retained.at(-1)?.end_sample??-1;
    const frames=[...retained,...spectral.frames.filter(f=>f.end_sample>cursor)];
    const latest=frames.at(-1)?.t_s??0;
    spectrogram={...spectral,frames:frames.filter(f=>f.t_s>=latest-spectral.history_s).slice(-Math.ceil(spectral.history_s/spectral.hop_s))};
  }
  return {...incoming,spectrogram,summary_points:points,point_count:points.length,
    events:reset?incoming.events:previous.events,
    state_markers:reset?incoming.state_markers:previous.state_markers};
}

async function renderLiveSpectrogram(series){
  if(!document.getElementById('spectrogramToggle').checked)return;
  const spectral=series?.spectrogram,frames=spectral?.frames||[];
  const hint=document.getElementById('spectrogramHint');
  hint.textContent=frames.length?`Letzte ${spectral.history_s} s · ${Math.round(spectral.window_s*1000)}-ms-Fenster · ${(1/spectral.window_s).toLocaleString('de-DE',{maximumFractionDigits:2})} Hz FFT-Abstand · Mittelwert entfernt · blaue Sleep- / gelbe Wake-Marker`:'Warte auf ein vollständiges 250-ms-Messfenster …';
  const x=[],columns=[];
  if(state.lastLiveSnapshot?.spectral_comparison)hint.textContent+=' · FFT Sleep: Türkis / FFT Wake: Pink (gestrichelt)';
  frames.forEach((f,i)=>{
    if(i&&f.t_s-frames[i-1].t_s>spectral.hop_s*1.01){
      x.push((f.t_s+frames[i-1].t_s)/2);columns.push(null);
    }
    x.push(f.t_s);columns.push(f.psd_db);
  });
  const frequencies=spectral?.frequencies_hz||[];
  const latest=frames.at(-1)?.t_s??0;
  const markers=(series?.state_markers||[]).filter(m=>m.t_s>=latest-120&&m.t_s<=latest);
  await Plotly.react(document.getElementById('spectrogramChart'),[{
    type:'heatmap',x,y:frequencies,z:frequencies.map((_,i)=>columns.map(c=>c?c[i]:null)),
    colorscale:'Viridis',zmin:-100,zmax:60,zsmooth:false,connectgaps:false,
    colorbar:{title:{text:'dB re<br>1 µA²/Hz'},thickness:14},
    hovertemplate:'t=%{x:.3f} s<br>f=%{y:.1f} Hz<br>%{z:.1f} dB re 1 µA²/Hz<extra></extra>'
  }],{
    paper_bgcolor:'transparent',plot_bgcolor:'transparent',font:{color:'#c8d0d8',size:11},
    margin:{l:82,r:100,t:12,b:60},
    xaxis:{title:{text:series?.phase==='startup'?'Zeit seit Einschalten [s]':'Zeit seit Sleep Start [s]'},range:[Math.max(0,latest-120),Math.max(.25,latest)],gridcolor:'#222a31'},
    yaxis:{title:{text:'Frequenz [Hz]'},type:'log',gridcolor:'#222a31'},
    shapes:sleepStartShapes(markers),showlegend:false
  },{...plotConfig,scrollZoom:false});
}
