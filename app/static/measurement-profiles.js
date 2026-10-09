/* Named, persistent snapshots of the measurement form; API units stay canonical. */
const MeasurementProfiles=(()=>{
  let profiles=[],saveTask=null,pendingProfile=null;
  const el=id=>document.getElementById(id);
  const status=message=>{el('measurementProfileStatus').textContent=message};
  function reset(){
    if(el('measurementProfileDialog').open)el('measurementProfileDialog').close();
    pendingProfile=null;
    el('measurementProfileName').value='';
    el('measurementProfileName').setCustomValidity('');
    el('measurementProfileSaveError').hidden=true;
    el('measurementProfileSaveError').textContent='';
  }
  function setSaving(saving){
    el('measurementProfileName').disabled=saving;
    el('measurementProfileConfirm').disabled=saving;
    el('measurementProfileConfirm').textContent=saving?'Speichert …':'Profil speichern';
    document.querySelectorAll('[data-close-profile-dialog]').forEach(button=>button.disabled=saving);
    el('measurementProfileSave').disabled=saving||el('measurementForm').dataset.submitting==='1';
    el('measurementSubmit').disabled=saving||el('measurementForm').dataset.submitting==='1';
    el('measurementProfileSelect').disabled=saving;
  }
  function openSaveDialog(){
    const form=el('measurementForm');
    if(saveTask||form.dataset.submitting==='1'||!el('measurementDialog').open)return;
    try{
      const payload=measurementPayload();
      reset();pendingProfile={payload,token:form.dataset.dialogToken};
      el('measurementProfileDialog').showModal();
      el('measurementProfileName').focus();
    }catch(error){toast(error.message,true)}
  }
  function closeSaveDialog(){
    if(saveTask)return;
    el('measurementProfileDialog').close();pendingProfile=null;
  }
  function render(selected=''){
    const select=el('measurementProfileSelect');
    select.replaceChildren(new Option('Ohne Messprofil',''));
    for(const profile of profiles)select.add(new Option(profile.name,profile.id));
    select.value=selected;
  }
  async function load(){
    const token=el('measurementForm').dataset.dialogToken;
    el('measurementProfileSelect').disabled=true;
    status('Messprofile werden geladen …');
    try{
      const loaded=await api('/api/measurement-profiles');
      if(token!==el('measurementForm').dataset.dialogToken||!el('measurementDialog').open)return;
      profiles=loaded;render();
      status(profiles.length?'Ein Profil übernimmt die gespeicherten Werte in das Formular. Du kannst sie anschließend anpassen.':'Noch keine Messprofile. Über „Einstellungen speichern“ kannst du ein Profil anlegen.');
    }catch(error){
      if(token===el('measurementForm').dataset.dialogToken){profiles=[];render();status(`Messprofile konnten nicht geladen werden: ${error.message}`)}
    }finally{
      if(token===el('measurementForm').dataset.dialogToken)el('measurementProfileSelect').disabled=!!saveTask;
    }
  }
  async function apply(){
    const select=el('measurementProfileSelect'),profile=profiles.find(p=>p.id===select.value);
    const form=el('measurementForm'),token=form.dataset.dialogToken;
    select.disabled=true;
    try{
      await MeasurementPreview.reset();
      if(!el('measurementDialog').open||token!==form.dataset.dialogToken)return;
      const port=form.elements.port.value;
      setDialogDefaults();form.elements.port.value=port;
      if(profile){
        CurrentInputs.canonicalize();TimeInputs.canonicalize();
        for(const [name,value] of Object.entries(profile.settings)){
          const input=form.elements[name];
          if(input&&value!=null&&!['scheduled_start_at','scheduled_end_at','port'].includes(name))input.value=value;
        }
        if(profile.settings.scheduled_start_at)el('scheduledStartAt').value=localInput(profile.settings.scheduled_start_at);
        if(profile.settings.scheduled_end_at)el('scheduledEndAt').value=localInput(profile.settings.scheduled_end_at);
        if(profile.settings.duration_s!=null){form.elements.duration_value.value=profile.settings.duration_s;form.elements.duration_unit.value='1'}
        if(profile.settings.port){
          const device=(state.devices.ppk2||[]).find(p=>p.port===profile.settings.port);
          form.elements.port.value='';
          state.preferredDevice={port:profile.settings.port,id:device?.device_id||device?.serial||null};
        }
        CurrentInputs.restore();TimeInputs.restore();
        MeasurementFields.restore('measurementForm',profile.settings);
        select.value=profile.id;
      }
      renderDevices(state.devices);updateScheduleFields();updateSpectralSettings();updateEventSettings();
      status(profile?`Messprofil „${profile.name}“ übernommen. Alle Werte können angepasst werden.`:'Ohne Messprofil: Standardparameter und leere Messungsangaben.');
    }catch(error){toast(error.message,true)}finally{if(token===form.dataset.dialogToken)select.disabled=false}
  }
  async function save(name,payload,token){
    if(saveTask)return saveTask;
    saveTask=(async()=>{
      // Use a readable validation message without changing the shared API helper.
      const response=await fetch('/api/measurement-profiles',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({name,settings:payload})});
      const result=await response.json();
      if(!response.ok){
        const detail=Array.isArray(result.detail)?result.detail.map(error=>`${error.loc.slice(1).join('.')}: ${error.msg}`).join('; '):result.detail;
        throw new Error(detail||'Messprofil konnte nicht gespeichert werden.');
      }
      if(token===el('measurementForm').dataset.dialogToken&&el('measurementDialog').open){
        profiles.push(result);profiles.sort((a,b)=>a.name.localeCompare(b.name,'de'));
        render(result.id);status(`Messprofil „${result.name}“ gespeichert.`);
      }
      return result;
    })();
    setSaving(true);
    try{return await saveTask}finally{
      saveTask=null;setSaving(false);
    }
  }
  el('measurementProfileSelect').addEventListener('change',apply);
  el('measurementProfileSave').addEventListener('click',openSaveDialog);
  document.querySelectorAll('[data-close-profile-dialog]').forEach(button=>button.addEventListener('click',closeSaveDialog));
  el('measurementProfileDialog').addEventListener('cancel',event=>{
    if(saveTask)event.preventDefault();else pendingProfile=null;
  });
  el('measurementDialog').addEventListener('close',reset);
  el('measurementProfileName').addEventListener('input',()=>{
    el('measurementProfileName').setCustomValidity('');el('measurementProfileSaveError').hidden=true;
  });
  el('measurementProfileForm').addEventListener('submit',async event=>{
    event.preventDefault();
    if(saveTask||!pendingProfile)return;
    const name=el('measurementProfileName').value.trim(),pending=pendingProfile;
    el('measurementProfileName').setCustomValidity(name?'':'Bitte einen Profilnamen eingeben.');
    if(!event.currentTarget.reportValidity())return;
    if(pending.token!==el('measurementForm').dataset.dialogToken||!el('measurementDialog').open){closeSaveDialog();return;}
    try{
      await save(name,pending.payload,pending.token);
      if(pending.token===el('measurementForm').dataset.dialogToken){closeSaveDialog();toast('Messprofil gespeichert')}
    }catch(error){
      if(el('measurementProfileDialog').open&&pending.token===el('measurementForm').dataset.dialogToken){
        el('measurementProfileSaveError').textContent=error.message;el('measurementProfileSaveError').hidden=false;
      }
    }
  });
  return {load,reset};
})();
