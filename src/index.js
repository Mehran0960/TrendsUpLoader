const JSON_HEADERS={"content-type":"application/json; charset=UTF-8","cache-control":"no-store"};
const GEOS=["US","IR"],HN_TOP=10;
const RSS_FEEDS=[
  {source:"ars_technica",url:"https://feeds.arstechnica.com/arstechnica/index",category:"technology"},
  {source:"techcrunch",url:"https://techcrunch.com/feed/",category:"business"}
];
const RISK_RULES=[
  [/\b(porn|xxx|sex|onlyfans)\b|پورن|سکس|مستهجن|فحشا/i,"adult"],
  [/\b(gambling|casino|betting)\b|قمار|شرط.?بندی|کازینو/i,"gambling"],
  [/\b(war|attack|terror|explosion|murder|killed|death)\b|جنگ|حمله|ترور|انفجار|قتل|کشته|مرگ|خشونت/i,"violence"],
  [/\b(election|president|politics|political|parliament|government)\b|انتخابات|رئیس.?جمهور|سیاست|سیاسی|پارلمان|مجلس|دولت/i,"politics"],
  [/\b(earthquake|flood|hurricane|wildfire)\b|زلزله|سیل|طوفان|آتش.?سوزی/i,"disaster"]
];
const CONTENT_RULES=[
  [/\b(ai|artificial intelligence|llm|chatgpt|claude|gemini|openai|anthropic|agent|agents|robot|robotics)\b|هوش مصنوعی|چت.?جی.?پی.?تی|کلود|جمینای|اُپن.?ای|ربات/i,12],
  [/\b(software|github|linux|android|iphone|apple|google|microsoft|coding|developer|programming|browser|app|apps)\b|نرم.?افزار|گیت.?هاب|لینوکس|اندروید|آیفون|برنامه|اپلیکیشن|برنامه.?نویسی/i,9],
  [/\b(gadget|smartphone|laptop|chip|gpu|nvidia|amd|intel|hardware)\b|گجت|گوشی|لپ.?تاپ|تراشه|پردازنده|سخت.?افزار/i,8],
  [/\b(startup|business|entrepreneur|ecommerce|retail|market|economy|finance|investing|money)\b|استارت.?آپ|کسب.?و.?کار|کارآفرینی|فروشگاه|اقتصاد|مالی|سرمایه.?گذاری|پول/i,8],
  [/\b(deal|discount|price|product|tool|tutorial|guide)\b|تخفیف|قیمت|محصول|ابزار|آموزش|راهنما/i,5],
  [/\b(weather|forecast|temperature|climate|horoscope|astrology|lottery|lotto)\b|آب.?وهوا|هوای?\s+(فردا|امروز)|هواشناسی|پیش.?بینی.?هوا|فال|طالع.?بینی|لاتاری/i,-12],
  [/\b(celebrity|actor|actress|singer|influencer)\b|سلبریتی|بازیگر|خواننده|اینفلوئنسر/i,-3]
];
const IR_NOISE_RULES=[
  /^(خبر|news|weather|آب.?وهوا|هواشناسی|وضعیت هوای فردا|هوای امروز|weather tomorrow)$/i,
  /فال|طالع.?بینی|لاتاری|lottery/i,
  /^.+\s+مقابل\s+.+$/i,
  /^.+\s+vs\.?\s+.+$/i
];
const IR_SINGLE_USEFUL=/آیفون|اپل|گوگل|مایکروسافت|اندروید|تلگرام|اینستاگرام|واتساپ|هوش|ربات|تکنولوژی|فناوری|لینوکس|گیت.?هاب|چت.?جی.?پی.?تی|جمینای/i;
function usefulIranTrendTitle(title){
  const t=String(title||"").trim();
  if(!t||IR_NOISE_RULES.some(re=>re.test(t)))return false;
  const tokens=t.split(/\s+/).filter(Boolean);
  return tokens.length>=2||/[A-Za-z0-9]/.test(t)||IR_SINGLE_USEFUL.test(t);
}

function decodeEntities(s){return String(s||"").replace(/&amp;/g,"&").replace(/&lt;/g,"<").replace(/&gt;/g,">").replace(/&quot;/g,'"').replace(/&#39;/g,"'").replace(/&#(x[0-9a-f]+|\d+);/gi,(_,v)=>String.fromCodePoint(v.toLowerCase().startsWith("x")?parseInt(v.slice(1),16):parseInt(v,10)));}
function xmlTag(block,tag){const re=new RegExp("<(?:[\\w-]+:)?"+tag+"[^>]*>([\\s\\S]*?)<\\/(?:[\\w-]+:)?"+tag+">","i");const m=block.match(re);return m?decodeEntities(m[1].trim()):"";}
function parseTraffic(s){const m=String(s||"").replace(/,/g,"").match(/([0-9.]+)([KkMmBb])?/);if(!m)return 0;const n=Number(m[1]);return m[2]?(["K","k"].includes(m[2])?n*1e3:["M","m"].includes(m[2])?n*1e6:n*1e9):n;}
function now(){return new Date().toISOString();}
function hashString(s){let h=2166136261;for(const ch of String(s||"")){h^=ch.charCodeAt(0);h=Math.imul(h,16777619);}return (h>>>0).toString(16).padStart(8,"0");}
function trendKey(s){
  const stop=new Set(["the","a","an","and","or","of","to","in","on","for","with","is","are","was","were","has","have","this","that","new","how","several","discovered","discover","upcoming","will","be","been","being","latest","update","updates","today","tomorrow","according","report","reports","reported","news","says","said","say","over","into","from","after","before","via","what","why","when","where","who","از","به","در","برای","با","و","یا","که","این","آن","یک","بر","را","است","شد","های","هایش","جدید","آخرین","امروز","فردا","خبر","گزارش","گزارشها","گفت","گفته","خواهد","شدند","شده","درمورد","مربوط","توسط"]);
  const important=new Set(["ai","ml","xr","vr","ar","5g","6g","gpu","cpu","api","hn","os"]);
  const normalized=String(s||"").toLowerCase()
    .replace(/[يى]/g,"ی").replace(/ك/g,"ک").replace(/[\u200c\u200d]/g," ")
    .replace(/https?:\/\/\S+/g,"")
    .replace(/[^\p{L}\p{N}]+/gu," ").trim();
  const raw=normalized.split(/\s+/).filter(t=>((t.length>2||important.has(t))&&!stop.has(t)&&!/^\d+$/.test(t)));
  const tokens=[...new Set(raw.map(t=>t.length>5&&t.endsWith("s")?t.slice(0,-1):t))];
  tokens.sort((a,b)=>(b.length-a.length)||a.localeCompare(b));
  return tokens.slice(0,5).sort((a,b)=>a.localeCompare(b)).join(" ");
}

function riskFlags(title){return RISK_RULES.filter(([re])=>re.test(title)).map(([,name])=>name).join(",");}
function contentFit(title){
  let fit=0;
  for(const [re,weight] of CONTENT_RULES)if(re.test(title))fit+=weight;
  return Math.max(-15,Math.min(15,fit));
}
function score(x){
  const fresh=Math.max(0,25-Math.max(0,(Date.now()-new Date(x.published_at||now()).getTime())/3600000));
  const mag=x.source.startsWith("google_trends")?Math.min(50,Math.log10(Math.max(1,x.signal_value))*10):Math.min(45,x.signal_value/5);
  const vel=Math.max(0,Math.min(30,x.velocity_pct>0?Math.log10(1+x.velocity_pct)*15:0));
  const fit=Math.max(-15,Math.min(15,Number(x.content_fit??contentFit(x.title))));
  const risk=x.risk_flags?30:0;
  return Math.round(Math.max(0,Math.min(100,mag+fresh+vel+fit-risk))*100)/100;
}
async function fetchWithRetry(url,json=false){
  let last=null;
  for(let i=0;i<3;i++){
    const controller=new AbortController();
    const timer=setTimeout(()=>controller.abort("upstream_timeout"),10000);
    try{
      const r=await fetch(url,{headers:{"user-agent":"trend-radar-mvp/1.0"},signal:controller.signal});
      if(r.ok)return json?r.json():r.text();
      if([429,502,503,504].includes(r.status)){
        last=new Error("HTTP "+r.status+" from "+url);
        if(i<2)await new Promise(resolve=>setTimeout(resolve,500*(i+1)));
        continue;
      }
      throw new Error("HTTP "+r.status+" from "+url);
    }catch(e){
      last=e?.name==="AbortError"?new Error("Timeout from "+url):e;
      if(i<2)await new Promise(resolve=>setTimeout(resolve,500*(i+1)));
    }finally{
      clearTimeout(timer);
    }
  }
  throw last||new Error("fetch failed: "+url);
}
async function getText(url){return fetchWithRetry(url,false);}
async function getJson(url){return fetchWithRetry(url,true);}
async function readGoogleTrends(geo){const xml=await getText("https://trends.google.com/trending/rss?geo="+geo),out=[];for(const block of xml.split(/<item>/i).slice(1,41)){const b=block.split(/<\/item>/i)[0],title=xmlTag(b,"title");if(!title)continue;const link=xmlTag(b,"link"),pub=xmlTag(b,"pubDate"),traffic=xmlTag(b,"approx_traffic");const explore="https://trends.google.com/trends/explore?q="+encodeURIComponent(title)+"&geo="+encodeURIComponent(geo);out.push({source:"google_trends_"+geo.toLowerCase(),external_id:title.toLowerCase(),trend_key:trendKey(title),title,url:explore,published_at:pub?new Date(pub).toISOString():now(),signal_value:parseTraffic(traffic),category:geo==="IR"?"iran":"web",risk_flags:riskFlags(title)});}return out;}
async function readRssFeed(source,feed,category){
  const xml=await getText(feed),out=[];
  for(const [i,block] of xml.split(/<item>/i).slice(1,21).entries()){
    const b=block.split(/<\/item>/i)[0],title=xmlTag(b,"title");
    if(!title)continue;
    const link=xmlTag(b,"link"),pub=xmlTag(b,"pubDate"),guid=xmlTag(b,"guid");
    out.push({source,external_id:guid||link||title.toLowerCase(),trend_key:trendKey(title),title,url:link||null,published_at:pub?new Date(pub).toISOString():now(),signal_value:Math.max(1,90-i*3),category,risk_flags:riskFlags(title)});
  }
  return out;
}
async function readGoogleNews(source,feed,category){
  const xml=await getText(feed),out=[];
  for(const [i,block] of xml.split(/<item>/i).slice(1,21).entries()){
    const b=block.split(/<\/item>/i)[0],title=xmlTag(b,"title");
    if(!title)continue;
    const link=xmlTag(b,"link"),pub=xmlTag(b,"pubDate"),sourceName=xmlTag(b,"source");
    out.push({
      source,external_id:link||title.toLowerCase(),trend_key:trendKey(title),title,
      url:link||null,published_at:pub?new Date(pub).toISOString():now(),
      signal_value:Math.max(1,80-i*3),category,risk_flags:riskFlags(title),
      source_name:sourceName||null
    });
  }
  return out;
}
async function readHackerNews(){
  const ids=(await getJson("https://hacker-news.firebaseio.com/v0/beststories.json")).slice(0,HN_TOP);
  const rr=await Promise.allSettled(ids.map(id=>getJson("https://hacker-news.firebaseio.com/v0/item/"+id+".json")));
  const out=[];
  for(let i=0;i<rr.length;i++){
    const item=rr[i].status==="fulfilled"?rr[i].value:null;
    const id=ids[i];
    if(item&&item.type==="story"&&item.title)out.push({
      source:"hacker_news",external_id:String(id),trend_key:trendKey(item.title),title:item.title,
      url:item.url||("https://news.ycombinator.com/item?id="+id),
      published_at:new Date(Number(item.time||0)*1000).toISOString(),
      signal_value:Number(item.score||0),category:"technology",risk_flags:riskFlags(item.title)
    });
  }
  return out;
}
function cosine(a,b){
  let dot=0,na=0,nb=0;
  const n=Math.min(a.length,b.length);
  for(let i=0;i<n;i++){const x=Number(a[i])||0,y=Number(b[i])||0;dot+=x*y;na+=x*x;nb+=y*y;}
  return na&&nb?dot/Math.sqrt(na*nb):0;
}
async function enrichSemantics(env,signals){
  if(!env.AI||!signals.length)return {embedded:0,clusters:0};
  const fresh=signals
    .filter(x=>(!x.risk_flags)&&Number(x.content_fit??contentFit(x.title??""))>=0)
    .sort((a,b)=>Number(b.signal_value||0)-Number(a.signal_value||0))
    .slice(0,30);
  if(!fresh.length)return {embedded:0,clusters:0};

  const currentKeys=new Set(fresh.map(x=>String(x.source)+"::"+String(x.external_id)));
  const existing=await env.DB.prepare("SELECT id,source,external_id,title,embedding_json,semantic_cluster FROM signals WHERE embedding_json IS NOT NULL ORDER BY last_seen_at DESC LIMIT 120").all();
  const refs=[];
  for(const row of (existing.results||[])){
    if(currentKeys.has(String(row.source)+"::"+String(row.external_id)))continue;
    try{
      const v=JSON.parse(row.embedding_json);
      if(Array.isArray(v)&&v.length)refs.push({id:Number(row.id),source:String(row.source),title:String(row.title||""),embedding:v,cluster:row.semantic_cluster||null});
    }catch(_){}
  }

  const irRows=await env.DB.prepare("SELECT id,source,external_id,title,embedding_json,semantic_cluster FROM signals WHERE source='google_trends_ir' ORDER BY last_seen_at DESC LIMIT 40").all();
  const iranNeeds=[];
  const iranPool=[];
  const embeddedIrKeys=new Set();

  for(const row of (irRows.results||[])){
    if(!usefulIranTrendTitle(row.title))continue;
    const key=String(row.source)+"::"+String(row.external_id);
    if(currentKeys.has(key))continue;
    try{
      const v=JSON.parse(row.embedding_json);
      if(Array.isArray(v)&&v.length){
        iranPool.push({title:String(row.title||""),embedding:v});
        embeddedIrKeys.add(key);
        refs.push({id:Number(row.id),source:String(row.source),title:String(row.title||""),embedding:v,cluster:row.semantic_cluster||null});
        continue;
      }
    }catch(_){}
    if(iranNeeds.length<20)iranNeeds.push(row);
  }

  const aiInputs=[...fresh.map(x=>({kind:"fresh",item:x})),...iranNeeds.map(x=>({kind:"iran",item:x}))];
  const resp=await env.AI.run("@cf/baai/bge-m3",{text:aiInputs.map(x=>String(x.item.title||""))});
  const vectors=Array.isArray(resp?.data)?resp.data:[];

  const freshVectors=new Map();
  const irVectors=[];
  for(let i=0;i<aiInputs.length;i++){
    const item=aiInputs[i],v=vectors[i];
    if(!Array.isArray(v)||!v.length)continue;
    if(item.kind==="fresh")freshVectors.set(String(item.item.source)+"::"+String(item.item.external_id),v);
    else{
      const key=String(item.item.source)+"::"+String(item.item.external_id);
      irVectors.push({row:item.item,v,key});
      iranPool.push({title:String(item.item.title||""),embedding:v});
      refs.push({id:Number(item.item.id),source:"google_trends_ir",title:String(item.item.title||""),embedding:v,cluster:item.item.semantic_cluster||null});
    }
  }

  let embedded=0;
  const updates=[];
  for(const x of fresh){
    const key=String(x.source)+"::"+String(x.external_id);
    const v=freshVectors.get(key);
    if(!v)continue;

    let best=null;
    for(const ref of refs){
      if(ref.source===String(x.source))continue;
      const sim=cosine(v,ref.embedding);
      if(!best||sim>best.sim)best={sim,ref};
    }

    let cluster=null,similarity=0;
    if(best&&best.sim>=0.78){
      cluster=best.ref.cluster||("sem-"+best.ref.id);
      similarity=best.sim;
    }else{
      cluster="sem-"+hashString(key);
    }

    let iranSimilarity=0,iranMatch=null;
    if(String(x.source)!=="google_trends_ir"){
      let bestIran=null;
      for(const ref of iranPool){
        const sim=cosine(v,ref.embedding);
        if(!bestIran||sim>bestIran.sim)bestIran={sim,ref};
      }
      if(bestIran){
        iranSimilarity=Number(bestIran.sim)||0;
        iranMatch=bestIran.ref.title||null;
      }
    }

    updates.push(env.DB.prepare("UPDATE signals SET embedding_json=?,semantic_cluster=?,semantic_similarity=?,iran_interest_similarity=?,iran_interest_match=? WHERE source=? AND external_id=?").bind(JSON.stringify(v),cluster,similarity,iranSimilarity,iranMatch,String(x.source),String(x.external_id)));
    refs.push({id:Number(x.id||0),source:String(x.source),title:String(x.title||""),embedding:v,cluster});
    embedded++;
  }

  for(const item of irVectors){
    updates.push(env.DB.prepare("UPDATE signals SET embedding_json=? WHERE source=? AND external_id=?").bind(JSON.stringify(item.v),String(item.row.source),String(item.row.external_id)));
  }

  for(let i=0;i<updates.length;i+=10)await env.DB.batch(updates.slice(i,i+10));
  return {embedded,clusters:embedded,iran_anchors:iranPool.length};
}

async function saveSignals(env,signals){
  const t=now(),normalized=signals.map(x=>({...x,source:String(x?.source??"unknown"),external_id:String(x?.external_id??x?.title??"unknown")}));
  const groups=new Map();
  for(const x of normalized){if(!groups.has(x.source))groups.set(x.source,[]);groups.get(x.source).push(x);}
  const previous=new Map();
  const existing=new Map();
  for(const [source,items] of groups){
    const keys=Array.from(new Set(items.map(x=>String(x.trend_key??x.title??""))));
    if(!keys.length)continue;
    const qs=keys.map(()=>"?").join(",");
    const params=[source,...keys];
    const r=await env.DB.prepare("SELECT source,external_id,trend_key,signal_value,title,url,published_at,category,risk_flags,score,content_fit,previous_value,velocity_pct FROM signals WHERE source=? AND trend_key IN ("+qs+")").bind(...params).all();
    for(const row of (r.results||[])){
      const ek=source+"::"+String(row.external_id??"");
      existing.set(ek,row);
      const k=source+"::"+String(row.trend_key??"");
      if(!previous.has(k))previous.set(k,Number(row.signal_value||0));
    }
  }

  const stmts=[];
  const observationCutoff=new Date(Date.now()-3600000).toISOString();

  for(const x of normalized){
    const ek=x.source+"::"+String(x.external_id);
    const prev=previous.get(x.source+"::"+String(x.trend_key??x.title??""))||0;
    const velocity=prev>0?((x.signal_value-prev)/Math.abs(prev))*100:0;
    const safe={
      source:String(x.source??"unknown"),
      external_id:String(x.external_id??x.title??"unknown"),
      trend_key:String(x.trend_key??x.title??""),
      title:String(x.title??"untitled"),
      url:x.url??null,
      published_at:x.published_at??t,
      signal_value:Number(x.signal_value??0),
      category:x.category??null,
      risk_flags:x.risk_flags??null,
      previous_value:prev,
      velocity_pct:velocity,
      content_fit:Number(x.content_fit??contentFit(x.title??""))
    };
    const s=score(safe);
    const old=existing.get(ek);
    const changed=!old ||
      String(old.trend_key??"")!==safe.trend_key ||
      String(old.title??"")!==safe.title ||
      String(old.url??"")!==String(safe.url??"") ||
      String(old.published_at??"")!==String(safe.published_at??"") ||
      String(old.category??"")!==String(safe.category??"") ||
      String(old.risk_flags??"")!==String(safe.risk_flags??"") ||
      Number(old.signal_value??0)!==safe.signal_value ||
      Number(old.previous_value??0)!==safe.previous_value ||
      Number(old.velocity_pct??0)!==safe.velocity_pct ||
      Number(old.score??0)!==s ||
      Number(old.content_fit??0)!==safe.content_fit;

    if(!old){
      stmts.push(env.DB.prepare("INSERT INTO signals(source,external_id,trend_key,title,url,published_at,first_seen_at,last_seen_at,signal_value,category,risk_flags,previous_value,velocity_pct,score,content_fit) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)").bind(safe.source,safe.external_id,safe.trend_key,safe.title,safe.url,safe.published_at,t,t,safe.signal_value,safe.category,safe.risk_flags,safe.previous_value,safe.velocity_pct,s,safe.content_fit));
    }else if(changed){
      stmts.push(env.DB.prepare("UPDATE signals SET trend_key=?,title=?,url=?,published_at=?,last_seen_at=?,signal_value=?,category=?,risk_flags=?,previous_value=?,velocity_pct=?,score=?,content_fit=? WHERE source=? AND external_id=?").bind(safe.trend_key,safe.title,safe.url,safe.published_at,t,safe.signal_value,safe.category,safe.risk_flags,safe.previous_value,safe.velocity_pct,s,safe.content_fit,safe.source,safe.external_id));
    }

    stmts.push(env.DB.prepare("INSERT INTO observations(source,external_id,trend_key,observed_at,signal_value,score,risk_flags) SELECT ?,?,?,?,?,?,? WHERE NOT EXISTS (SELECT 1 FROM observations WHERE source=? AND external_id=? AND observed_at>?)").bind(safe.source,safe.external_id,safe.trend_key,t,safe.signal_value,s,safe.risk_flags,safe.source,safe.external_id,observationCutoff));
  }

  for(let i=0;i<stmts.length;i+=10)await env.DB.batch(stmts.slice(i,i+10));
}
function withTimeout(promise,ms,label){
  return Promise.race([
    promise,
    new Promise((_,reject)=>setTimeout(()=>reject(new Error(label+" timeout after "+ms+"ms")),ms))
  ]);
}

async function runOnce(env,controller){
  const scheduledAt=Number(controller?.scheduledTime||Date.now());
  const d=new Date(scheduledAt);
  const minute=d.getUTCMinutes();
  const hour=d.getUTCHours();
  const minuteOfDay=hour*60+minute;
  const collectTrends=minute%15===0;
  const runSemantics=minuteOfDay%60===0;
  const started=now();

  // Recover prior invocations that exceeded the expected execution window.
  const staleCutoff=new Date(Date.now()-3*60*1000).toISOString();
  try{
    await withTimeout(
      env.DB.prepare("UPDATE runs SET finished_at=?,status='partial',error=COALESCE(error,'execution exceeded watchdog window') WHERE status='started' AND started_at<?")
        .bind(started,staleCutoff).run(),
      10000,
      "stale_run_cleanup"
    );
  }catch(_){}

  const tasks=[
    ...(collectTrends?GEOS.map((geo)=>withTimeout(readGoogleTrends(geo),45000,"google_trends_"+geo)):[]),
    ...RSS_FEEDS.map(x=>withTimeout(readRssFeed(x.source,x.url,x.category),45000,x.source)),
    withTimeout(readHackerNews(),60000,"hacker_news")
  ];

  const run=await env.DB.prepare("INSERT INTO runs(started_at,status,source_count,signal_count) VALUES(?,?,?,?)")
    .bind(started,"started",tasks.length,0).run();
  const runId=run.meta?.last_row_id;

  const finish=async(status,signalCount,error)=>{
    try{
      await withTimeout(
        env.DB.prepare("UPDATE runs SET finished_at=?,status=?,source_count=?,signal_count=?,error=? WHERE id=?")
          .bind(now(),status,0,signalCount,error||null,runId).run(),
        10000,
        "run_finalize"
      );
    }catch(_){}
  };

  try{
    let rr;
    try{
      rr=await withTimeout(Promise.allSettled(tasks),75000,"source_collection");
    }catch(e){
      await finish("failed",0,String(e));
      return {ok:false,signals:0,errors:[String(e)]};
    }

    const signals=rr.flatMap(r=>r.status==="fulfilled"&&Array.isArray(r.value)?r.value:[]);
    const errors=rr.filter(r=>r.status==="rejected").map(r=>String(r.reason));

    try{
      await withTimeout(saveSignals(env,signals),60000,"save_signals");
    }catch(e){
      await finish("failed",signals.length,String(e));
      return {ok:false,signals:signals.length,errors:[...errors,String(e)]};
    }

    let semanticError=null;
    if(runSemantics){
      try{
        await withTimeout(enrichSemantics(env,signals),90000,"semantic_enrichment");
      }catch(e){
        semanticError=String(e);
      }
    }

    const allErrors=[...errors,...(semanticError?[semanticError]:[])];
    await finish(allErrors.length?"partial":"ok",signals.length,allErrors.join(" | "));
    return {ok:errors.length===0,signals:signals.length,errors};
  }catch(e){
    await finish("failed",0,String(e));
    return {ok:false,signals:0,errors:[String(e)]};
  }
}
export default {async fetch(request,env){
  const u=new URL(request.url);
  if(request.method==="GET"&&u.pathname==="/")return new Response(JSON.stringify({service:"trend-radar",mode:"opportunity-validation",publishing_enabled:false,ai_enabled:true,semantic_enabled:true,iran_relevance_enabled:true}),{headers:JSON_HEADERS});
  if(request.method==="GET"&&u.pathname==="/health"){let db="ok";try{await env.DB.prepare("SELECT 1").first();}catch(_){db="error";}return new Response(JSON.stringify({ok:db==="ok",db}),{headers:JSON_HEADERS});}
  if(request.method==="GET"&&u.pathname==="/status"){try{const a=await env.DB.prepare("SELECT COUNT(*) n FROM signals").first(),b=await env.DB.prepare("SELECT COUNT(*) n FROM runs").first(),c=await env.DB.prepare("SELECT source,title,score,velocity_pct,risk_flags,last_seen_at FROM signals ORDER BY score DESC,last_seen_at DESC LIMIT 20").all();return new Response(JSON.stringify({ok:true,signals:a?.n||0,runs:b?.n||0,top:c?.results||[]}),{headers:JSON_HEADERS});}catch(e){return new Response(JSON.stringify({ok:false,error:String(e)}),{status:500,headers:JSON_HEADERS});}}
  if(request.method==="GET"&&u.pathname==="/metrics"){try{const r=await env.DB.prepare("SELECT (SELECT COUNT(*) FROM observations) observations,(SELECT COUNT(DISTINCT source||':'||external_id) FROM signals) entities,COALESCE(AVG(CASE WHEN velocity_pct>0 THEN velocity_pct END),0) avg_positive_velocity,(SELECT COUNT(*) FROM signals WHERE risk_flags IS NOT NULL AND risk_flags<>'') risk_marked,(SELECT COUNT(*) FROM signals WHERE content_fit>0) positive_content_fit,(SELECT COUNT(*) FROM signals WHERE content_fit<0) negative_content_fit,(SELECT COUNT(*) FROM signals WHERE embedding_json IS NOT NULL) semantic_embedded,(SELECT COUNT(DISTINCT semantic_cluster) FROM signals WHERE semantic_cluster IS NOT NULL) semantic_clusters,(SELECT COUNT(*) FROM signals WHERE iran_interest_similarity>0) iran_interest_linked,COALESCE(AVG(CASE WHEN iran_interest_similarity>0 THEN iran_interest_similarity END),0) avg_iran_interest_similarity FROM signals").first();return new Response(JSON.stringify(r||{}),{headers:JSON_HEADERS});}catch(e){return new Response(JSON.stringify({ok:false,error:String(e)}),{status:500,headers:JSON_HEADERS});}}
  if(request.method==="GET"&&u.pathname==="/candidates"){try{const c=await env.DB.prepare("SELECT s.source,s.title,s.url,ROUND(s.score,2) score,ROUND(s.velocity_pct,2) velocity_pct,ROUND(s.content_fit,2) content_fit,ROUND(s.semantic_similarity,3) semantic_similarity,ROUND(s.iran_interest_similarity,3) iran_interest_similarity,s.iran_interest_match,s.risk_flags,s.category,s.last_seen_at,(SELECT COUNT(DISTINCT s2.source) FROM signals s2 WHERE s2.semantic_cluster=s.semantic_cluster AND s.semantic_cluster IS NOT NULL) source_count,ROUND(MIN(100,s.score+CASE WHEN (SELECT COUNT(DISTINCT s2.source) FROM signals s2 WHERE s2.semantic_cluster=s.semantic_cluster AND s.semantic_cluster IS NOT NULL)>=3 THEN 15 WHEN (SELECT COUNT(DISTINCT s2.source) FROM signals s2 WHERE s2.semantic_cluster=s.semantic_cluster AND s.semantic_cluster IS NOT NULL)=2 THEN 8 ELSE 0 END),2) opportunity_score FROM signals s WHERE (s.risk_flags IS NULL OR s.risk_flags='') AND s.content_fit>=-5 AND s.score>=45 ORDER BY opportunity_score DESC,source_count DESC,s.last_seen_at DESC LIMIT 30").all();return new Response(JSON.stringify({ok:true,candidates:c?.results||[]}),{headers:JSON_HEADERS});}catch(e){return new Response(JSON.stringify({ok:false,error:String(e)}),{status:500,headers:JSON_HEADERS});}}
    return new Response(JSON.stringify({error:"not_found"}),{status:404,headers:JSON_HEADERS});
},async scheduled(controller,env){return await runOnce(env,controller);}};