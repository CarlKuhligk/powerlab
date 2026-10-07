/* Current controls display nA/µA/mA; application and API values remain µA. */
const CurrentInputs=(()=>{
  const factors={nA:.001,'µA':1,mA:1000};
  const ids=['sleepThresholdUa','wakeThresholdUa'];
  function parse(text,unit='µA'){
    const match=String(text).match(/^\s*([+-]?(?:\d+(?:[.,]\d*)?|[.,]\d+)(?:e[+-]?\d+)?)\s*(na|ua|µa|μa|ma)?\s*$/i);
    if(!match)return null;
    const value=Number(match[1].replace(',','.')),suffix=match[2]?.toLowerCase();
    const selected=suffix?(suffix==='na'?'nA':suffix==='ma'?'mA':'µA'):unit;
    return Number.isFinite(value)&&Number.isFinite(value*factors[selected])?{value,unit:selected,ua:value*factors[selected],explicit:!!suffix}:null;
  }
  const inputs=()=>ids.map(id=>document.getElementById(id)).filter(input=>input?.dataset.currentUnit);
  const select=input=>document.getElementById(`${input.id}Unit`);
  function ua(input){return parse(input.value,input.dataset.currentUnit||'µA')?.ua??NaN}
  function validate(input){
    if(input.disabled){input.setCustomValidity('');return}
    const value=parse(input.value,input.dataset.currentUnit);
    let error='';
    if(input.value.trim()&&!value)error='Strom als Zahl mit nA, µA oder mA eingeben.';
    if(value&&ids.includes(input.id)&&value.ua<=0)error='Der Strom muss größer als 0 sein.';
    input.setCustomValidity(error);
  }
  function normalize(input,commit=false){
    const value=parse(input.value,input.dataset.currentUnit);
    if(value&&(value.explicit||commit)){
      select(input).value=value.unit;input.dataset.currentUnit=value.unit;
      input.value=String(value.value);
    }
    validate(input);
  }
  function restore(){
    // Call when field values have just been populated from µA settings.
    for(const input of inputs()){
      const value=Number(input.value),unit=input.value.trim()&&Math.abs(value)>=1000?'mA':input.value.trim()&&value!==0&&Math.abs(value)<1?'nA':'µA';
      input.dataset.currentUnit=unit;select(input).value=unit;
      if(input.value.trim())input.value=String(Number((value/factors[unit]).toPrecision(12)));
      validate(input);
    }
  }
  function canonicalize(){
    for(const input of inputs()){
      if(input.value.trim())input.value=String(ua(input));
      input.dataset.currentUnit='µA';select(input).value='µA';
    }
  }
  function sync(){for(const input of inputs()){select(input).disabled=input.disabled;normalize(input)}}
  function install(){
    const labels={sleepThresholdUa:'Sleep unter',wakeThresholdUa:'Wake über'};
    for(const id of ids){
      const input=document.getElementById(id);if(!input)continue;
      input.type='text';input.inputMode='decimal';input.dataset.currentUnit='µA';
      const wrapper=document.createElement('div');wrapper.className='current-input';input.before(wrapper);wrapper.append(input);
      const dropdown=document.createElement('select');dropdown.id=`${id}Unit`;dropdown.setAttribute('aria-label',`Einheit für ${labels[id]}`);
      for(const unit of Object.keys(factors)){const option=new Option(unit,unit,unit==='µA',unit==='µA');dropdown.add(option)}
      wrapper.append(dropdown);
      const label=document.querySelector(`label[for="${id}"]`);if(label)label.textContent=labels[id];
      input.addEventListener('input',()=>normalize(input));
      input.addEventListener('blur',()=>normalize(input,true));
      dropdown.addEventListener('change',()=>{
        input.dataset.currentUnit=dropdown.value;validate(input);
        input.dispatchEvent(new Event('input',{bubbles:true}));
      });
    }
    restore();sync();
  }
  return {parse,ua,restore,canonicalize,sync,install};
})();
