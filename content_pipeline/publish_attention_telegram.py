#!/usr/bin/env python3
"""Publish a validated Viral Radar single-video result to Telegram."""
import json
import os
import uuid
from datetime import datetime, timezone
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

def main():
    token=os.environ.get("TELEGRAM_BOT_TOKEN","").strip()
    chat_id=os.environ.get("TELEGRAM_CHAT_ID","").strip()
    if not token or not chat_id:
        error={"stage":"configuration","error":"TELEGRAM_BOT_TOKEN or TELEGRAM_CHAT_ID is missing"}
        out_dir=Path("out")
        candidates=sorted(out_dir.glob("**/metadata.json"),key=lambda x:x.stat().st_mtime,reverse=True)
        if candidates:
            target=candidates[0].parent/"publish_error.json"
        else:
            out_dir.mkdir(parents=True,exist_ok=True)
            target=out_dir/"publish_error.json"
        target.write_text(json.dumps(error,ensure_ascii=False,indent=2),encoding="utf-8")
        raise RuntimeError(json.dumps(error,ensure_ascii=False))
    videos=sorted(Path("out").glob("**/video.mp4"),key=lambda p:p.stat().st_mtime,reverse=True)
    if not videos:
        print("Publish skipped: no video.")
        return 0
    video=videos[0]
    meta=json.loads((video.parent/'metadata.json').read_text(encoding='utf-8'))
    if meta.get("content_type")!="viral_radar_licensed_single_video" or meta.get("quality_gate")!="passed_publish":
        raise RuntimeError("Refusing unvalidated attention content.")
    if float(meta.get("duration_seconds") or 0)<1:
        raise RuntimeError("Refusing zero-length attention content.")
    boundary='----Attention'+uuid.uuid4().hex
    chunks=[]
    fields={'chat_id':chat_id,'caption':str(meta.get('display_title_fa') or '🔥 یه ترکیب غیرمنتظره')}
    for key,value in fields.items():
        chunks.extend([f'--{boundary}\r\n'.encode(),f'Content-Disposition: form-data; name="{key}"\r\n\r\n'.encode(),str(value).encode('utf-8'),b'\r\n'])
    chunks.extend([f'--{boundary}\r\n'.encode(),b'Content-Disposition: form-data; name="video"; filename="video.mp4"\r\n',b'Content-Type: video/mp4\r\n\r\n',video.read_bytes(),b'\r\n',f'--{boundary}--\r\n'.encode()])
    body=b''.join(chunks)
    request_url=f'https://api.telegram.org/bot{token}/sendVideo'
    try:
        req=Request(request_url,data=body,method='POST',headers={
            'Content-Type':f'multipart/form-data; boundary={boundary}',
            'Content-Length':str(len(body)),
            'User-Agent':'attention-remix-publisher/3.0',
        })
        with urlopen(req,timeout=90) as resp:
            result=json.loads(resp.read().decode('utf-8','replace'))
    except HTTPError as exc:
        raw=exc.read().decode('utf-8','replace')
        try:
            result=json.loads(raw)
        except Exception:
            result={'ok':False,'http_status':exc.code,'raw':raw}
        error={
            'stage':'sendVideo',
            'http_status':exc.code,
            'error':result,
            'video_bytes':video.stat().st_size,
            'video_mb':round(video.stat().st_size/1024/1024,2),
            'duration_seconds':meta.get('duration_seconds'),
            'content_type':meta.get('content_type'),
            'quality_gate':meta.get('quality_gate'),
        }
        (video.parent/'publish_error.json').write_text(
            json.dumps(error,ensure_ascii=False,indent=2),
            encoding='utf-8'
        )
        raise RuntimeError(json.dumps(error,ensure_ascii=False))
    except URLError as exc:
        error={
            'stage':'sendVideo',
            'network_error':str(exc),
            'video_bytes':video.stat().st_size,
            'video_mb':round(video.stat().st_size/1024/1024,2),
            'duration_seconds':meta.get('duration_seconds'),
        }
        (video.parent/'publish_error.json').write_text(
            json.dumps(error,ensure_ascii=False,indent=2),
            encoding='utf-8'
        )
        raise RuntimeError(json.dumps(error,ensure_ascii=False))
    if not result.get("ok"):
        error={
            'stage':'sendVideo',
            'error':result,
            'video_bytes':video.stat().st_size,
            'video_mb':round(video.stat().st_size/1024/1024,2),
            'duration_seconds':meta.get('duration_seconds'),
        }
        (video.parent/'publish_error.json').write_text(
            json.dumps(error,ensure_ascii=False,indent=2),
            encoding='utf-8'
        )
        raise RuntimeError(json.dumps(error,ensure_ascii=False))
    member_count = None
    try:
        count_req = Request(
            f"https://api.telegram.org/bot{token}/getChatMemberCount?chat_id={chat_id}",
            headers={"User-Agent":"attention-remix-publisher/2.1"},
        )
        with urlopen(count_req, timeout=30) as resp:
            count_result=json.loads(resp.read().decode("utf-8","replace"))
        if count_result.get("ok"):
            member_count=int(count_result.get("result"))
    except Exception as exc:
        print("Member count unavailable:", exc)

    publish = {
        "message_id": (result.get("result") or {}).get("message_id"),
        "experiment": meta.get("experiment"),
        "categories": meta.get("actual_categories") or [x.get("category") for x in meta.get("sources",[])],
        "published_at": datetime.now(timezone.utc).isoformat(),
        "member_count": member_count,
        "content_key": meta.get("content_key"),
        "combination_key": meta.get("combination_key"),
    }
    (video.parent/"publish.json").write_text(
        json.dumps(publish,ensure_ascii=False,indent=2),
        encoding="utf-8"
    )
    print(json.dumps(publish,ensure_ascii=False))
    return 0

if __name__=='__main__':
    main()