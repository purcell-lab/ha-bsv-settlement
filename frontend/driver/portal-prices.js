// Presentation only. Public prices never confer spending or session authority.
export function priceCard(prices, direction, now=Date.now()){
  const item=prices?.[direction],checked=Date.parse(prices?.checked_at);
  const start=Date.parse(item?.start),end=Date.parse(item?.end);
  const raw=item?.aud_per_kwh;
  const valid=item?.available===true&&item?.estimate===false&&
    typeof raw==="string"&&raw.trim()!==""&&Number.isFinite(Number(raw))&&Math.abs(Number(raw))<=1000000&&
    Number.isFinite(checked)&&now-checked<=90000&&checked<=now+5000&&
    Number.isFinite(start)&&Number.isFinite(end)&&start<=now&&now<end&&end>start;
  return valid?{value:`${Number(raw).toFixed(4)} $/kWh`,available:true}:
    {value:"Unavailable",available:false};
}
