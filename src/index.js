const JSON_HEADERS={"content-type":"application/json; charset=UTF-8","cache-control":"no-store"};
const GEOS=["US","IR"],HN_TOP=10;
const RISK_RULES=[
  [/\b(porn|xxx|sex|onlyfans)\b/i,"adult"],
  [/\b(gambling|casino|betting)\b/i,"gambling"],
  [/\b(war|attack|terror|explosion|murder|killed|death)\b/i,"violence"],
  [/\b(election|president|politics|political|parliament|government)\b/i,"politics"],
  [/\b(earthquake|flood|hurricane|wildfire)\b/i,"disaster"]
];
function decodeEntities(s){return String(s||"").replace(/&amp;/g,"&").replace(/&lt;/g,"<").replace(/&gt;/g,">").replace(/&quot;/g,'"').replace(/&#39;/g,"'");}
function xmlTag(block,tag){const re=new RegExp("<(?:[\\w-]+:)?"+tag+"[^>]*>([\\s\\S]*?)<\\/(?:[\\w-]+:)?"+tag+">","i");const m=block.match(re);return m?decodeEntities(m[1].trim()):"";}
function parseTraffic(s){const m=String(s||"").replace(/,/g,"").match(/([0-9.]+)([KkMmBb])?/);if(!m)return 0;const n=Number(m[1]);return m[2]?(["K","k"].includes(m[2])?n*1e3:["M","m"].includes(m[2])?n*1e6:n*1e9):n;}
function now(){return new Date().toISOString();}
function trendKey(s){return String(s||"").toLowerCase().replace(/https?:\/\/\S+/g,"").replace(/[^\p{L}\p{N}]+/gu," ").trim().split(/\s+/).slice(0,10).join(" ");}
function riskFlags(title){return RISK_RULES.filter(([re])=>re.test(title)).map(([,name])=>name).join(",");}
function score(x){
  const fresh=Math.max(0,25-Math.max(0,(Date.now()-new Date(x.published_at||now()).getTime())/3600000));
  const mag=x.source.startsWith("google_trends")?Math.min(50,Math.log10(Math.max(1,x.signal_value))*10):Math.min(45,x.signal_value/5);
  const vel=Math.max(0,Math.min(30,x.velocity_pct>0?Math.log10(1+x.velocity_pct)*15:0));
  const risk=x.risk_flags?30:0;
  return Math.round(Math.max(0,Math.min(100,mag+fresh+vel-risk))*100)/100;
}
async function getText(url){const r=await fetch(url,{headers:{"user-agent":"trend-radar-mvp/1.0"}});if(!r.ok)throw new Error("HTTP "+r.status+" from "+url);return r.text();}
async function getJson(url){const r=await fetch(url,{headers:{"user-agent":"trend-radar-mvp/1.0"}});if(!r.ok)throw new Error("HTTP "+r.status+" from "+url);return r.json();}
async function readGoogleTrends(geo){const xml=await getText("https://trends.google.com/trending/rss?geo="+geo),out=[];for(const block of xml.split(/<item>/i).slice(1,41)){const b=block.split(/<\/item>/i)[0],title=xmlTag(b,"title");if(!title)continue;const link=xmlTag(b,"link"),pub=xmlTag(b,"pubDate"),traffic=xmlTag(b,"approx_traffic");out.push({source:"google_trends_"+geo.toLowerCase(),external_id:title.toLowerCase(),trend_key:trendKey(title),title,url:link||null,published_at:pub?new Date(pub).toISOString():now(),signal_value:parseTraffic(traffic),category:geo==="IR"?"iran":"web",risk_flags:riskFlags(title)});}return out;}
async function readHackerNews(){const ids=(await getJson("https://hacker-news.firebaseio.com/v0/beststories.json")).slice(0,HN_TOP),out=[];for(const id of ids){try{const item=await getJson("https://hacker-news.firebaseio.com/v0/item/"+id+".json");if(item&&item.type==="story"&&item.title)out.push({source:"hacker_news",external_id:String(id),trend_key:trendKey(item.title),title:item.title,url:item.url||("https://news.ycombinator.com/item?id="+id),published_at:new Date(Number(item.time||0)*1000).toISOString(),signal_value:Number(item.score||0),category:"technology",risk_flags:riskFlags(item.title)});}catch(_){}}return out;}
async function saveSignals(env,signals){
  const t=now(),groups=new Map();
  for(const x of signals){if(!groups.has(x.source))groups.set(x.source,[]);groups.get(x.source).push(x);}
  const previous=new Map();
  for(const [source,items] of groups){
    const qs=items.map(()=>"?").join(",");
    const params=[source,...items.map(x=>x.external_id)];
    const r=await env.DB.prepare("SELECT source,external_id,signal_value FROM signals WHERE source=? AND external_id IN ("+qs+")").bind(...params).all();
    for(const row of (r.results||[]))previous.set(source+"::"+row.external_id,Number(row.signal_value||0));
  }
  const stmts=[];
  for(const x of signals){
    const prev=previous.get(x.source+"::"+x.external_id)||0;
    const velocity=prev>0?((x.signal_value-prev)/Math.abs(prev))*100:0;
    const safe={source:String(x.source??"unknown"),external_id:String(x.external_id??x.title??"unknown"),trend_key:String(x.trend_key??x.title??""),title:String(x.title??"untitled"),url:x.url??null,published_at:x.published_at??t,signal_value:Number(x.signal_value??0),category:x.category??null,risk_flags:x.risk_flags??null,previous_value:prev,velocity_pct:velocity};
    const s=score(safe);
    stmts.push(env.DB.prepare("INSERT INTO signals(source,external_id,trend_key,title,url,published_at,first_seen_at,last_seen_at,signal_value,category,risk_flags,previous_value,velocity_pct,score) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT(source,external_id) DO UPDATE SET trend_key=excluded.trend_key,title=excluded.title,url=excluded.url,published_at=excluded.published_at,last_seen_at=excluded.last_seen_at,previous_value=excluded.previous_value,signal_value=excluded.signal_value,category=excluded.category,risk_flags=excluded.risk_flags,velocity_pct=excluded.velocity_pct,score=excluded.score").bind(safe.source,safe.external_id,safe.trend_key,safe.title,safe.url,safe.published_at,t,t,safe.signal_value,safe.category,safe.risk_flags,safe.previous_value,safe.velocity_pct,s));
    stmts.push(env.DB.prepare("INSERT INTO observations(source,external_id,trend_key,observed_at,signal_value,score,risk_flags) VALUES(?,?,?,?,?,?,?)").bind(x.source,x.external_id,x.trend_key,t,x.signal_value,s,x.risk_flags||null));
  }
  for(let i=0;i<stmts.length;i+=10)await env.DB.batch(stmts.slice(i,i+10));
}
async function runOnce(env){
  const started=now(),run=await env.DB.prepare("INSERT INTO runs(started_at,status,source_count,signal_count) VALUES(?,?,?,?)").bind(started,"started",3,0).run();
  const runId=run.meta?.last_row_id;
  const tasks=[...GEOS.map(readGoogleTrends),readHackerNews],rr=await Promise.allSettled(tasks);
  const signals=rr.flatMap(r=>r.status==="fulfilled"?r.value:[]),errors=rr.filter(r=>r.status==="rejected").map(r=>String(r.reason));
  try { await saveSignals(env,signals); }
  catch(e) {
    const msg=String(e);
    await env.DB.prepare("UPDATE runs SET finished_at=?,status=?,signal_count=?,error=? WHERE id=?").bind(now(),"failed",signals.length,msg,runId).run();
    throw e;
  }
  await env.DB.prepare("UPDATE runs SET finished_at=?,status=?,signal_count=?,error=? WHERE id=?").bind(now(),errors.length?"partial":"ok",signals.length,errors.join(" | ")||null,runId).run();
  return {ok:errors.length===0,signals:signals.length,errors};
}
export default {async fetch(request,env){
  const u=new URL(request.url);
  if(request.method==="GET"&&u.pathname==="/")return new Response(JSON.stringify({service:"trend-radar",mode:"signal-validation",publishing_enabled:false,ai_enabled:false}),{headers:JSON_HEADERS});
  if(request.method==="GET"&&u.pathname==="/health"){let db="ok";try{await env.DB.prepare("SELECT 1").first();}catch(_){db="error";}return new Response(JSON.stringify({ok:db==="ok",db}),{headers:JSON_HEADERS});}
  if(request.method==="GET"&&u.pathname==="/status"){try{const a=await env.DB.prepare("SELECT COUNT(*) n FROM signals").first(),b=await env.DB.prepare("SELECT COUNT(*) n FROM runs").first(),c=await env.DB.prepare("SELECT source,title,score,velocity_pct,risk_flags,last_seen_at FROM signals ORDER BY score DESC,last_seen_at DESC LIMIT 20").all();return new Response(JSON.stringify({ok:true,signals:a?.n||0,runs:b?.n||0,top:c?.results||[]}),{headers:JSON_HEADERS});}catch(e){return new Response(JSON.stringify({ok:false,error:String(e)}),{status:500,headers:JSON_HEADERS});}}
  if(request.method==="GET"&&u.pathname==="/metrics"){try{const r=await env.DB.prepare("SELECT (SELECT COUNT(*) FROM observations) observations,(SELECT COUNT(DISTINCT source||':'||external_id) FROM signals) entities,COALESCE(AVG(CASE WHEN velocity_pct>0 THEN velocity_pct END),0) avg_positive_velocity,(SELECT COUNT(*) FROM signals WHERE risk_flags IS NOT NULL) risk_marked FROM signals").first();return new Response(JSON.stringify(r||{}),{headers:JSON_HEADERS});}catch(e){return new Response(JSON.stringify({ok:false,error:String(e)}),{status:500,headers:JSON_HEADERS});}}
  if(request.method==="GET"&&u.pathname==="/candidates"){try{const c=await env.DB.prepare("SELECT source,title,url,score,velocity_pct,risk_flags,category,last_seen_at FROM signals WHERE risk_flags IS NULL AND score>=45 ORDER BY score DESC,last_seen_at DESC LIMIT 30").all();return new Response(JSON.stringify({ok:true,candidates:c?.results||[]}),{headers:JSON_HEADERS});}catch(e){return new Response(JSON.stringify({ok:false,error:String(e)}),{status:500,headers:JSON_HEADERS});}}
    return new Response(JSON.stringify({error:"not_found"}),{status:404,headers:JSON_HEADERS});
},async scheduled(_controller,env,ctx){ctx.waitUntil(runOnce(env));}};