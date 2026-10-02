const JSON_HEADERS = { "content-type": "application/json; charset=UTF-8" };

export default {
  async fetch(request, env) {
    const url = new URL(request.url);
    if (request.method === "GET" && url.pathname === "/") {
      return new Response(JSON.stringify({service:"trend-radar",status:"ok",mode:"preflight-only",publishing_enabled:false,ai_enabled:false}), {headers:JSON_HEADERS});
    }
    if (request.method === "GET" && url.pathname === "/health") {
      return new Response(JSON.stringify({ok:true,now:new Date().toISOString()}), {headers:JSON_HEADERS});
    }
    return new Response(JSON.stringify({error:"not_found"}), {status:404,headers:JSON_HEADERS});
  },
  async scheduled(controller, env, ctx) {
    // Intentionally empty: provider/quota preflight must pass first.
  }
};
