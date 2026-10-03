import subprocess, sys, wave
from pathlib import Path

TEXT = "امروز یک خبر مهم در دنیای فناوری منتشر شده. چند آسیب‌پذیری در هستهٔ لینوکس پیدا شده و نسخهٔ جدید برای برطرف کردن آن‌ها منتشر شده است. در این نمونه فقط کیفیت صدا، تلفظ و لحن فارسی را مقایسه می‌کنیم."
VOICES = [
    ("amir", "fa_IR-amir-medium"),
    ("ganji", "fa_IR-ganji-medium"),
    ("ganji_adabi", "fa_IR-ganji_adabi-medium"),
    ("gyro", "fa_IR-gyro-medium"),
    ("reza_ibrahim", "fa_IR-reza_ibrahim-medium"),
]
OUT = Path("voice_out")
OUT.mkdir(exist_ok=True)
VOICE_DIR = Path(".voices")
VOICE_DIR.mkdir(exist_ok=True)

def run(cmd):
    subprocess.run(cmd, check=True)

def duration(path):
    with wave.open(str(path), "rb") as wf:
        return wf.getnframes()/wf.getframerate()

parts=[]
for name, model in VOICES:
    wav=OUT/f"{name}.wav"
    mp3=OUT/f"{name}.mp3"
    run([sys.executable, "-m", "piper.download_voices", model, "--data-dir", str(VOICE_DIR)])
    run([sys.executable, "-m", "piper", "-m", model, "--data-dir", str(VOICE_DIR),
         "-f", str(wav), "--", TEXT])
    run(["ffmpeg","-y","-i",str(wav),"-codec:a","libmp3lame","-q:a","3",str(mp3)])
    parts.append((name, wav, duration(wav)))

# Build one simple comparison MP4: each voice is a labeled card + its audio.
W,H=720,1280
segments=[]
from PIL import Image, ImageDraw, ImageFont

def make_card(name, wav, idx):
    img=Image.new("RGB",(W,H),(9,14,28))
    d=ImageDraw.Draw(img)
    for y in range(H):
        v=int(18+35*y/H)
        d.line((0,y,W,y),fill=(9,14+v//3,28+v//2))
    f=ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",60)
    title={"amir":"AMIR","ganji":"GANJI","ganji_adabi":"GANJI ADABI","gyro":"GYRO","reza_ibrahim":"REZA IBRAHIM"}[name]
    d.text((W/2,300),title,font=f,anchor="mm",fill=(240,245,250))
    small=ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",34)
    d.text((W/2,420),"Persian voice comparison",font=small,anchor="mm",fill=(175,195,215))
    pf=ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",28)
    d.text((W/2,540),f"Sample {idx}/5",font=pf,anchor="mm",fill=(145,170,195))
    p=OUT/f"{name}.png"; img.save(p); return p

for i,(name,wav,dur) in enumerate(parts,1):
    img=make_card(name,wav,i)
    seg=OUT/f"{name}.mp4"
    run(["ffmpeg","-y","-loop","1","-i",str(img),"-i",str(wav),"-t",f"{dur:.3f}",
         "-r","30","-c:v","libx264","-pix_fmt","yuv420p","-c:a","aac","-b:a","128k",
         "-shortest","-movflags","+faststart",str(seg)])
    segments.append(seg)

manifest=OUT/"concat.txt"
manifest.write_text("\n".join("file '"+p.resolve().as_posix()+"'" for p in segments)+"\n")
run(["ffmpeg","-y","-f","concat","-safe","0","-i",str(manifest),"-c","copy","-movflags","+faststart",
     str(OUT/"persian-voice-comparison.mp4")])

print("\n".join(f"{name}: {dur:.2f}s" for name,_,dur in parts))
