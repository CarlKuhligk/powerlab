/* Wake statistics from fully confirmed Sleep → Wake → Sleep cycles. */
const WakeAnalysisUI=(()=>{
  const variabilityMetrics=[['duration_s','Wake-Dauer','s'],['current_ua','Mittlerer Strom je Wake','µA'],['energy_uwh','Wake-Energie','µWh']];
  const statistic=(value,unit)=>value==null?'—':`${Number(value.toPrecision(5)).toLocaleString('de-DE',{maximumSignificantDigits:5})} ${unit}`;
  function render(data,prefix){
    const count=data?.cycle_count||0;
    document.getElementById(`${prefix}WakeAnalysisCount`).textContent=`${count} gültige Wakes`;
    document.getElementById(`${prefix}WakeAnalysisNote`).textContent=count?
      `Vollständig bestätigte Sleep → Wake → Sleep-Zyklen. ${data.excluded_wake_count??0} weitere Wake-Ereignisse ausgeschlossen.`:
      'Noch kein vollständig bestätigter Sleep → Wake → Sleep-Zyklus. Offene Phasen gehen nicht in die Wake-Auswertung ein.';
    if(data?.interpolated_samples)document.getElementById(`${prefix}WakeAnalysisNote`).textContent+=
      ` Kleine Datenlücken in ${data.interpolated_cycle_count} Zyklen mit Abschnittsmittelwerten ergänzt: ${(data.interpolated_duration_s*1000).toFixed(2)} ms. Werte enthalten geschätzte Anteile.`;
    if(data?.excluded_cycles_with_gaps)document.getElementById(`${prefix}WakeAnalysisNote`).textContent+=
      ` ${data.excluded_cycles_with_gaps} Zyklen wegen Datenlücken ausgeschlossen.`;
    for(const [id,key,format] of [['Duration','average_wake_duration_s',fmtDuration],['Current','average_wake_current_ua',fmtCurrent],['Energy','average_wake_energy_uwh',fmtEnergy]]){
      document.getElementById(`${prefix}WakeAnalysis${id}`).textContent=format(data?.[key]??null);
    }
    const variability=data?.wake_variability;
    document.getElementById(`${prefix}WakeAnalysisVariabilityNote`).textContent=
      `Streuung zwischen ${variability?.count??0} gültigen Wakes. Jeder Wake zählt gleich. Stichprobenvarianz (n − 1); mindestens zwei Wakes erforderlich. Relative Streuung = Standardabweichung / Betrag des Mittelwerts.`;
    for(const [key,,unit] of variabilityMetrics){
      const metric=variability?.[key];
      for(const [field,displayUnit] of [['mean',unit],['stddev',unit],['variance',`${unit}²`],['cv_pct','%']]){
        document.getElementById(`${prefix}WakeAnalysisVariability${key}${field}`).textContent=statistic(metric?.[field]??null,displayUnit);
      }
    }
  }
  function install(){
    const prefix='detail';
    const card=document.createElement('section');
    card.className='card wake-analysis-card detail-wake';
    card.setAttribute('aria-labelledby','detailWakeAnalysisTitle');
    card.innerHTML=`<div class="card-title-row"><h3 id="detailWakeAnalysisTitle">Wake-Auswertung</h3><span id="${prefix}WakeAnalysisCount"></span></div><p class="form-hint" id="${prefix}WakeAnalysisNote"></p>
      <div class="kpi-grid wake-analysis-kpis"><div class="kpi"><span>Ø Wake-Dauer</span><strong id="${prefix}WakeAnalysisDuration"></strong></div><div class="kpi"><span>Ø Wake-Strom · zeitgewichtet</span><strong id="${prefix}WakeAnalysisCurrent"></strong></div><div class="kpi"><span>Ø Wake-Energie</span><strong id="${prefix}WakeAnalysisEnergy"></strong></div></div>
      <h4>Streuung der gültigen Wake-Phasen</h4><p class="form-hint" id="${prefix}WakeAnalysisVariabilityNote"></p>
      <div class="event-scroll"><table><thead><tr><th scope="col">Wake-Kennwert</th><th scope="col">Mittelwert je Wake</th><th scope="col">Standardabweichung</th><th scope="col">Varianz</th><th scope="col">Relative Streuung</th></tr></thead><tbody>
      ${variabilityMetrics.map(([key,label])=>`<tr><td>${label}</td>${['mean','stddev','variance','cv_pct'].map(field=>`<td id="${prefix}WakeAnalysisVariability${key}${field}"></td>`).join('')}</tr>`).join('')}
      </tbody></table></div>`;
    document.querySelector('.history-metadata-card').before(card);
    render(null,prefix);
  }
  return {install,render};
})();
