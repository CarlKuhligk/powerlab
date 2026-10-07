/* Display units are independent of the seconds/milliseconds used by the API. */
const TimeInputs=(()=>{
  const factors={ns:1e-9,'µs':1e-6,ms:1e-3,s:1};
  const fields={sleepMinS:{base:'s',max:60,positive:true,label:'Sleep-Mindestdauer'},wakeMinMs:{base:'ms',max:60000,positive:true,label:'Wake-Mindestdauer'},preTriggerMs:{base:'ms',max:5000,label:'Vorlauf'},postTriggerMs:{base:'ms',max:5000,label:'Nachlauf'}};
  const inputs=()=>Object.keys(fields).map(id=>document.getElementById(id)).filter(input=>input?.dataset.timeUnit);
  const select=input=>document.getElementById(`${input.id}Unit`);
  function parse(text,unit='ms'){
    const match=String(text).match(/^\s*([+-]?(?:\d+(?:[.,]\d*)?|[.,]\d+)(?:e[+-]?\d+)?)\s*(ns|us|µs|μs|ms|s)?\s*$/i);
    if(!match)return null;
    const value=Number(match[1].replace(',','.')),suffix=match[2]?.toLowerCase();
    const selected=suffix?(['us','µs','μs'].includes(suffix)?'µs':suffix):unit;
    const seconds=value*factors[selected];
    return Number.isFinite(value)&&Number.isFinite(seconds)?{value,unit:selected,seconds,explicit:!!suffix}:null;
  }
  function value(input){
    const parsed=parse(input.value,input.dataset.timeUnit||fields[input.id].base);
    return parsed?Number((parsed.seconds/factors[fields[input.id].base]).toPrecision(15)):NaN;
  }
  function format(input){
    const parsed=parse(input.value,input.dataset.timeUnit);
    return parsed?`${parsed.value.toLocaleString('de-DE',{maximumSignificantDigits:15})} ${parsed.unit}`:'—';
  }
  function validate(input){
    const config=fields[input.id],parsed=parse(input.value,input.dataset.timeUnit),amount=value(input);
    let error='';
    if(!input.disabled&&input.value.trim()){
      if(!parsed)error='Zeit als Zahl mit ns, µs, ms oder s eingeben.';
      else if(config.positive?amount<=0:amount<0)error=config.positive?'Die Dauer muss größer als 0 sein.':'Die Dauer darf nicht negativ sein.';
      else if(amount>config.max)error=`Maximal ${config.max*factors[config.base]} s erlaubt.`;
    }
    input.setCustomValidity(error);
  }
  function normalize(input,commit=false){
    const parsed=parse(input.value,input.dataset.timeUnit);
    if(parsed&&(parsed.explicit||commit)){
      input.dataset.timeUnit=parsed.unit;select(input).value=parsed.unit;input.value=String(parsed.value);
    }
    validate(input);
  }
  function restore(){
    for(const input of inputs()){
      input.dataset.timeUnit=fields[input.id].base;select(input).value=input.dataset.timeUnit;validate(input);
    }
  }
  function canonicalize(){
    for(const input of inputs()){
      if(input.value.trim())input.value=String(value(input));
      input.dataset.timeUnit=fields[input.id].base;select(input).value=input.dataset.timeUnit;
    }
  }
  function sync(){for(const input of inputs()){select(input).disabled=input.disabled;normalize(input)}}
  function install(){
    for(const [id,config] of Object.entries(fields)){
      const input=document.getElementById(id);if(!input)continue;
      input.type='text';input.inputMode='decimal';input.dataset.timeUnit=config.base;
      const wrapper=document.createElement('div');wrapper.className='current-input time-input';input.before(wrapper);wrapper.append(input);
      const dropdown=document.createElement('select');dropdown.id=`${id}Unit`;dropdown.setAttribute('aria-label',`Einheit für ${config.label}`);
      for(const unit of Object.keys(factors))dropdown.add(new Option(unit,unit,unit===config.base,unit===config.base));
      wrapper.append(dropdown);
      const label=document.querySelector(`label[for="${id}"]`);if(label)label.textContent=config.label.includes('Mindestdauer')?'Mindestdauer':config.label;
      input.addEventListener('input',()=>normalize(input));
      input.addEventListener('blur',()=>normalize(input,true));
      dropdown.addEventListener('change',()=>{
        const parsed=parse(input.value,input.dataset.timeUnit);
        if(parsed)input.value=String(Number((parsed.seconds/factors[dropdown.value]).toPrecision(15)));
        input.dataset.timeUnit=dropdown.value;
        input.dispatchEvent(new Event('input',{bubbles:true}));
      });
    }
    restore();sync();
  }
  return {parse,value,format,restore,canonicalize,sync,install};
})();
