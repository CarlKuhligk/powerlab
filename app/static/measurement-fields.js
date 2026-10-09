/* User-defined measurement context, shared by creation and metadata editing. */
const MeasurementFields=(()=>{
  const legacy=[['project','Projekt'],['device','Gerät'],['serial_number','Seriennummer'],['firmware','Firmware-Version'],['hardware_version','Hardware-Version']];
  let nextId=0;
  const container=formId=>document.getElementById(`${formId}CustomFields`);
  const rows=formId=>Array.from(container(formId).children);
  function fitValue(value){
    value.style.height='38px';
    value.style.height=`${Math.min(98,Math.max(38,value.scrollHeight+2))}px`;
  }
  function updateAddButton(formId){
    document.querySelector(`[data-add-measurement-field="${formId}"]`).disabled=rows(formId).length>=100;
  }
  function add(formId,field={label:'',value:''},focus=true){
    if(focus&&rows(formId).length>=100)return;
    const row=document.createElement('div');row.className='custom-field-row';
    row.setAttribute('role','group');row.setAttribute('aria-label','Eigenes Feld');
    const id=`customField${++nextId}`;
    const nameLabel=document.createElement('label');
    const nameText=document.createElement('span');nameText.className='custom-field-label-text';nameText.textContent='Feldname';nameLabel.append(nameText);
    const name=document.createElement('input');name.id=`${id}Label`;name.dataset.fieldLabel='';
    name.type='text';name.maxLength=160;name.required=true;name.value=field.label;
    name.placeholder='Feldname';
    nameLabel.htmlFor=name.id;nameLabel.append(name);
    const validateName=()=>name.setCustomValidity(name.value.trim()?'':'Bitte einen Feldnamen eingeben.');
    name.addEventListener('input',validateName);
    const valueLabel=document.createElement('label');
    const valueText=document.createElement('span');valueText.className='custom-field-label-text';valueText.textContent='Wert';valueLabel.append(valueText);
    const value=document.createElement('textarea');value.id=`${id}Value`;value.dataset.fieldValue='';
    value.rows=1;value.maxLength=4000;value.value=field.value;value.placeholder='Wert';
    value.addEventListener('input',()=>fitValue(value));
    valueLabel.htmlFor=value.id;valueLabel.append(value);
    const remove=document.createElement('button');remove.type='button';remove.className='custom-field-remove';
    const removeIcon=document.createElement('span');removeIcon.textContent='×';removeIcon.setAttribute('aria-hidden','true');remove.append(removeIcon);
    remove.setAttribute('aria-label','Eigenes Feld entfernen');remove.title='Eigenes Feld entfernen';
    remove.addEventListener('click',()=>{
      const index=rows(formId).indexOf(row);row.remove();updateAddButton(formId);
      const remaining=rows(formId);(remaining[Math.min(index,remaining.length-1)]?.querySelector('[data-field-label]')||document.querySelector(`[data-add-measurement-field="${formId}"]`)).focus();
    });
    row.append(nameLabel,valueLabel,remove);container(formId).append(row);
    fitValue(value);requestAnimationFrame(()=>{if(value.isConnected)fitValue(value)});
    updateAddButton(formId);if(focus)name.focus();
  }
  function restore(formId,source={}){
    container(formId).replaceChildren();
    for(const [key,label] of legacy)if(source[key])add(formId,{label,value:source[key]},false);
    for(const field of source.custom_fields||[])add(formId,field,false);
    updateAddButton(formId);
  }
  function payload(formId){
    const custom_fields=rows(formId).map(row=>{
      const input=row.querySelector('[data-field-label]'),label=input.value.trim();
      return {label,value:row.querySelector('[data-field-value]').value};
    });
    // Convert legacy context to editable custom fields when saving through the UI.
    return {...Object.fromEntries(legacy.map(([key])=>[key,''])),custom_fields};
  }
  document.querySelectorAll('[data-add-measurement-field]').forEach(button=>button.addEventListener('click',()=>add(button.dataset.addMeasurementField)));
  window.addEventListener('resize',()=>document.querySelectorAll('.custom-fields [data-field-value]').forEach(fitValue));
  return {restore,payload};
})();
