/* Complete-cycle energy card in measurement history. */
const CycleEnergyUI=(()=>{
  const summaries={};
  const units={seconds:1,minutes:60,hours:3600,days:86400,weeks:604800};
  const energy=value=>value==null?'—':Math.abs(value)>=1e6?`${(value/1e6).toFixed(4)} Wh`:Math.abs(value)>=1000?`${(value/1000).toFixed(4)} mWh`:Math.abs(value)<.01?`${(value*1000).toFixed(3)} nWh`:`${value.toFixed(4)} µWh`;
  const power=value=>value==null?'—':Math.abs(value)>=1000?`${(value/1000).toFixed(4)} mW`:`${value.toFixed(4)} µW`;
  function custom(prefix){
    const input=document.getElementById(`${prefix}CycleCustomValue`);
    const seconds=Number(input.value)*units[document.getElementById(`${prefix}CycleCustomUnit`).value];
    const data=summaries[prefix];
    const valid=input.value.trim()&&Number.isFinite(seconds)&&seconds>0;
    input.setCustomValidity(valid?'':'Bitte eine positive Zeitspanne eingeben.');
    for(const kind of ['wake','sleep','combined']){
      const value=valid&&data?.[`average_${kind}_power_uw`]!=null?data[`average_${kind}_power_uw`]*seconds/3600:null;
      document.getElementById(`${prefix}CycleCustom${kind}`).textContent=energy(value);
    }
  }
  function render(data,prefix){
    summaries[prefix]=data;
    const count=data?.cycle_count||0;
    document.getElementById(`${prefix}CycleCount`).textContent=`${count} vollständige Zyklen`;
    document.getElementById(`${prefix}CycleNote`).textContent=count?
      `Nur bestätigte Sleep → Wake → Sleep-Zyklen. ${data.excluded_wake_count} weitere Wake-Events ausgeschlossen. Hochrechnung bei ${data.voltage_v} V und unverändertem Zyklusverhalten.`:
      'Noch kein vollständig bestätigter Sleep → Wake → Sleep-Zyklus. Offene Phasen gehen nicht in Mittelwerte und Hochrechnung ein.';
    if(data?.interpolated_samples)document.getElementById(`${prefix}CycleNote`).textContent+=
      ` Kleine Datenlücken in ${data.interpolated_cycle_count} Zyklen mit Abschnittsmittelwerten ergänzt: ${(data.interpolated_duration_s*1000).toFixed(2)} ms. Werte enthalten geschätzte Anteile.`;
    if(data?.excluded_cycles_with_gaps)document.getElementById(`${prefix}CycleNote`).textContent+=
      ` ${data.excluded_cycles_with_gaps} Zyklen wegen Datenlücken ausgeschlossen.`;
    for(const [id,key,format] of [['WakeEnergy','average_wake_energy_uwh',energy],['SleepEnergy','average_sleep_energy_uwh',energy],['SleepCurrent','average_sleep_current_ua',fmtCurrent],['Power','average_combined_power_uw',power]]){
      document.getElementById(`${prefix}Cycle${id}`).textContent=format(data?.[key]??null);
    }
    for(const period of ['day','week','month','year'])for(const kind of ['wake','sleep','combined']){
      document.getElementById(`${prefix}Cycle${period}${kind}`).textContent=energy(data?.projections?.[period]?.[`${kind}_energy_uwh`]??null);
    }
    custom(prefix);
  }
  function install(){
    for(const prefix of ['detail']){
      const card=document.createElement('div');card.className='card cycle-energy-card detail-energy';
      card.innerHTML=`<div class="card-title-row"><h3>Energie aus bestätigten Zyklen</h3><span id="${prefix}CycleCount"></span></div><p class="form-hint" id="${prefix}CycleNote"></p>
        <div class="kpi-grid detail-kpis"><div class="kpi"><span>Ø Wake-Energie / Zyklus</span><strong id="${prefix}CycleWakeEnergy"></strong></div><div class="kpi"><span>Ø Sleep-Strom · zeitgewichtet</span><strong id="${prefix}CycleSleepCurrent"></strong></div><div class="kpi"><span>Ø Sleep-Energie / Zyklus</span><strong id="${prefix}CycleSleepEnergy"></strong></div><div class="kpi"><span>Ø gemeinsame Leistung</span><strong id="${prefix}CyclePower"></strong></div></div>
        <div class="event-scroll"><table><thead><tr><th>Hochrechnung</th><th>Wake-Energie</th><th>Sleep-Energie</th><th>Gesamtenergie</th></tr></thead><tbody>
        ${[['day','Tag'],['week','Woche'],['month','Monat · 30 Tage'],['year','Jahr · 365 Tage']].map(([key,label])=>`<tr><td>${label}</td>${['wake','sleep','combined'].map(kind=>`<td id="${prefix}Cycle${key}${kind}"></td>`).join('')}</tr>`).join('')}
        <tr><td><div class="cycle-custom"><input id="${prefix}CycleCustomValue" type="number" min="0.000001" step="any" value="1" aria-label="Eigene Zeitspanne"><select id="${prefix}CycleCustomUnit" aria-label="Einheit der eigenen Zeitspanne"><option value="seconds">Sekunden</option><option value="minutes">Minuten</option><option value="hours">Stunden</option><option value="days" selected>Tage</option><option value="weeks">Wochen</option></select></div></td>${['wake','sleep','combined'].map(kind=>`<td id="${prefix}CycleCustom${kind}"></td>`).join('')}</tr>
        </tbody></table></div>`;
      document.querySelector('.history-metadata-card').before(card);
      document.getElementById(`${prefix}CycleCustomValue`).addEventListener('input',()=>custom(prefix));
      document.getElementById(`${prefix}CycleCustomUnit`).addEventListener('change',()=>custom(prefix));
      render(null,prefix);
    }
  }
  return {install,render};
})();
