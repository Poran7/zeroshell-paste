"""ZeroShell v7.0 - PWA + API + 2FA + AI + Diff + Analytics"""
import os,hashlib,secrets,json,re,hmac,struct,time,base64
from datetime import datetime,timedelta
from flask import Flask,request,redirect,session,flash,get_flashed_messages,Response,jsonify

app=Flask(__name__)
app.secret_key=secrets.token_hex(32)
import sqlite3
DB="zeroshell.db"

# ━━━ DB ━━━
def get_db():
    db=sqlite3.connect(DB); db.row_factory=sqlite3.Row; return db

def init_db():
    db=get_db()
    db.executescript("""
    CREATE TABLE IF NOT EXISTS users(id INTEGER PRIMARY KEY AUTOINCREMENT,username TEXT UNIQUE NOT NULL,email TEXT DEFAULT '',password TEXT NOT NULL,bio TEXT DEFAULT '',telegram TEXT DEFAULT '',avatar TEXT DEFAULT '👤',theme TEXT DEFAULT 'cyan',is_admin INTEGER DEFAULT 0,created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,total_views INTEGER DEFAULT 0,totp_secret TEXT DEFAULT '',totp_enabled INTEGER DEFAULT 0,api_key TEXT DEFAULT '');
    CREATE TABLE IF NOT EXISTS pastes(id INTEGER PRIMARY KEY AUTOINCREMENT,slug TEXT UNIQUE NOT NULL,title TEXT NOT NULL,content TEXT NOT NULL,syntax TEXT DEFAULT 'text',tags TEXT DEFAULT '',visibility TEXT DEFAULT 'public',password TEXT DEFAULT '',views INTEGER DEFAULT 0,likes INTEGER DEFAULT 0,dislikes INTEGER DEFAULT 0,pinned INTEGER DEFAULT 0,user_id INTEGER,created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,expires_at TIMESTAMP DEFAULT NULL,ai_summary TEXT DEFAULT '');
    CREATE TABLE IF NOT EXISTS comments(id INTEGER PRIMARY KEY AUTOINCREMENT,paste_id INTEGER,user_id INTEGER,content TEXT NOT NULL,created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP);
    CREATE TABLE IF NOT EXISTS notifications(id INTEGER PRIMARY KEY AUTOINCREMENT,user_id INTEGER,message TEXT NOT NULL,link TEXT DEFAULT '',read INTEGER DEFAULT 0,created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP);
    CREATE TABLE IF NOT EXISTS follows(id INTEGER PRIMARY KEY AUTOINCREMENT,follower_id INTEGER,following_id INTEGER,created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,UNIQUE(follower_id,following_id));
    CREATE TABLE IF NOT EXISTS paste_likes(id INTEGER PRIMARY KEY AUTOINCREMENT,paste_id INTEGER,user_id INTEGER,vote INTEGER,UNIQUE(paste_id,user_id));
    CREATE TABLE IF NOT EXISTS paste_views(id INTEGER PRIMARY KEY AUTOINCREMENT,paste_id INTEGER,viewer_key TEXT,created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,UNIQUE(paste_id,viewer_key));
    CREATE TABLE IF NOT EXISTS activity(id INTEGER PRIMARY KEY AUTOINCREMENT,user_id INTEGER,action TEXT,target_id INTEGER,target_type TEXT,created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP);
    CREATE TABLE IF NOT EXISTS ads(id INTEGER PRIMARY KEY AUTOINCREMENT,title TEXT NOT NULL,content TEXT NOT NULL,url TEXT DEFAULT '',active INTEGER DEFAULT 1,created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP);
    """)
    safe=[("users","avatar","TEXT DEFAULT '👤'"),("users","theme","TEXT DEFAULT 'cyan'"),("users","is_admin","INTEGER DEFAULT 0"),("users","email","TEXT DEFAULT ''"),("users","totp_secret","TEXT DEFAULT ''"),("users","totp_enabled","INTEGER DEFAULT 0"),("users","api_key","TEXT DEFAULT ''"),("pastes","password","TEXT DEFAULT ''"),("pastes","pinned","INTEGER DEFAULT 0"),("pastes","expires_at","TIMESTAMP DEFAULT NULL"),("pastes","tags","TEXT DEFAULT ''"),("pastes","likes","INTEGER DEFAULT 0"),("pastes","dislikes","INTEGER DEFAULT 0"),("pastes","ai_summary","TEXT DEFAULT ''")]
    for t,c,d in safe:
        try: db.execute(f"ALTER TABLE {t} ADD COLUMN {c} {d}")
        except: pass
    db.commit(); db.close()

def cleanup_expired():
    try:
        db=get_db()
        now=datetime.now().isoformat()
        deleted=db.execute("DELETE FROM pastes WHERE expires_at IS NOT NULL AND expires_at < ?",(now,))
        db.commit(); db.close()
        return deleted.rowcount
    except: return 0

# ━━━ HELPERS ━━━
def hash_pw(pw): return hashlib.sha256(pw.encode()).hexdigest()
def rand_slug(n=8): return secrets.token_urlsafe(n)[:n]
def is_expired(p):
    if not p['expires_at']: return False
    try: return datetime.now()>datetime.fromisoformat(str(p['expires_at']))
    except: return False

def viewer_key(slug):
    ip=request.remote_addr or 'x'
    uid=str(session.get('user_id','anon'))
    return hash_pw(f"{slug}:{ip}:{uid}")[:16]

def count_unique_view(paste_id,slug):
    key=viewer_key(slug)
    try:
        db=get_db()
        db.execute("INSERT OR IGNORE INTO paste_views(paste_id,viewer_key) VALUES(?,?)",(paste_id,key))
        changed=db.execute("SELECT changes()").fetchone()[0]
        if changed: db.execute("UPDATE pastes SET views=views+1 WHERE id=?",(paste_id,))
        db.commit(); db.close(); return changed==1
    except: return False

def send_notif(uid,msg,link=''):
    try:
        db=get_db(); db.execute("INSERT INTO notifications(user_id,message,link) VALUES(?,?,?)",(uid,msg,link)); db.commit(); db.close()
    except: pass

def log_activity(uid,action,target_id=0,target_type=''):
    try:
        db=get_db(); db.execute("INSERT INTO activity(user_id,action,target_id,target_type) VALUES(?,?,?,?)",(uid,action,target_id,target_type)); db.commit(); db.close()
    except: pass

def unread_count(uid):
    if not uid: return 0
    try:
        db=get_db(); c=db.execute("SELECT COUNT(*) FROM notifications WHERE user_id=? AND read=0",(uid,)).fetchone()[0]; db.close(); return c
    except: return 0

# ━━━ TOTP (no external lib) ━━━
def totp_gen_secret(): return base64.b32encode(secrets.token_bytes(20)).decode()

def totp_hotp(secret,counter):
    key=base64.b32decode(secret.upper()+'='*8,casefold=True)
    msg=struct.pack('>Q',counter)
    h=hmac.new(key,msg,'sha1').digest()
    offset=h[-1]&0xf
    code=struct.unpack('>I',h[offset:offset+4])[0]&0x7fffffff
    return str(code%1000000).zfill(6)

def totp_now(secret):
    return totp_hotp(secret,int(time.time())//30)

def totp_verify(secret,code):
    t=int(time.time())//30
    for i in [-1,0,1]:
        if totp_hotp(secret,t+i)==str(code): return True
    return False

def totp_uri(secret,username):
    return f"otpauth://totp/ZeroShell:{username}?secret={secret}&issuer=ZeroShell"

def get_badge(views,p30):
    if views>=10000: return('Legendary','👑','#ffd700')
    if views>=5000: return('Famous','⚡','#ff6b00')
    if views>=1000: return('Popular','🔥','#ff2d55')
    if p30>=5: return('Active','🏃','#00f5ff')
    return('Newcomer','⭐','#8899aa')

THEMES={'cyan':'#00f5ff','red':'#ff2d55','green':'#00ff88','gold':'#ffd60a','purple':'#bf5af2','blue':'#2979ff'}
AVATARS=['👤','⚡','🔥','💀','🤖','👾','🦊','🐉','🎭','🔮','🦅','🐺']
EXPIRE_OPTS=[('','Never'),('1h','1 Hour'),('1d','1 Day'),('1w','1 Week'),('1m','1 Month')]
ALL_TAGS=['python','javascript','html','css','bash','json','config','snippet','tutorial','other']

# ━━━ SYNTAX HIGHLIGHT ━━━
def highlight(code,lang):
    import html as h; code=h.escape(code)
    if lang=='python':
        code=re.sub(r'(#[^\n]*)','<span style="color:#6272a4">\\1</span>',code)
        code=re.sub(r'("""[\s\S]*?"""|\'\'\'[\s\S]*?\'\'\'|"[^"]*"|\'[^\']*\')','<span style="color:#f1fa8c">\\1</span>',code)
        code=re.sub(r'\b(def|class|import|from|return|if|elif|else|for|while|in|not|and|or|try|except|finally|with|as|pass|break|continue|lambda|yield|True|False|None)\b','<span style="color:#ff79c6">\\1</span>',code)
        code=re.sub(r'\b(\d+\.?\d*)\b','<span style="color:#bd93f9">\\1</span>',code)
        code=re.sub(r'\b(print|len|range|int|str|float|list|dict|set|type|open|input)\b','<span style="color:#8be9fd">\\1</span>',code)
    elif lang=='javascript':
        code=re.sub(r'(//[^\n]*)','<span style="color:#6272a4">\\1</span>',code)
        code=re.sub(r'(`[^`]*`|"[^"]*"|\'[^\']*\')','<span style="color:#f1fa8c">\\1</span>',code)
        code=re.sub(r'\b(const|let|var|function|return|if|else|for|while|class|import|export|from|new|this|async|await|try|catch|true|false|null|undefined)\b','<span style="color:#ff79c6">\\1</span>',code)
        code=re.sub(r'\b(\d+\.?\d*)\b','<span style="color:#bd93f9">\\1</span>',code)
    elif lang=='html':
        code=re.sub(r'(&lt;/?)([\w-]+)','\\1<span style="color:#ff79c6">\\2</span>',code)
        code=re.sub(r'("([^"]*)")','<span style="color:#f1fa8c">\\1</span>',code)
    elif lang=='json':
        code=re.sub(r'"([^"]+)"(\s*:)','<span style="color:#8be9fd">"\\1"</span>\\2',code)
        code=re.sub(r'(:\s*)"([^"]*)"','\\1<span style="color:#f1fa8c">"\\2"</span>',code)
        code=re.sub(r'\b(true|false|null)\b','<span style="color:#ff79c6">\\1</span>',code)
    elif lang=='bash':
        code=re.sub(r'(#[^\n]*)','<span style="color:#6272a4">\\1</span>',code)
        code=re.sub(r'("[^"]*"|\'[^\']*\')','<span style="color:#f1fa8c">\\1</span>',code)
        code=re.sub(r'\b(if|then|else|fi|for|while|do|done|echo|export|cd|ls|mkdir|rm|git|pip|python|sudo)\b','<span style="color:#ff79c6">\\1</span>',code)
        code=re.sub(r'(\$[\w{}\(\)]+)','<span style="color:#50fa7b">\\1</span>',code)
    elif lang=='sql':
        code=re.sub(r'\b(SELECT|FROM|WHERE|INSERT|UPDATE|DELETE|CREATE|DROP|TABLE|JOIN|LEFT|RIGHT|ON|AS|ORDER|BY|GROUP|HAVING|LIMIT|AND|OR|NOT|IN|NULL)\b','<span style="color:#ff79c6">\\1</span>',code,flags=re.I)
        code=re.sub(r'("[^"]*"|\'[^\']*\')','<span style="color:#f1fa8c">\\1</span>',code)
    return code

# ━━━ STYLE ━━━
def style(theme='cyan',light=False):
    p=THEMES.get(theme,'#00f5ff')
    bg,card,border,text,dim=('#f0f4f8','#fff','#d0dce8','#1a2a3a','#7a9ab0') if light else ('#04080f','#0b1623','#0f2a40','#c8e0f0','#4a6a80')
    nav_bg='rgba(240,244,248,.97)' if light else 'rgba(11,22,35,.97)'
    code_bg='#f8fafc' if light else '#020810'
    code_col='#2d3748' if light else '#a8d0e0'
    return f"""<link href="https://fonts.googleapis.com/css2?family=Share+Tech+Mono&family=Rajdhani:wght@400;600;700&display=swap" rel="stylesheet">
<script src="https://cdn.jsdelivr.net/npm/chart.js"></script>
<style>
:root{{--bg:{bg};--card:{card};--border:{border};--p:{p};--green:#00cc66;--red:#ff2d55;--yellow:#e6b800;--text:{text};--dim:{dim};}}
*{{margin:0;padding:0;box-sizing:border-box;}}
body{{background:var(--bg);color:var(--text);font-family:'Rajdhani',sans-serif;min-height:100vh;}}
body::before{{content:'';position:fixed;inset:0;background-image:linear-gradient(rgba(128,128,128,.03) 1px,transparent 1px),linear-gradient(90deg,rgba(128,128,128,.03) 1px,transparent 1px);background-size:40px 40px;pointer-events:none;z-index:0;}}
.wrap{{position:relative;z-index:1;max-width:1100px;margin:0 auto;padding:20px;}}
nav{{display:flex;align-items:center;justify-content:space-between;padding:10px 20px;background:{nav_bg};border-bottom:1px solid var(--border);position:sticky;top:0;z-index:200;backdrop-filter:blur(12px);gap:8px;}}
.logo{{font-family:'Share Tech Mono',monospace;font-size:18px;color:var(--p);text-decoration:none;letter-spacing:2px;text-shadow:0 0 10px {p}66;}}
.nav-links{{display:flex;gap:4px;align-items:center;flex-wrap:wrap;}}
.nav-links a{{color:var(--text);text-decoration:none;font-size:12px;font-weight:600;padding:3px 7px;border-radius:5px;transition:all .2s;}}
.nav-links a:hover{{color:var(--p);background:rgba(128,128,128,.08);}}
.hamburger{{display:none;flex-direction:column;gap:5px;cursor:pointer;padding:4px;}}
.hamburger span{{display:block;width:22px;height:2px;background:var(--text);border-radius:2px;}}
.mob-menu{{display:none;flex-direction:column;gap:3px;padding:10px 20px;background:{nav_bg};border-bottom:1px solid var(--border);}}
.mob-menu a{{color:var(--text);text-decoration:none;font-size:13px;font-weight:600;padding:7px 10px;border-radius:6px;border:1px solid var(--border);}}
.mob-menu a:hover{{color:var(--p);border-color:var(--p);}}
@media(max-width:700px){{.nav-links{{display:none;}}.hamburger{{display:flex;}}.mob-menu.open{{display:flex;}}}}
#toast{{position:fixed;bottom:22px;right:18px;z-index:9999;padding:10px 18px;border-radius:9px;font-family:'Rajdhani',sans-serif;font-size:13px;font-weight:700;background:var(--card);border:1px solid var(--p);color:var(--p);box-shadow:0 4px 20px {p}33;transform:translateY(60px);opacity:0;transition:all .3s cubic-bezier(.4,0,.2,1);pointer-events:none;}}
#toast.show{{transform:translateY(0);opacity:1;}}
.btn{{padding:5px 12px;border-radius:6px;border:none;cursor:pointer;font-family:'Rajdhani',sans-serif;font-size:12px;font-weight:700;letter-spacing:1px;text-decoration:none;display:inline-block;transition:all .2s;}}
.btn-p{{background:var(--p);color:#000;}}.btn-p:hover{{box-shadow:0 0 14px {p}55;transform:translateY(-1px);}}
.btn-o{{background:transparent;border:1px solid var(--border);color:var(--text);}}.btn-o:hover{{border-color:var(--p);color:var(--p);}}
.btn-r{{background:rgba(255,45,85,.1);border:1px solid rgba(255,45,85,.3);color:var(--red);}}
.btn-g{{background:rgba(0,204,102,.1);border:1px solid rgba(0,204,102,.3);color:var(--green);}}
.btn-y{{background:rgba(230,184,0,.1);border:1px solid rgba(230,184,0,.3);color:var(--yellow);}}
.card{{background:var(--card);border:1px solid var(--border);border-radius:12px;padding:18px;margin-bottom:14px;position:relative;overflow:hidden;}}
.card::before{{content:'';position:absolute;top:0;left:0;right:0;height:1px;background:linear-gradient(90deg,transparent,var(--p),transparent);opacity:.4;}}
input,textarea,select{{width:100%;padding:8px 11px;background:{'rgba(0,0,0,.05)' if light else 'rgba(0,0,0,.4)'};border:1px solid var(--border);border-radius:7px;color:var(--text);font-family:'Rajdhani',sans-serif;font-size:13px;outline:none;transition:border .2s;}}
input:focus,textarea:focus,select:focus{{border-color:var(--p);}}
label{{display:block;font-size:10px;color:var(--dim);margin-bottom:4px;text-transform:uppercase;letter-spacing:1px;}}
.fg{{margin-bottom:12px;}}
.pi{{display:flex;justify-content:space-between;align-items:center;padding:9px 13px;background:{'rgba(0,0,0,.03)' if light else 'rgba(0,0,0,.2)'};border:1px solid var(--border);border-radius:8px;margin-bottom:5px;transition:all .2s;text-decoration:none;color:var(--text);}}
.pi:hover{{border-color:var(--p);transform:translateX(3px);}}.pi.pinned{{border-color:{p}55;}}
.pt{{font-size:13px;font-weight:700;color:var(--p);margin-bottom:1px;}}.pm{{font-size:9px;color:var(--dim);font-family:'Share Tech Mono',monospace;}}.pv{{font-family:'Share Tech Mono',monospace;color:var(--green);font-size:10px;white-space:nowrap;}}
.badge{{display:inline-flex;align-items:center;gap:3px;padding:2px 7px;border-radius:99px;font-size:9px;font-weight:700;letter-spacing:1px;}}
.tag{{display:inline-block;padding:2px 7px;border-radius:99px;font-size:9px;font-weight:700;background:rgba(128,128,128,.1);border:1px solid var(--border);color:var(--dim);margin:2px;cursor:pointer;transition:all .2s;text-decoration:none;}}
.tag:hover,.tag.active{{border-color:var(--p);color:var(--p);background:{p}11;}}
.code{{background:{code_bg};border:1px solid var(--border);border-radius:8px;padding:14px;overflow-x:auto;font-family:'Share Tech Mono',monospace;font-size:12px;line-height:1.8;white-space:pre-wrap;word-break:break-all;color:{code_col};max-height:560px;overflow-y:auto;}}
.sg{{display:grid;grid-template-columns:repeat(auto-fit,minmax(90px,1fr));gap:7px;margin-bottom:14px;}}
.sb{{background:{'rgba(0,0,0,.04)' if light else 'rgba(0,0,0,.3)'};border:1px solid var(--border);border-radius:8px;padding:9px;text-align:center;transition:transform .2s;}}.sb:hover{{transform:translateY(-2px);}}
.sn{{font-family:'Share Tech Mono',monospace;font-size:18px;font-weight:700;display:block;}}.sl{{font-size:9px;color:var(--dim);text-transform:uppercase;letter-spacing:1px;}}
.g2{{display:grid;grid-template-columns:1fr 1fr;gap:11px;}}.g3{{display:grid;grid-template-columns:repeat(3,1fr);gap:8px;}}.g4{{display:grid;grid-template-columns:repeat(4,1fr);gap:8px;}}
.alert{{padding:8px 12px;border-radius:7px;margin-bottom:10px;font-size:12px;}}
.ar{{background:rgba(255,45,85,.1);border:1px solid rgba(255,45,85,.3);color:var(--red);}}.ag{{background:rgba(0,204,102,.1);border:1px solid rgba(0,204,102,.3);color:var(--green);}}
.av{{width:52px;height:52px;border-radius:50%;background:rgba(128,128,128,.1);display:flex;align-items:center;justify-content:center;font-size:24px;border:2px solid var(--p);box-shadow:0 0 10px {p}33;}}
.lc-bar{{display:flex;justify-content:space-between;flex-wrap:wrap;gap:5px;padding:5px 9px;background:{'rgba(0,0,0,.04)' if light else 'rgba(0,0,0,.3)'};border:1px solid var(--border);border-radius:6px;margin-top:4px;font-family:'Share Tech Mono',monospace;font-size:10px;}}
.lc-num{{color:var(--p);font-weight:700;}}
.ad-bar{{background:rgba(230,184,0,.05);border:1px solid rgba(230,184,0,.2);border-radius:7px;padding:7px 12px;margin-bottom:10px;}}
.sb-wrap{{display:flex;gap:7px;margin-bottom:12px;}}.sb-wrap input{{flex:1;}}
.ao{{font-size:22px;cursor:pointer;padding:4px;border-radius:6px;border:2px solid transparent;transition:all .2s;display:inline-block;}}.ao:hover,.ao.sel{{border-color:var(--p);}}
.th-btn{{width:26px;height:26px;border-radius:50%;border:3px solid transparent;cursor:pointer;transition:all .2s;display:inline-block;}}.th-btn:hover,.th-btn.act{{border-color:{'#000' if light else '#fff'};transform:scale(1.2);}}
.at{{width:100%;border-collapse:collapse;font-size:11px;}}.at th{{padding:6px;text-align:left;color:var(--dim);border-bottom:1px solid var(--border);font-size:9px;letter-spacing:1px;text-transform:uppercase;}}.at td{{padding:6px;border-bottom:1px solid var(--border);}}
.comment{{padding:9px 12px;background:{'rgba(0,0,0,.03)' if light else 'rgba(0,0,0,.2)'};border:1px solid var(--border);border-radius:8px;margin-bottom:6px;}}
.notif{{padding:8px 12px;border-radius:7px;margin-bottom:4px;background:{'rgba(0,0,0,.03)' if light else 'rgba(0,0,0,.2)'};border:1px solid var(--border);font-size:11px;display:flex;justify-content:space-between;align-items:center;gap:7px;}}
.notif.unread{{border-color:{p}55;background:{p}08;}}
.notif-dot{{width:6px;height:6px;border-radius:50%;background:var(--p);flex-shrink:0;}}
.mode-btn{{background:{'rgba(0,0,0,.07)' if light else 'rgba(255,255,255,.07)'};border:1px solid var(--border);border-radius:14px;padding:3px 9px;cursor:pointer;font-size:12px;font-weight:700;color:var(--text);transition:all .2s;}}
.mode-btn:hover{{border-color:var(--p);}}
.notif-badge{{background:var(--red);color:#fff;border-radius:99px;font-size:9px;font-weight:700;padding:1px 5px;margin-left:2px;}}
.like-btn{{display:inline-flex;align-items:center;gap:4px;padding:4px 10px;border-radius:99px;border:1px solid var(--border);background:transparent;color:var(--text);cursor:pointer;font-size:11px;font-weight:700;font-family:'Rajdhani',sans-serif;transition:all .2s;}}
.like-btn:hover,.like-btn.active{{border-color:var(--p);color:var(--p);background:{p}11;}}
.like-btn.dislike:hover,.like-btn.dislike.active{{border-color:var(--red);color:var(--red);background:rgba(255,45,85,.1);}}
.follow-btn{{padding:5px 13px;border-radius:99px;border:1px solid var(--p);background:transparent;color:var(--p);cursor:pointer;font-size:11px;font-weight:700;font-family:'Rajdhani',sans-serif;transition:all .2s;}}
.follow-btn:hover,.follow-btn.following{{background:var(--p);color:#000;}}
.diff-add{{background:rgba(0,204,102,.15);border-left:3px solid var(--green);display:block;}}
.diff-del{{background:rgba(255,45,85,.15);border-left:3px solid var(--red);display:block;}}
.diff-eq{{display:block;color:var(--dim);}}
.ai-box{{background:{p}08;border:1px solid {p}33;border-radius:8px;padding:12px;margin-top:10px;font-size:12px;line-height:1.6;}}
.scan{{position:fixed;inset:0;pointer-events:none;z-index:999;background:repeating-linear-gradient(0deg,transparent,transparent 2px,rgba(0,0,0,{'0' if light else '.025'}) 2px,rgba(0,0,0,{'0' if light else '.025'}) 4px);}}
footer{{text-align:center;padding:16px;color:var(--dim);font-family:'Share Tech Mono',monospace;font-size:10px;letter-spacing:2px;border-top:1px solid var(--border);margin-top:18px;}}
.install-btn{{background:linear-gradient(135deg,{p},{p}aa);color:#000;border:none;padding:6px 14px;border-radius:99px;font-size:11px;font-weight:700;cursor:pointer;font-family:'Rajdhani',sans-serif;display:none;}}
@media(max-width:600px){{.g2,.g3,.g4{{grid-template-columns:1fr 1fr;}}}}
</style>"""

TOAST_JS='<div id="toast"></div><script>function toast(m,c){const t=document.getElementById("toast");t.textContent=m;t.style.borderColor=c||"var(--p)";t.style.color=c||"var(--p)";t.classList.add("show");setTimeout(()=>t.classList.remove("show"),2500);}</script>'
MOB_JS='<script>function toggleMenu(){document.getElementById("mm").classList.toggle("open");}</script>'

PWA_JS="""
<script>
// PWA Install
let deferredPrompt;
window.addEventListener('beforeinstallprompt',(e)=>{
  e.preventDefault(); deferredPrompt=e;
  const btn=document.getElementById('installBtn');
  if(btn){btn.style.display='inline-block';}
});
function installPWA(){
  if(deferredPrompt){deferredPrompt.prompt();deferredPrompt.userChoice.then(()=>{deferredPrompt=null;});}
}
// Service Worker
if('serviceWorker' in navigator){
  navigator.serviceWorker.register('/sw.js').catch(()=>{});
}
</script>
"""

def base(content,title="ZeroShell",theme='cyan'):
    light=session.get('light_mode',False)
    s=style(theme,light)
    msgs=get_flashed_messages(with_categories=True)
    alerts=''.join(f'<div class="alert {"ag" if c=="green" else "ar"}">{m}</div>' for c,m in msgs)
    try:
        db=get_db(); ad=db.execute("SELECT * FROM ads WHERE active=1 ORDER BY RANDOM() LIMIT 1").fetchone(); db.close()
    except: ad=None
    ad_html=f'<div class="ad-bar"><span style="color:var(--yellow);font-size:9px;font-weight:700;">📢</span><a href="{ad["url"] or "#"}" target="_blank" style="color:var(--yellow);text-decoration:none;font-size:11px;margin-left:7px;">{ad["title"]} — {ad["content"]}</a></div>' if ad else ''
    u=session.get('user',''); uid=session.get('user_id')
    uc=unread_count(uid)
    nb=f'<span class="notif-badge">{uc}</span>' if uc>0 else ''
    mi='☀️' if not light else '🌙'
    if u:
        adm='<a href="/admin">⚙️</a>' if session.get('is_admin') else ''
        nav_r=f'<a href="/notifications">🔔{nb}</a><a href="/feed">📊</a><a href="/profile/{u}">{session.get("avatar","👤")} {u}</a>{adm}<a href="/logout">Exit</a>'
        mob_r=f'<a href="/notifications">🔔 Notifs{nb}</a><a href="/feed">📊 Feed</a><a href="/profile/{u}">{session.get("avatar","👤")} {u}</a><a href="/settings">⚙️ Settings</a>{"<a href=/admin>👑 Admin</a>" if session.get("is_admin") else ""}<a href="/logout">🚪 Logout</a>'
    else:
        nav_r='<a href="/login">Login</a><a href="/register" class="btn btn-p">Register</a>'
        mob_r='<a href="/login">Login</a><a href="/register">Register</a>'
    p_color=THEMES.get(theme,'#00f5ff')
    tg_help='https://t.me/ZeroShell_help'
    return f'''<!DOCTYPE html><html lang="en"><head>
<meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<meta name="theme-color" content="{p_color}">
<meta name="apple-mobile-web-app-capable" content="yes">
<meta name="apple-mobile-web-app-title" content="ZeroShell">
<link rel="manifest" href="/manifest.json">
<title>{title} - ZeroShell</title>{s}</head><body>
{TOAST_JS}{MOB_JS}{PWA_JS}
<nav>
  <div class="nav-inner">
    <a class="logo" href="/">⚡ ZeroShell</a>
    <div class="nav-links">
      <a href="/">Home</a>
      <a href="/new">+ New Paste</a>
      <a href="/search">🔍 Search</a>
      <a href="/leaderboard">🏆</a>
      <a href="/diff">🔀 Diff</a>
      <a href="/preview">👁 Preview</a>
      <a href="/api/v1/docs">🌐 API</a>
    </div>
    <div class="nav-right">
      {nav_r}
      <button class="install-btn" id="installBtn" onclick="installPWA()">📱</button>
      <a href="/toggle-mode" class="mode-btn">{mi}</a>
    </div>
    <div class="hamburger" onclick="toggleMenu()"><span></span><span></span><span></span></div>
  </div>
</nav>
<div class="mob-menu" id="mm">
  <a href="/">🏠 Home</a><a href="/new">📝 New Paste</a>
  <a href="/search">🔍 Search</a><a href="/leaderboard">🏆 Leaderboard</a>
  <a href="/diff">🔀 Diff Tool</a><a href="/preview">👁 Live Preview</a>
  <a href="/api/v1/docs">🌐 API Docs</a>
  {mob_r}
  <a href="{tg_help}" target="_blank">✈️ Telegram Help</a>
  <a href="/toggle-mode">{mi} Toggle Mode</a>
</div>
<div class="wrap">{alerts}{ad_html}{content}</div>
<!-- Telegram Helpline -->
<div class="helpline">
  <a href="{tg_help}" target="_blank" class="help-btn help-tg" title="Telegram Support">
    <svg width="16" height="16" viewBox="0 0 24 24" fill="white"><path d="M12 0C5.373 0 0 5.373 0 12s5.373 12 12 12 12-5.373 12-12S18.627 0 12 0zm5.894 8.221l-1.97 9.28c-.145.658-.537.818-1.084.508l-3-2.21-1.447 1.394c-.16.16-.295.295-.605.295l.213-3.053 5.56-5.023c.242-.213-.054-.333-.373-.12l-6.871 4.326-2.962-.924c-.643-.204-.657-.643.136-.953l11.57-4.461c.537-.194 1.006.131.833.941z"/></svg>
    Help
  </a>
</div>
<footer>
  <div style="display:flex;justify-content:center;align-items:center;gap:16px;flex-wrap:wrap;">
    <a href="/" style="color:var(--sub);text-decoration:none;font-size:12px;">⚡ ZeroShell v7.5</a>
    <a href="{tg_help}" target="_blank" style="color:var(--p);text-decoration:none;font-size:12px;">✈️ Telegram</a>
    <a href="/api/v1/docs" style="color:var(--sub);text-decoration:none;font-size:12px;">🌐 API</a>
    <a href="/leaderboard" style="color:var(--sub);text-decoration:none;font-size:12px;">🏆 Leaderboard</a>
  </div>
</footer>
</body></html>'''

# ━━━ PWA Manifest & SW ━━━
@app.route('/manifest.json')
def manifest():
    m={"name":"ZeroShell","short_name":"ZeroShell","description":"Paste sharing platform","start_url":"/","display":"standalone","background_color":"#04080f","theme_color":"#00f5ff","icons":[{"src":"data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 100 100'%3E%3Ctext y='.9em' font-size='90'%3E⚡%3C/text%3E%3C/svg%3E","sizes":"any","type":"image/svg+xml"}]}
    return Response(json.dumps(m),mimetype='application/json')

@app.route('/sw.js')
def sw():
    sw_code="""
const CACHE='zeroshell-v7';
const OFFLINE=['/'];
self.addEventListener('install',e=>{
  e.waitUntil(caches.open(CACHE).then(c=>c.addAll(OFFLINE)));
  self.skipWaiting();
});
self.addEventListener('activate',e=>{
  e.waitUntil(caches.keys().then(keys=>Promise.all(keys.filter(k=>k!==CACHE).map(k=>caches.delete(k)))));
  self.clients.claim();
});
self.addEventListener('fetch',e=>{
  if(e.request.method!=='GET') return;
  e.respondWith(fetch(e.request).catch(()=>caches.match(e.request).then(r=>r||caches.match('/'))));
});
"""
    return Response(sw_code,mimetype='application/javascript')

# ━━━ PUBLIC API ━━━
def api_auth():
    key=request.headers.get('X-API-Key') or request.args.get('api_key','')
    if not key: return None
    db=get_db(); user=db.execute("SELECT * FROM users WHERE api_key=?",(key,)).fetchone(); db.close()
    return user

@app.route('/api/v1/docs')
def api_docs():
    base_url="https://zeroshell-paste.up.railway.app"
    c=f'''<div style="max-width:800px;margin:0 auto;">
<div style="font-size:18px;font-weight:700;color:var(--p);letter-spacing:2px;margin-bottom:20px;">🌐 ZeroShell Public API v1</div>
<div class="card">
<div style="font-size:13px;font-weight:700;color:var(--yellow);margin-bottom:10px;">Authentication</div>
<p style="font-size:12px;color:var(--dim);margin-bottom:8px;">Get your API key from Settings page. Pass as header or query param:</p>
<div class="code">X-API-Key: your_api_key_here
# or
{base_url}/api/v1/pastes?api_key=your_key</div></div>
<div class="card">
<div style="font-size:13px;font-weight:700;color:var(--green);margin-bottom:12px;">Endpoints</div>
<div style="margin-bottom:14px;"><span style="background:rgba(0,204,102,.15);border:1px solid var(--green);border-radius:4px;padding:2px 7px;font-family:'Share Tech Mono',monospace;font-size:11px;color:var(--green);">GET</span> <code style="font-family:'Share Tech Mono',monospace;font-size:12px;color:var(--p);margin-left:8px;">/api/v1/pastes</code>
<p style="font-size:11px;color:var(--dim);margin-top:4px;">List your pastes. Params: page, limit</p></div>
<div style="margin-bottom:14px;"><span style="background:rgba(0,204,102,.15);border:1px solid var(--green);border-radius:4px;padding:2px 7px;font-family:'Share Tech Mono',monospace;font-size:11px;color:var(--green);">GET</span> <code style="font-family:'Share Tech Mono',monospace;font-size:12px;color:var(--p);margin-left:8px;">/api/v1/paste/&lt;slug&gt;</code>
<p style="font-size:11px;color:var(--dim);margin-top:4px;">Get a specific paste by slug</p></div>
<div style="margin-bottom:14px;"><span style="background:rgba(41,121,255,.15);border:1px solid #2979ff;border-radius:4px;padding:2px 7px;font-family:'Share Tech Mono',monospace;font-size:11px;color:#2979ff;">POST</span> <code style="font-family:'Share Tech Mono',monospace;font-size:12px;color:var(--p);margin-left:8px;">/api/v1/paste</code>
<p style="font-size:11px;color:var(--dim);margin-top:4px;">Create a new paste. Body: title, content, syntax, visibility, tags</p></div>
<div style="margin-bottom:14px;"><span style="background:rgba(255,45,85,.15);border:1px solid var(--red);border-radius:4px;padding:2px 7px;font-family:'Share Tech Mono',monospace;font-size:11px;color:var(--red);">DELETE</span> <code style="font-family:'Share Tech Mono',monospace;font-size:12px;color:var(--p);margin-left:8px;">/api/v1/paste/&lt;slug&gt;</code>
<p style="font-size:11px;color:var(--dim);margin-top:4px;">Delete a paste (owner only)</p></div>
<div><span style="background:rgba(0,204,102,.15);border:1px solid var(--green);border-radius:4px;padding:2px 7px;font-family:'Share Tech Mono',monospace;font-size:11px;color:var(--green);">GET</span> <code style="font-family:'Share Tech Mono',monospace;font-size:12px;color:var(--p);margin-left:8px;">/api/v1/me</code>
<p style="font-size:11px;color:var(--dim);margin-top:4px;">Get your profile info</p></div>
</div>
<div class="card">
<div style="font-size:12px;font-weight:700;color:var(--p);margin-bottom:8px;">Example Response</div>
<div class="code">{{"slug":"abc12345","title":"My Paste","syntax":"python","views":42,"created_at":"2025-01-01T00:00:00"}}</div>
</div></div>'''
    return base(c,"API Docs",session.get('theme','cyan'))

@app.route('/api/v1/me')
def api_me():
    user=api_auth()
    if not user: return jsonify({'error':'Unauthorized'}),401
    return jsonify({'id':user['id'],'username':user['username'],'email':user['email'],'total_views':user['total_views'],'created_at':user['created_at']})

@app.route('/api/v1/pastes')
def api_pastes():
    user=api_auth()
    if not user: return jsonify({'error':'Unauthorized'}),401
    page=max(1,int(request.args.get('page',1)))
    limit=min(50,int(request.args.get('limit',20)))
    offset=(page-1)*limit
    db=get_db()
    pastes=db.execute("SELECT slug,title,syntax,visibility,views,likes,created_at,tags FROM pastes WHERE user_id=? ORDER BY created_at DESC LIMIT ? OFFSET ?",(user['id'],limit,offset)).fetchall()
    total=db.execute("SELECT COUNT(*) FROM pastes WHERE user_id=?",(user['id'],)).fetchone()[0]
    db.close()
    return jsonify({'pastes':[dict(p) for p in pastes],'total':total,'page':page,'limit':limit})

@app.route('/api/v1/paste/<slug>')
def api_get_paste(slug):
    user=api_auth()
    db=get_db(); paste=db.execute("SELECT * FROM pastes WHERE slug=?",(slug,)).fetchone(); db.close()
    if not paste: return jsonify({'error':'Not found'}),404
    if paste['visibility']=='private' and (not user or user['id']!=paste['user_id']): return jsonify({'error':'Forbidden'}),403
    return jsonify({'slug':paste['slug'],'title':paste['title'],'content':paste['content'],'syntax':paste['syntax'],'visibility':paste['visibility'],'views':paste['views'],'likes':paste['likes'],'tags':paste['tags'],'created_at':paste['created_at']})

@app.route('/api/v1/paste',methods=['POST'])
def api_create_paste():
    user=api_auth()
    if not user: return jsonify({'error':'Unauthorized'}),401
    data=request.get_json() or {}
    title=str(data.get('title','')).strip()[:200]
    content=str(data.get('content','')).strip()
    syntax=str(data.get('syntax','text'))
    visibility=str(data.get('visibility','public'))
    tags=str(data.get('tags',''))[:200]
    if not title or not content: return jsonify({'error':'title and content required'}),400
    if visibility not in ('public','private'): visibility='public'
    slug=rand_slug()
    db=get_db(); db.execute("INSERT INTO pastes(slug,title,content,syntax,visibility,tags,user_id) VALUES(?,?,?,?,?,?,?)",(slug,title,content,syntax,visibility,tags,user['id'])); db.commit(); db.close()
    return jsonify({'slug':slug,'url':f'/paste/{slug}'}),201

@app.route('/api/v1/paste/<slug>',methods=['DELETE'])
def api_delete_paste(slug):
    user=api_auth()
    if not user: return jsonify({'error':'Unauthorized'}),401
    db=get_db(); p=db.execute("SELECT * FROM pastes WHERE slug=?",(slug,)).fetchone()
    if not p: db.close(); return jsonify({'error':'Not found'}),404
    if p['user_id']!=user['id']: db.close(); return jsonify({'error':'Forbidden'}),403
    db.execute("DELETE FROM pastes WHERE slug=?",(slug,)); db.commit(); db.close()
    return jsonify({'deleted':True})

# ━━━ TOGGLE MODE ━━━
@app.route('/toggle-mode')
def toggle_mode():
    session['light_mode']=not session.get('light_mode',False)
    return redirect(request.referrer or '/')

# ━━━ AI SUMMARY ━━━
@app.route('/ai-summary/<slug>',methods=['POST'])
def ai_summary(slug):
    if not session.get('user_id'): return jsonify({'error':'Login required'}),401
    db=get_db(); paste=db.execute("SELECT * FROM pastes WHERE slug=?",(slug,)).fetchone()
    if not paste: db.close(); return jsonify({'error':'Not found'}),404
    existing=paste['ai_summary']
    if existing: db.close(); return jsonify({'summary':existing})
    content_preview=paste['content'][:2000]
    api_key=os.environ.get('ANTHROPIC_API_KEY','')
    if not api_key: db.close(); return jsonify({'summary':f'📊 Paste Info: {len(paste["content"].split(chr(10)))} lines · {len(paste["content"])} chars · Language: {paste["syntax"]} · Created: {paste["created_at"][:10]}'})
    import urllib.request, urllib.error
    try:
        payload=json.dumps({"model":"claude-haiku-4-5-20251001","max_tokens":150,"messages":[{"role":"user","content":f"Summarize this {paste['syntax']} code/text in 2 sentences max. Be concise:\n\n{content_preview}"}]}).encode()
        req=urllib.request.Request("https://api.anthropic.com/v1/messages",data=payload,headers={"Content-Type":"application/json","x-api-key":api_key,"anthropic-version":"2023-06-01"},method='POST')
        with urllib.request.urlopen(req,timeout=10) as r:
            resp=json.loads(r.read())
            summary=resp['content'][0]['text']
            db.execute("UPDATE pastes SET ai_summary=? WHERE slug=?",(summary,slug))
            db.commit()
    except Exception as e:
        summary=f"⚡ {paste['syntax'].upper()} · {len(paste['content'].split(chr(10)))} lines · {len(paste['content'])} chars"
    db.close()
    return jsonify({'summary':summary})

# ━━━ DIFF TOOL ━━━
@app.route('/diff',methods=['GET','POST'])
def diff_tool():
    result=''
    a_text=request.form.get('a','')
    b_text=request.form.get('b','')
    slug_a=request.args.get('a','')
    slug_b=request.args.get('b','')
    if slug_a and slug_b and request.method=='GET':
        db=get_db()
        pa=db.execute("SELECT * FROM pastes WHERE slug=?",(slug_a,)).fetchone()
        pb=db.execute("SELECT * FROM pastes WHERE slug=?",(slug_b,)).fetchone()
        db.close()
        if pa: a_text=pa['content']
        if pb: b_text=pb['content']
    if (a_text or b_text) and request.method=='POST':
        al=a_text.splitlines(); bl=b_text.splitlines()
        adds=dels=same=0
        html_lines=[]
        i,j=0,0
        # simple LCS-based diff
        import difflib
        matcher=difflib.SequenceMatcher(None,al,bl)
        for op,i1,i2,j1,j2 in matcher.get_opcodes():
            if op=='equal':
                for l in al[i1:i2]: html_lines.append(f'<span class="diff-eq">  {__import__("html").escape(l) or " "}</span>'); same+=1
            elif op=='replace':
                for l in al[i1:i2]: html_lines.append(f'<span class="diff-del">- {__import__("html").escape(l) or " "}</span>'); dels+=1
                for l in bl[j1:j2]: html_lines.append(f'<span class="diff-add">+ {__import__("html").escape(l) or " "}</span>'); adds+=1
            elif op=='delete':
                for l in al[i1:i2]: html_lines.append(f'<span class="diff-del">- {__import__("html").escape(l) or " "}</span>'); dels+=1
            elif op=='insert':
                for l in bl[j1:j2]: html_lines.append(f'<span class="diff-add">+ {__import__("html").escape(l) or " "}</span>'); adds+=1
        result=f'<div style="display:flex;gap:12px;margin-bottom:10px;flex-wrap:wrap;"><span style="color:var(--green);font-size:12px;font-weight:700;">+{adds} added</span><span style="color:var(--red);font-size:12px;font-weight:700;">-{dels} removed</span><span style="color:var(--dim);font-size:12px;">={same} same</span></div><div class="code" style="font-size:11px;">{"".join(html_lines) or "No differences found!"}</div>'
    import html as html_mod
    c=f'''<div style="max-width:900px;margin:0 auto;">
<div style="font-size:16px;font-weight:700;color:var(--p);letter-spacing:2px;margin-bottom:16px;">🔀 DIFF / COMPARE</div>
<div class="card">
<form method="POST">
<div class="g2">
<div class="fg"><label>Text A (Original)</label><textarea name="a" rows="10" style="font-family:'Share Tech Mono',monospace;font-size:11px;resize:vertical;" placeholder="Paste original text here...">{html_mod.escape(a_text)}</textarea></div>
<div class="fg"><label>Text B (Modified)</label><textarea name="b" rows="10" style="font-family:'Share Tech Mono',monospace;font-size:11px;resize:vertical;" placeholder="Paste modified text here...">{html_mod.escape(b_text)}</textarea></div>
</div>
<button type="submit" class="btn btn-p" style="width:100%;padding:10px;font-size:13px;">🔀 Compare</button>
</form></div>
{f'<div class="card"><div style="font-size:12px;font-weight:700;color:var(--p);letter-spacing:2px;margin-bottom:10px;">📊 RESULT</div>{result}</div>' if result else ''}
</div>'''
    return base(c,"Diff Tool",session.get('theme','cyan'))

# ━━━ HOME ━━━
@app.route('/')
def home():
    cleanup_expired()
    tag=request.args.get('tag','')
    db=get_db()
    if tag:
        pastes=db.execute("SELECT p.*,u.username,u.avatar FROM pastes p LEFT JOIN users u ON p.user_id=u.id WHERE p.visibility='public' AND p.tags LIKE ? ORDER BY p.pinned DESC,p.created_at DESC LIMIT 20",(f'%{tag}%',)).fetchall()
    else:
        pastes=db.execute("SELECT p.*,u.username,u.avatar FROM pastes p LEFT JOIN users u ON p.user_id=u.id WHERE p.visibility='public' ORDER BY p.pinned DESC,p.created_at DESC LIMIT 20").fetchall()
    tp=db.execute("SELECT COUNT(*) FROM pastes").fetchone()[0]
    tv=db.execute("SELECT COALESCE(SUM(views),0) FROM pastes").fetchone()[0]
    tu=db.execute("SELECT COUNT(*) FROM users").fetchone()[0]
    db.close()
    def exp_tag(p):
        if not p['expires_at']: return ''
        try:
            d=datetime.fromisoformat(str(p['expires_at']))-datetime.now(); h=int(d.total_seconds()//3600)
            return f'<span style="color:var(--yellow);font-size:9px;"> ⏰{h}h</span>' if h>=0 else ''
        except: return ''
    pl=''.join(f'<a href="/paste/{p["slug"]}" class="pi {"pinned" if p["pinned"] else ""}"><div><div class="pt">{"📌 " if p["pinned"] else ""}{"🔒 " if p["password"] else ""}{p["title"]}{exp_tag(p)}</div><div class="pm">{p["avatar"] or "👤"} {p["username"] or "Anon"} · {p["created_at"][:10]} · {p["syntax"]}{" · ❤️"+str(p["likes"]) if p["likes"]>0 else ""}</div></div><div class="pv">👁 {p["views"]}</div></a>' for p in pastes if not is_expired(p)) or '<div style="text-align:center;color:var(--dim);padding:20px;">No pastes yet!</div>'
    tag_links=''.join(f'<a href="/?tag={t}" class="tag {"active" if tag==t else ""}">{t}</a>' for t in ALL_TAGS)

    # Build sidebar
    try:
        db2=get_db()
        top_users=db2.execute("SELECT username,avatar,total_views FROM users ORDER BY total_views DESC LIMIT 5").fetchall()
        hot_pastes=db2.execute("SELECT slug,title,views,likes FROM pastes WHERE visibility='public' ORDER BY views DESC LIMIT 5").fetchall()
        db2.close()
    except: top_users=[]; hot_pastes=[]
    top_u_html=''.join('<div style="display:flex;align-items:center;gap:8px;padding:5px 0;border-bottom:1px solid var(--border);"><span style="font-size:16px;">'+str(u["avatar"] or "👤")+'</span><a href="/profile/'+u["username"]+'" style="color:var(--p);text-decoration:none;font-size:12px;font-weight:600;flex:1;">'+u["username"]+'</a><span style="font-family:monospace;color:var(--green);font-size:11px;">👁'+str(u["total_views"])+'</span></div>' for u in top_users)
    hot_p_html=''.join('<div style="padding:5px 0;border-bottom:1px solid var(--border);"><a href="/paste/'+p["slug"]+'" style="color:var(--p);text-decoration:none;font-size:12px;font-weight:600;display:block;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;">'+p["title"]+'</a><span style="font-size:10px;color:var(--sub);">👁'+str(p["views"])+' · ❤️'+str(p["likes"])+'</span></div>' for p in hot_pastes)
    sidebar=f'''<div class="sidebar">
<div class="side-card">
<div class="side-title">🔥 Hot Pastes</div>
{hot_p_html or "<p style=color:var(--sub);font-size:12px;>No pastes yet</p>"}
</div>
<div class="side-card">
<div class="side-title">🏆 Top Users</div>
{top_u_html or "<p style=color:var(--sub);font-size:12px;>No users yet</p>"}
<a href="/leaderboard" style="display:block;text-align:center;margin-top:8px;color:var(--p);font-size:11px;font-weight:600;text-decoration:none;">See all →</a>
</div>
<div class="side-card">
<div class="side-title">⚡ Quick Actions</div>
<div style="display:flex;flex-direction:column;gap:5px;">
<a href="/new" class="qa-btn" style="justify-content:center;">📝 New Paste</a>
<a href="/preview" class="qa-btn" style="justify-content:center;">👁 Live Preview</a>
<a href="/diff" class="qa-btn" style="justify-content:center;">🔀 Diff Tool</a>
<a href="/feed" class="qa-btn" style="justify-content:center;">📊 Activity Feed</a>
{f'<a href="/bookmarks" class="qa-btn" style="justify-content:center;">🔖 Bookmarks</a>' if session.get("user") else ""}
</div>
</div>
<div class="side-card">
<div class="side-title">🌐 Stats</div>
<div style="display:grid;grid-template-columns:1fr 1fr;gap:8px;">
<div class="sb"><span class="sn" style="color:var(--p);font-size:16px;">{tp}</span><span class="sl">Pastes</span></div>
<div class="sb"><span class="sn" style="color:var(--green);font-size:16px;">{tv}</span><span class="sl">Views</span></div>
<div class="sb"><span class="sn" style="color:var(--yellow);font-size:16px;">{tu}</span><span class="sl">Users</span></div>
<div class="sb"><span class="sn" style="color:var(--sub);font-size:11px;">v7.5</span><span class="sl">Version</span></div>
</div>
</div>
</div>'''

    qa_tag_links=''.join(f'<a href="/?tag={t}" class="qa-btn {"active" if tag==t else ""}">'+t+'</a>' for t in ALL_TAGS)
    c=f'''<div style="text-align:center;padding:28px 20px 16px;">
<div style="font-family:'Share Tech Mono',monospace;font-size:clamp(22px,4vw,40px);color:var(--p);text-shadow:0 0 22px var(--p)55;letter-spacing:4px;margin-bottom:6px;">⚡ ZEROSHELL</div>
<div style="color:var(--sub);font-size:11px;letter-spacing:3px;margin-bottom:16px;">PASTE · SHARE · TRACK · v7.5</div>
<a href="/new" class="btn btn-p" style="font-size:14px;padding:9px 28px;">+ New Paste</a>
</div>
<!-- Quick actions bar -->
<div class="qa-bar">
<span style="font-size:10px;font-weight:700;color:var(--sub);letter-spacing:.8px;text-transform:uppercase;margin-right:4px;">Browse:</span>
{qa_tag_links}
</div>
<!-- Main layout with sidebar -->
<div class="main-layout">
<div>
<div style="font-size:12px;font-weight:700;color:var(--sub);letter-spacing:.8px;text-transform:uppercase;margin-bottom:10px;">🕐 Recent Pastes{f" · #{tag}" if tag else ""}</div>
{pl}
</div>
{sidebar}
</div>'''
    return base(c,"Home",session.get('theme','cyan'))

# ━━━ SEARCH ━━━
@app.route('/search')
def search():
    q=request.args.get('q','').strip()
    res=[]
    if q:
        db=get_db(); res=db.execute("SELECT p.*,u.username,u.avatar FROM pastes p LEFT JOIN users u ON p.user_id=u.id WHERE p.visibility='public' AND (p.title LIKE ? OR p.content LIKE ? OR p.tags LIKE ?) ORDER BY p.created_at DESC LIMIT 30",(f'%{q}%',f'%{q}%',f'%{q}%')).fetchall(); db.close()
    rl=''.join(f'<a href="/paste/{p["slug"]}" class="pi"><div><div class="pt">{p["title"]}</div><div class="pm">{p["avatar"] or "👤"} {p["username"] or "Anon"} · {p["created_at"][:10]}</div></div><div class="pv">👁 {p["views"]}</div></a>' for p in res if not is_expired(p)) or (f'<div style="text-align:center;color:var(--dim);padding:14px;">No results for "{q}"</div>' if q else '')
    c=f'<div class="card"><div style="font-size:13px;font-weight:700;color:var(--p);letter-spacing:2px;margin-bottom:10px;">🔍 SEARCH</div><form method="GET"><div class="sb-wrap"><input name="q" value="{q}" placeholder="Search..." autofocus><button type="submit" class="btn btn-p">Go</button></div></form>{rl}</div>'
    return base(c,"Search",session.get('theme','cyan'))

# ━━━ TAGS / LEADERBOARD / FEED ━━━
@app.route('/tags')
def tags():
    db=get_db()
    tc={t:db.execute("SELECT COUNT(*) FROM pastes WHERE visibility='public' AND tags LIKE ?",(f'%{t}%',)).fetchone()[0] for t in ALL_TAGS}
    db.close()
    rows=''.join(f'<a href="/?tag={t}" class="pi"><div><div class="pt">#{t}</div></div><div class="pv">{tc[t]} pastes</div></a>' for t in ALL_TAGS)
    c=f'<div style="max-width:600px;margin:0 auto;"><div style="font-size:15px;font-weight:700;color:var(--p);letter-spacing:2px;margin-bottom:14px;">🏷️ TAGS</div><div class="card">{rows}</div></div>'
    return base(c,"Tags",session.get('theme','cyan'))

@app.route('/leaderboard')
def leaderboard():
    db=get_db()
    users=db.execute("SELECT u.*,COUNT(p.id) as pc FROM users u LEFT JOIN pastes p ON u.id=p.user_id GROUP BY u.id ORDER BY u.total_views DESC LIMIT 20").fetchall()
    db.close()
    rows=''.join(f'<div style="display:flex;align-items:center;gap:12px;padding:9px 13px;background:rgba(0,0,0,.15);border:1px solid var(--border);border-radius:8px;margin-bottom:6px;"><div style="font-family:\'Share Tech Mono\',monospace;font-size:15px;font-weight:700;width:28px;text-align:center;">{["🥇","🥈","🥉"][i] if i<3 else "#"+str(i+1)}</div><div style="font-size:20px;">{u["avatar"] or "👤"}</div><div style="flex:1;"><a href="/profile/{u["username"]}" style="color:var(--p);text-decoration:none;font-size:12px;font-weight:700;">{u["username"]}</a><div style="font-size:9px;color:var(--dim);">{u["pc"]} pastes</div></div><div style="font-family:\'Share Tech Mono\',monospace;color:var(--green);font-size:12px;font-weight:700;">👁 {u["total_views"]}</div></div>' for i,u in enumerate(users)) or '<div style="text-align:center;color:var(--dim);padding:16px;">No users!</div>'
    c=f'<div style="max-width:600px;margin:0 auto;"><div style="font-size:16px;font-weight:700;color:var(--p);letter-spacing:2px;margin-bottom:14px;text-align:center;">🏆 LEADERBOARD</div><div class="card">{rows}</div></div>'
    return base(c,"Leaderboard",session.get('theme','cyan'))

@app.route('/feed')
def feed():
    db=get_db()
    acts=db.execute("SELECT a.*,u.username,u.avatar FROM activity a LEFT JOIN users u ON a.user_id=u.id ORDER BY a.created_at DESC LIMIT 40").fetchall()
    db.close()
    icons={'paste':'📝','like':'❤️','comment':'💬','follow':'👥','fork':'🔎'}
    rows=''.join(f'<div style="display:flex;align-items:flex-start;gap:9px;padding:8px 12px;border-left:2px solid var(--border);margin-bottom:7px;"><div style="font-size:16px;">{icons.get(a["target_type"],"⚡")}</div><div style="flex:1;"><div style="font-size:11px;font-weight:700;"><a href="/profile/{a["username"]}" style="color:var(--p);text-decoration:none;">{a["avatar"] or "👤"} {a["username"]}</a> {a["action"]}</div><div style="font-size:9px;color:var(--dim);font-family:\'Share Tech Mono\',monospace;margin-top:1px;">{a["created_at"][:16]}</div></div></div>' for a in acts) or '<div style="text-align:center;color:var(--dim);padding:16px;">No activity!</div>'
    c=f'<div style="max-width:640px;margin:0 auto;"><div style="font-size:15px;font-weight:700;color:var(--p);letter-spacing:2px;margin-bottom:14px;">📊 ACTIVITY FEED</div><div class="card">{rows}</div></div>'
    return base(c,"Feed",session.get('theme','cyan'))

# ━━━ NEW PASTE ━━━
@app.route('/new',methods=['GET','POST'])
def new_paste():
    fork_slug=request.args.get('fork',''); fork_data={}
    if fork_slug:
        db=get_db(); fp=db.execute("SELECT * FROM pastes WHERE slug=?",(fork_slug,)).fetchone(); db.close()
        if fp: fork_data={'title':f"Fork of {fp['title']}",'content':fp['content'],'syntax':fp['syntax']}
    if request.method=='POST':
        title=request.form.get('title','').strip(); content=request.form.get('content','').strip()
        syntax=request.form.get('syntax','text'); vis=request.form.get('visibility','public')
        pw=request.form.get('paste_pw','').strip(); exp=request.form.get('expire','')
        tags=','.join(t.strip() for t in request.form.getlist('tags') if t.strip())
        expires_at=None
        if exp=='1h': expires_at=(datetime.now()+timedelta(hours=1)).isoformat()
        elif exp=='1d': expires_at=(datetime.now()+timedelta(days=1)).isoformat()
        elif exp=='1w': expires_at=(datetime.now()+timedelta(weeks=1)).isoformat()
        elif exp=='1m': expires_at=(datetime.now()+timedelta(days=30)).isoformat()
        if not title or not content: flash('Fill all fields!','red')
        else:
            slug=rand_slug(); db=get_db()
            db.execute("INSERT INTO pastes(slug,title,content,syntax,visibility,password,tags,user_id,expires_at) VALUES(?,?,?,?,?,?,?,?,?)",(slug,title,content,syntax,vis,hash_pw(pw) if pw else '',tags,session.get('user_id'),expires_at))
            db.commit()
            if session.get('user_id'):
                pid=db.execute("SELECT id FROM pastes WHERE slug=?",(slug,)).fetchone()[0]
                log_activity(session['user_id'],f'created "{title}"',pid,'paste')
                fols=db.execute("SELECT follower_id FROM follows WHERE following_id=?",(session['user_id'],)).fetchall()
                for f in fols: send_notif(f['follower_id'],f'📝 {session["user"]} created "{title}"',f'/paste/{slug}')
            db.close(); return redirect(f'/paste/{slug}')
    exp_opts=''.join(f'<option value="{v}">{l}</option>' for v,l in EXPIRE_OPTS)
    tag_checks=''.join(f'<label style="display:inline-flex;align-items:center;gap:4px;margin:3px;cursor:pointer;font-size:11px;text-transform:none;letter-spacing:0;"><input type="checkbox" name="tags" value="{t}" style="width:auto;"> #{t}</label>' for t in ALL_TAGS)
    c=f'''<div style="max-width:800px;margin:0 auto;"><div class="card">
<div style="font-size:14px;font-weight:700;color:var(--p);letter-spacing:2px;margin-bottom:13px;">📝 {"FORK" if fork_data else "NEW PASTE"}</div>
<form method="POST">
<div class="fg"><label>Title</label><input name="title" value="{fork_data.get('title','')}" placeholder="Paste title..." required></div>
<div class="fg"><label>Content</label>
<textarea name="content" id="pc" rows="11" required style="font-family:'Share Tech Mono',monospace;font-size:12px;resize:vertical;" oninput="lc(this)">{fork_data.get('content','')}</textarea>
<div class="lc-bar"><span>📄 <span class="lc-num" id="ll">0</span> lines</span><span>📝 <span class="lc-num" id="lc2">0</span> chars</span><span>📦 <span class="lc-num" id="lw">0</span> words</span><span>💾 <span class="lc-num" id="ls">0 B</span></span></div></div>
<div class="g2">
<div class="fg"><label>Syntax</label><select name="syntax"><option value="text">Plain Text</option><option value="python">Python</option><option value="javascript">JavaScript</option><option value="html">HTML</option><option value="css">CSS</option><option value="bash">Bash</option><option value="json">JSON</option><option value="sql">SQL</option></select></div>
<div class="fg"><label>Visibility</label><select name="visibility"><option value="public">🌐 Public</option><option value="private">🔒 Private</option></select></div></div>
<div class="g2">
<div class="fg"><label>🔒 Password</label><input name="paste_pw" type="password" placeholder="Optional..."></div>
<div class="fg"><label>⏰ Expires</label><select name="expire">{exp_opts}</select></div></div>
<div class="fg"><label>🏷️ Tags</label><div style="margin-top:4px;">{tag_checks}</div></div>
<button type="submit" class="btn btn-p" style="width:100%;font-size:13px;padding:10px;">🚀 Create</button>
</form></div></div>
<script>function lc(el){{const v=el.value,l=v?v.split('\\n').length:0,c=v.length,w=v.trim()?v.trim().split(/\\s+/).length:0,sz=new Blob([v]).size,ss=sz>1024?(sz/1024).toFixed(1)+' KB':sz+' B';document.getElementById('ll').textContent=l;document.getElementById('lc2').textContent=c;document.getElementById('lw').textContent=w;document.getElementById('ls').textContent=ss;}}
{f"window.onload=()=>lc(document.getElementById('pc'));" if fork_data else ""}</script>'''
    return base(c,"New",session.get('theme','cyan'))

# ━━━ VIEW PASTE ━━━
@app.route('/paste/<slug>',methods=['GET','POST'])
def view_paste(slug):
    db=get_db(); paste=db.execute("SELECT * FROM pastes WHERE slug=?",(slug,)).fetchone()
    if not paste: db.close(); return base('<div class="card" style="text-align:center;padding:36px;"><div style="font-size:36px;">🔍</div><p style="color:var(--dim);margin-top:7px;">Not found!</p></div>',"404")
    if is_expired(paste): db.close(); return base('<div class="card" style="text-align:center;padding:36px;"><div style="font-size:36px;">⌛</div><p style="color:var(--dim);margin-top:7px;">Expired!</p></div>',"Expired")
    if paste['password']:
        entered=session.get(f'pw_{slug}','')
        if request.method=='POST' and request.form.get('paste_pw'):
            if hash_pw(request.form.get('paste_pw',''))==paste['password']: session[f'pw_{slug}']=paste['password']; return redirect(f'/paste/{slug}')
            else: db.close(); return base(f'<div style="max-width:360px;margin:44px auto;"><div class="card"><div style="text-align:center;font-size:30px;margin-bottom:8px;">🔒</div><div style="text-align:center;font-size:14px;font-weight:700;color:var(--p);margin-bottom:12px;">{paste["title"]}</div><div class="alert ar">Wrong password!</div><form method="POST"><div class="fg"><label>Password</label><input name="paste_pw" type="password" autofocus required></div><button type="submit" class="btn btn-p" style="width:100%;padding:9px;">🔓 Unlock</button></form></div></div>',"Locked")
        if not entered or entered!=paste['password']: db.close(); return base(f'<div style="max-width:360px;margin:44px auto;"><div class="card"><div style="text-align:center;font-size:30px;margin-bottom:8px;">🔒</div><div style="text-align:center;font-size:14px;font-weight:700;color:var(--p);margin-bottom:12px;">{paste["title"]}</div><form method="POST"><div class="fg"><label>Password</label><input name="paste_pw" type="password" autofocus required></div><button type="submit" class="btn btn-p" style="width:100%;padding:9px;">🔓 Unlock</button></form></div></div>',"Locked")
    if request.method=='POST' and request.form.get('comment_text'):
        if not session.get('user_id'): flash('Login to comment!','red')
        else:
            ctxt=request.form.get('comment_text','').strip()[:500]
            if ctxt:
                db.execute("INSERT INTO comments(paste_id,user_id,content) VALUES(?,?,?)",(paste['id'],session['user_id'],ctxt))
                db.commit()
                log_activity(session['user_id'],f'commented on "{paste["title"]}"',paste['id'],'paste')
                if paste['user_id'] and paste['user_id']!=session['user_id']:
                    send_notif(paste['user_id'],f'💬 {session["user"]} commented on "{paste["title"]}"',f'/paste/{slug}')
                flash('Comment added!','green')
        return redirect(f'/paste/{slug}')
    count_unique_view(paste['id'],slug)
    paste=db.execute("SELECT * FROM pastes WHERE slug=?",(slug,)).fetchone()
    auth=None; av='👤'; ath='cyan'
    if paste['user_id']:
        db.execute("UPDATE users SET total_views=(SELECT COALESCE(SUM(views),0) FROM pastes WHERE user_id=?) WHERE id=?",(paste['user_id'],paste['user_id']))
        u2=db.execute("SELECT username,avatar,theme FROM users WHERE id=?",(paste['user_id'],)).fetchone()
        if u2: auth=u2['username']; av=u2['avatar'] or '👤'; ath=u2['theme'] or 'cyan'
    comments=db.execute("SELECT c.*,u.username,u.avatar FROM comments c LEFT JOIN users u ON c.user_id=u.id WHERE c.paste_id=? ORDER BY c.created_at ASC",(paste['id'],)).fetchall()
    user_vote=None
    if session.get('user_id'):
        v=db.execute("SELECT vote FROM paste_likes WHERE paste_id=? AND user_id=?",(paste['id'],session['user_id'])).fetchone()
        if v: user_vote=v['vote']
    db.commit(); db.close()
    lc2=len(paste['content'].split('\n')); chars=len(paste['content']); words=len(paste['content'].split())
    sz=len(paste['content'].encode()); ss=f"{sz/1024:.1f} KB" if sz>1024 else f"{sz} B"
    is_owner=session.get('user_id')==paste['user_id']
    del_btn=f'<a href="/delete/{slug}" class="btn btn-r" style="font-size:9px;padding:3px 7px;" onclick="return confirm(\'Delete?\')">🗑</a>' if is_owner else ''
    edit_btn=f'<a href="/edit/{slug}" class="btn btn-y" style="font-size:9px;padding:3px 7px;">✏️</a>' if is_owner else ''
    pin_btn=f'<a href="/pin/{slug}" class="btn btn-o" style="font-size:9px;padding:3px 7px;">{"📌✓" if paste["pinned"] else "📌"}</a>' if is_owner else ''
    al=f'<a href="/profile/{auth}" style="color:var(--p);text-decoration:none;">{av} {auth}</a>' if auth else 'Anonymous'
    tag_html=''.join(f'<a href="/?tag={t}" class="tag">{t}</a>' for t in paste['tags'].split(',') if t.strip()) if paste['tags'] else ''
    url=f"https://zeroshell-paste.up.railway.app/paste/{slug}"
    tg_url=f"https://t.me/share/url?url={url}&text={paste['title']}"
    highlighted=highlight(paste['content'],paste['syntax'])
    cmts_html=''.join(f'<div class="comment"><div style="display:flex;justify-content:space-between;margin-bottom:4px;"><a href="/profile/{cm["username"]}" style="color:var(--p);text-decoration:none;font-size:10px;font-weight:700;">{cm["avatar"] or "👤"} {cm["username"]}</a><span style="font-size:9px;color:var(--dim);font-family:\'Share Tech Mono\',monospace;">{cm["created_at"][:16]}</span></div><div style="font-size:12px;">{cm["content"]}</div></div>' for cm in comments)
    cmt_form=f'<form method="POST" style="margin-top:10px;"><div class="fg"><textarea name="comment_text" rows="2" placeholder="Comment..." style="resize:vertical;font-size:12px;"></textarea></div><button type="submit" class="btn btn-p" style="font-size:11px;padding:6px 14px;">💬 Post</button></form>' if session.get('user') else f'<div style="text-align:center;padding:10px;color:var(--dim);font-size:11px;"><a href="/login" style="color:var(--p);">Login</a> to comment</div>'
    ai_box=f'<div class="ai-box" id="aiBox" style="display:none;"></div>' if session.get('user_id') else ''
    ai_btn=f'<button onclick="aiSum()" class="btn btn-o" style="font-size:9px;padding:3px 7px;" id="aiBtn">🤖 AI</button>' if session.get('user_id') else ''
    existing_summary=paste['ai_summary']
    if existing_summary:
        ai_box=f'<div class="ai-box" id="aiBox">🤖 {existing_summary}</div>'
    exp_info=''
    if paste['expires_at']:
        try:
            d=datetime.fromisoformat(str(paste['expires_at']))-datetime.now(); h=int(d.total_seconds()//3600)
            exp_info=f'<span style="color:var(--yellow);font-size:9px;font-family:\'Share Tech Mono\',monospace;">⏰{h}h left</span>'
        except: pass
    c=f'''<div style="max-width:880px;margin:0 auto;">
<div class="card">
<div style="display:flex;justify-content:space-between;align-items:flex-start;flex-wrap:wrap;gap:7px;">
<div><div style="font-size:16px;font-weight:700;color:var(--p);margin-bottom:3px;">{"📌 " if paste["pinned"] else ""}{"🔒 " if paste["password"] else ""}{paste["title"]}</div>
<div style="font-size:9px;color:var(--dim);font-family:'Share Tech Mono',monospace;">by {al} · {paste["created_at"][:16]} · {paste["syntax"]}</div>
{f'<div style="margin-top:4px;">{tag_html}</div>' if tag_html else ''}</div>
<div style="display:flex;gap:3px;align-items:center;flex-wrap:wrap;">
<span style="font-family:'Share Tech Mono',monospace;color:var(--green);font-size:10px;">👁{paste["views"]}</span>
<button class="like-btn {"active" if user_vote==1 else ""}" onclick="vote(1)" id="likeBtn">❤️{paste["likes"]}</button>
<button class="like-btn dislike {"active" if user_vote==-1 else ""}" onclick="vote(-1)" id="disBtn">👎{paste["dislikes"]}</button>
<a href="/raw/{slug}" class="btn btn-o" style="font-size:9px;padding:3px 7px;" target="_blank">Raw</a>
<button onclick="cp()" class="btn btn-o" style="font-size:9px;padding:3px 7px;">📋</button>
<button onclick="shareLink()" class="btn btn-o" style="font-size:9px;padding:3px 7px;">🔗</button>
<a href="{tg_url}" target="_blank" class="btn btn-o" style="font-size:9px;padding:3px 7px;">✈️</a>
<a href="/download/{slug}" class="btn btn-g" style="font-size:9px;padding:3px 7px;">📥</a>
<a href="/new?fork={slug}" class="btn btn-o" style="font-size:9px;padding:3px 7px;">🔎Fork</a>
<a href="/diff?a={slug}" class="btn btn-o" style="font-size:9px;padding:3px 7px;">🔀Diff</a>
{ai_btn}{edit_btn}{pin_btn}{del_btn}
</div></div>
<div style="display:flex;gap:8px;margin-top:7px;flex-wrap:wrap;align-items:center;">
<span style="font-family:'Share Tech Mono',monospace;font-size:9px;color:var(--dim);">📄{lc2}·📝{chars}·💾{ss}</span>{exp_info}</div>
{ai_box}</div>
<div class="card" style="padding:0;">
<div style="padding:7px 13px;border-bottom:1px solid var(--border);display:flex;justify-content:space-between;">
<span style="font-family:'Share Tech Mono',monospace;font-size:9px;color:var(--dim);">{paste["syntax"].upper()}</span>
<span style="font-size:9px;color:var(--dim);">{lc2} lines·{ss}</span></div>
<div class="code" id="pc">{highlighted}</div></div>
<div class="card"><div style="font-size:11px;font-weight:700;color:var(--p);letter-spacing:2px;margin-bottom:9px;">💬 COMMENTS ({len(comments)})</div>
{cmts_html or '<div style="text-align:center;color:var(--dim);padding:9px;font-size:11px;">No comments!</div>'}
{cmt_form}</div></div>
<script>
const SLUG="{slug}",URL2="{url}";
function cp(){{navigator.clipboard.writeText(document.getElementById('pc').innerText).then(()=>toast('✅ Copied!'));}}
function shareLink(){{navigator.clipboard.writeText(URL2).then(()=>toast('🔗 Copied!'));}}
function vote(v){{fetch('/vote/'+SLUG+'/'+v,{{method:'POST'}}).then(r=>r.json()).then(d=>{{document.getElementById('likeBtn').textContent='❤️'+d.likes;document.getElementById('disBtn').textContent='👎'+d.dislikes;toast(v==1?'❤️ Liked!':'👎 Disliked!');}}); }}
function aiSum(){{
  const btn=document.getElementById('aiBtn');
  if(btn)btn.textContent='⏳...';
  fetch('/ai-summary/{slug}',{{method:'POST'}}).then(r=>r.json()).then(d=>{{
    const box=document.getElementById('aiBox');
    if(box){{box.style.display='block';box.innerHTML='🤖 '+(d.summary||d.error);}}
    if(btn)btn.style.display='none';
  }});
}}
</script>'''
    return base(c,paste['title'],ath)

# ━━━ VOTE ━━━
@app.route('/vote/<slug>/<int:vote>',methods=['POST'])
def vote_paste(slug,vote):
    if not session.get('user_id'): return jsonify({'error':'login'}),401
    if vote not in (1,-1): return jsonify({'error':'invalid'}),400
    db=get_db(); paste=db.execute("SELECT * FROM pastes WHERE slug=?",(slug,)).fetchone()
    if not paste: db.close(); return jsonify({'error':'nf'}),404
    ex=db.execute("SELECT * FROM paste_likes WHERE paste_id=? AND user_id=?",(paste['id'],session['user_id'])).fetchone()
    if ex:
        if ex['vote']==vote: db.execute("DELETE FROM paste_likes WHERE paste_id=? AND user_id=?",(paste['id'],session['user_id']))
        else: db.execute("UPDATE paste_likes SET vote=? WHERE paste_id=? AND user_id=?",(vote,paste['id'],session['user_id']))
    else:
        db.execute("INSERT INTO paste_likes(paste_id,user_id,vote) VALUES(?,?,?)",(paste['id'],session['user_id'],vote))
        if paste['user_id'] and paste['user_id']!=session['user_id']:
            send_notif(paste['user_id'],f'{"❤️" if vote==1 else "👎"} {session["user"]} {"liked" if vote==1 else "disliked"} "{paste["title"]}"',f'/paste/{slug}')
    likes=db.execute("SELECT COUNT(*) FROM paste_likes WHERE paste_id=? AND vote=1",(paste['id'],)).fetchone()[0]
    dislikes=db.execute("SELECT COUNT(*) FROM paste_likes WHERE paste_id=? AND vote=-1",(paste['id'],)).fetchone()[0]
    db.execute("UPDATE pastes SET likes=?,dislikes=? WHERE id=?",(likes,dislikes,paste['id']))
    db.commit(); db.close()
    return jsonify({'likes':likes,'dislikes':dislikes})

# ━━━ RAW / DOWNLOAD ━━━
@app.route('/raw/<slug>')
def raw_paste(slug):
    db=get_db(); p=db.execute("SELECT * FROM pastes WHERE slug=?",(slug,)).fetchone(); db.close()
    if not p or is_expired(p): return Response("Not found",status=404,mimetype='text/plain')
    if p['password'] and not session.get(f'pw_{slug}'): return Response("Password required",status=403,mimetype='text/plain')
    return Response(p['content'],mimetype='text/plain')

@app.route('/download/<slug>')
def download_paste(slug):
    db=get_db(); p=db.execute("SELECT * FROM pastes WHERE slug=?",(slug,)).fetchone(); db.close()
    if not p or is_expired(p): return Response("Not found",status=404,mimetype='text/plain')
    if p['password'] and not session.get(f'pw_{slug}'): return Response("Password required",status=403,mimetype='text/plain')
    ext={'python':'py','javascript':'js','html':'html','css':'css','bash':'sh','json':'json','sql':'sql'}.get(p['syntax'],'txt')
    fn=p['title'].replace(' ','_')[:40]+'.'+ext
    return Response(p['content'],mimetype='text/plain',headers={"Content-Disposition":f"attachment; filename={fn}"})

# ━━━ EDIT / PIN / DELETE ━━━
@app.route('/edit/<slug>',methods=['GET','POST'])
def edit_paste(slug):
    if not session.get('user_id'): return redirect('/login')
    db=get_db(); paste=db.execute("SELECT * FROM pastes WHERE slug=?",(slug,)).fetchone()
    if not paste or paste['user_id']!=session['user_id']: db.close(); flash('Not allowed!','red'); return redirect('/')
    if request.method=='POST':
        t=request.form.get('title','').strip(); ct=request.form.get('content','').strip()
        sy=request.form.get('syntax','text'); vi=request.form.get('visibility','public')
        tags=','.join(x.strip() for x in request.form.getlist('tags') if x.strip())
        if t and ct: db.execute("UPDATE pastes SET title=?,content=?,syntax=?,visibility=?,tags=?,ai_summary='' WHERE slug=?",(t,ct,sy,vi,tags,slug)); db.commit(); db.close(); flash('Updated!','green'); return redirect(f'/paste/{slug}')
        flash('Fill all fields!','red')
    db.close()
    cur_tags=paste['tags'].split(',') if paste['tags'] else []
    tag_checks=''.join(f'<label style="display:inline-flex;align-items:center;gap:4px;margin:3px;cursor:pointer;font-size:11px;text-transform:none;letter-spacing:0;"><input type="checkbox" name="tags" value="{t}" style="width:auto;" {"checked" if t in cur_tags else ""}> #{t}</label>' for t in ALL_TAGS)
    c=f'''<div style="max-width:800px;margin:0 auto;"><div class="card">
<div style="font-size:14px;font-weight:700;color:var(--yellow);letter-spacing:2px;margin-bottom:12px;">✏️ EDIT</div>
<form method="POST">
<div class="fg"><label>Title</label><input name="title" value="{paste['title']}" required></div>
<div class="fg"><label>Content</label><textarea name="content" id="pc" rows="11" required style="font-family:'Share Tech Mono',monospace;font-size:12px;resize:vertical;" oninput="lc(this)">{paste['content']}</textarea>
<div class="lc-bar"><span>📄 <span class="lc-num" id="ll">0</span></span><span>📝 <span class="lc-num" id="lc2">0</span></span></div></div>
<div class="g2"><div class="fg"><label>Syntax</label><select name="syntax"><option value="text" {"selected" if paste["syntax"]=="text" else ""}>Plain</option><option value="python" {"selected" if paste["syntax"]=="python" else ""}>Python</option><option value="javascript" {"selected" if paste["syntax"]=="javascript" else ""}>JS</option><option value="html" {"selected" if paste["syntax"]=="html" else ""}>HTML</option><option value="bash" {"selected" if paste["syntax"]=="bash" else ""}>Bash</option><option value="json" {"selected" if paste["syntax"]=="json" else ""}>JSON</option><option value="sql" {"selected" if paste["syntax"]=="sql" else ""}>SQL</option></select></div>
<div class="fg"><label>Visibility</label><select name="visibility"><option value="public" {"selected" if paste["visibility"]=="public" else ""}>🌐 Public</option><option value="private" {"selected" if paste["visibility"]=="private" else ""}>🔒 Private</option></select></div></div>
<div class="fg"><label>Tags</label><div style="margin-top:4px;">{tag_checks}</div></div>
<div style="display:flex;gap:7px;"><button type="submit" class="btn btn-p" style="flex:1;font-size:12px;padding:9px;">💾 Save</button><a href="/paste/{slug}" class="btn btn-o" style="padding:9px 14px;font-size:12px;">Cancel</a></div>
</form></div></div>
<script>function lc(el){{const v=el.value;document.getElementById('ll').textContent=v?v.split('\\n').length:0;document.getElementById('lc2').textContent=v.length;}}window.onload=()=>lc(document.getElementById('pc'));</script>'''
    return base(c,"Edit",session.get('theme','cyan'))

@app.route('/pin/<slug>')
def pin_paste(slug):
    if not session.get('user_id'): return redirect('/login')
    db=get_db(); p=db.execute("SELECT * FROM pastes WHERE slug=?",(slug,)).fetchone()
    if p and p['user_id']==session['user_id']: db.execute("UPDATE pastes SET pinned=1-pinned WHERE slug=?",(slug,)); db.commit()
    db.close(); return redirect(f'/paste/{slug}')

@app.route('/delete/<slug>')
def delete_paste(slug):
    if not session.get('user_id'): return redirect('/login')
    db=get_db(); p=db.execute("SELECT * FROM pastes WHERE slug=?",(slug,)).fetchone()
    if p and p['user_id']==session['user_id']: db.execute("DELETE FROM pastes WHERE slug=?",(slug,)); db.commit()
    db.close(); return redirect('/')

# ━━━ FOLLOW ━━━
@app.route('/follow/<username>')
def follow_user(username):
    if not session.get('user_id'): return redirect('/login')
    db=get_db(); target=db.execute("SELECT id FROM users WHERE username=?",(username,)).fetchone()
    if target and target['id']!=session['user_id']:
        ex=db.execute("SELECT id FROM follows WHERE follower_id=? AND following_id=?",(session['user_id'],target['id'])).fetchone()
        if ex: db.execute("DELETE FROM follows WHERE follower_id=? AND following_id=?",(session['user_id'],target['id']))
        else:
            db.execute("INSERT INTO follows(follower_id,following_id) VALUES(?,?)",(session['user_id'],target['id']))
            send_notif(target['id'],f'👥 {session["user"]} followed you!',f'/profile/{session["user"]}')
        db.commit()
    db.close(); return redirect(f'/profile/{username}')

# ━━━ NOTIFICATIONS ━━━
@app.route('/notifications')
def notifications():
    if not session.get('user_id'): return redirect('/login')
    db=get_db()
    notifs=db.execute("SELECT * FROM notifications WHERE user_id=? ORDER BY created_at DESC LIMIT 50",(session['user_id'],)).fetchall()
    db.execute("UPDATE notifications SET read=1 WHERE user_id=?",(session['user_id'],)); db.commit(); db.close()
    rows=''.join(f'<div class="notif {"unread" if not n["read"] else ""}"><div style="flex:1;"><div style="font-size:11px;font-weight:600;">{n["message"]}</div><div style="font-size:9px;color:var(--dim);font-family:\'Share Tech Mono\',monospace;margin-top:2px;">{n["created_at"][:16]}</div></div>{"<div class=notif-dot></div>" if not n["read"] else ""}{"<a href=\'"+n["link"]+"\' style=color:var(--p);text-decoration:none;font-size:10px;>→</a>" if n["link"] else ""}</div>' for n in notifs) or '<div style="text-align:center;color:var(--dim);padding:16px;">No notifications!</div>'
    c=f'<div style="max-width:640px;margin:0 auto;"><div style="font-size:15px;font-weight:700;color:var(--p);letter-spacing:2px;margin-bottom:13px;">🔔 NOTIFICATIONS</div><div class="card">{rows}</div></div>'
    return base(c,"Notifications",session.get('theme','cyan'))

# ━━━ PROFILE ━━━
@app.route('/profile/<username>')
def profile(username):
    db=get_db(); user=db.execute("SELECT * FROM users WHERE username=?",(username,)).fetchone()
    if not user: db.close(); return redirect('/')
    pastes=db.execute("SELECT * FROM pastes WHERE user_id=? AND visibility='public' ORDER BY pinned DESC,created_at DESC",(user['id'],)).fetchall()
    p30=db.execute("SELECT COUNT(*) FROM pastes WHERE user_id=? AND created_at>=?",(user['id'],(datetime.now()-timedelta(days=30)).strftime('%Y-%m-%d'))).fetchone()[0]
    followers=db.execute("SELECT COUNT(*) FROM follows WHERE following_id=?",(user['id'],)).fetchone()[0]
    following=db.execute("SELECT COUNT(*) FROM follows WHERE follower_id=?",(user['id'],)).fetchone()[0]
    is_following=False
    if session.get('user_id') and session['user_id']!=user['id']:
        is_following=bool(db.execute("SELECT id FROM follows WHERE follower_id=? AND following_id=?",(session['user_id'],user['id'])).fetchone())
    chart_labels=[]; pd2=[]; vd=[]
    for i in range(6,-1,-1):
        day=(datetime.now()-timedelta(days=i)); lbl=day.strftime('%b %d'); ds=day.strftime('%Y-%m-%d')
        pc=db.execute("SELECT COUNT(*) FROM pastes WHERE user_id=? AND DATE(created_at)=?",(user['id'],ds)).fetchone()[0]
        vc=db.execute("SELECT COALESCE(SUM(views),0) FROM pastes WHERE user_id=? AND DATE(created_at)=?",(user['id'],ds)).fetchone()[0]
        chart_labels.append(lbl); pd2.append(pc); vd.append(vc)
    db.close()
    theme=user['theme'] or 'cyan'; av=user['avatar'] or '👤'
    p=THEMES.get(theme,'#00f5ff')
    all_b=[('Active','🏃','#00f5ff',p30>=5),('Popular','🔥','#ff2d55',user['total_views']>=1000),('Famous','⚡','#ff6b00',user['total_views']>=5000),('Legendary','👑','#ffd700',user['total_views']>=10000)]
    bh=''.join(f'<span class="badge" style="background:{b[2]}{"22" if b[3] else "08"};color:{b[2] if b[3] else "#4a6a80"};border:1px solid {b[2]}{"44" if b[3] else "18"};">{b[1]} {b[0]}</span>' for b in all_b)
    pl=''.join(f'<a href="/paste/{p2["slug"]}" class="pi {"pinned" if p2["pinned"] else ""}"><div><div class="pt">{"📌 " if p2["pinned"] else ""}{p2["title"]}</div><div class="pm">{p2["created_at"][:10]} · {p2["syntax"]}</div></div><div class="pv">👁{p2["views"]}</div></a>' for p2 in pastes if not is_expired(p2)) or '<div style="text-align:center;color:var(--dim);padding:10px;">No pastes.</div>'
    eb=f'<a href="/settings" class="btn btn-o" style="font-size:10px;padding:3px 8px;">⚙️</a>' if session.get('user')==username else ''
    fb=''
    if session.get('user') and session['user']!=username:
        fc='follow-btn following' if is_following else 'follow-btn'
        ft='✓ Following' if is_following else '+ Follow'
        fb=f'<a href="/follow/{username}" class="{fc}">{ft}</a>'
    tg=f'<a href="https://t.me/{user["telegram"]}" target="_blank" style="color:#00aaff;font-size:10px;text-decoration:none;">✈️ @{user["telegram"]}</a>' if user['telegram'] else ''
    lj=json.dumps(chart_labels); pj=json.dumps(pd2); vj=json.dumps(vd)
    # Build contribution grid (last 52 weeks = 364 days)
    contrib_cells=[]
    for week in range(52):
        row_cells=[]
        for day in range(7):
            day_offset=363-(week*7+day)
            ds=(datetime.now()-timedelta(days=day_offset)).strftime('%Y-%m-%d')
            try:
                db3=get_db()
                cnt=db3.execute("SELECT COUNT(*) FROM pastes WHERE user_id=? AND DATE(created_at)=?",(user['id'],ds)).fetchone()[0]
                db3.close()
            except: cnt=0
            lvl=0 if cnt==0 else (1 if cnt==1 else (2 if cnt<4 else 3))
            colors=['var(--border)','#0e4429','#006d32','#26a641','#39d353']
            light_colors=['#ebedf0','#9be9a8','#40c463','#30a14e','#216e39']
            lc=session.get('light_mode',False)
            c3=light_colors[lvl] if lc else colors[lvl]
            row_cells.append(f'<div class="contrib-cell" style="background:{c3};" title="{ds}: {cnt} pastes"></div>')
        contrib_cells.append('<div class="contrib-row">'+''.join(row_cells)+'</div>')

    c=f'''<div style="max-width:1000px;margin:0 auto;display:grid;grid-template-columns:260px 1fr;gap:20px;">
<!-- LEFT: GitHub-style profile sidebar -->
<div>
<div style="text-align:center;margin-bottom:16px;">
<div class="av-lg" style="width:100px;height:100px;font-size:52px;margin:0 auto 12px;border:3px solid var(--border);box-shadow:0 0 0 4px {p}22;">{av}</div>
<div style="font-size:20px;font-weight:700;color:var(--text);margin-bottom:2px;">{username}</div>
{f'<div style="font-size:12px;color:var(--sub);margin-bottom:8px;">{user["bio"]}</div>' if user["bio"] else ""}
<div style="margin:8px 0;">{bh}</div>
{fb}
</div>
<div style="border-top:1px solid var(--border);padding-top:12px;display:flex;flex-direction:column;gap:8px;font-size:12px;color:var(--sub);">
{f'<div><span style="margin-right:6px;">✈️</span><a href="https://t.me/{user["telegram"]}" target="_blank" style="color:var(--p);text-decoration:none;">@{user["telegram"]}</a></div>' if user["telegram"] else ""}
<div style="display:flex;gap:14px;flex-wrap:wrap;margin-top:4px;">
  <span><strong style="color:var(--text);">{followers}</strong> <span>followers</span></span>
  <span><strong style="color:var(--text);">{following}</strong> <span>following</span></span>
</div>
<div style="display:flex;gap:6px;flex-wrap:wrap;margin-top:6px;">
<div class="sb" style="flex:1;min-width:60px;"><span class="sn" style="color:var(--p);font-size:16px;">{len(pastes)}</span><span class="sl">Pastes</span></div>
<div class="sb" style="flex:1;min-width:60px;"><span class="sn" style="color:var(--green);font-size:16px;">{user["total_views"]}</span><span class="sl">Views</span></div>
</div>
<div style="font-size:10px;margin-top:2px;color:var(--sub);">Joined {user["created_at"][:10]}</div>
</div>
{eb}
</div>
<!-- RIGHT: Activity + pastes -->
<div>
<!-- Contribution Graph -->
<div class="card" style="margin-bottom:14px;">
<div style="font-size:11px;font-weight:700;color:var(--sub);letter-spacing:.8px;text-transform:uppercase;margin-bottom:10px;">{sum(p2["views"] for p2 in pastes)} contributions in the last year</div>
<div class="contrib-grid" style="flex-direction:row;gap:3px;overflow-x:auto;">{"".join(contrib_cells)}</div>
<div style="display:flex;justify-content:flex-end;align-items:center;gap:5px;margin-top:6px;font-size:10px;color:var(--sub);">Less <div style="width:10px;height:10px;background:var(--border);border-radius:2px;"></div><div style="width:10px;height:10px;background:#0e4429;border-radius:2px;"></div><div style="width:10px;height:10px;background:#26a641;border-radius:2px;"></div><div style="width:10px;height:10px;background:#39d353;border-radius:2px;"></div> More</div>
</div>
<!-- Charts -->
<div class="g2" style="margin-bottom:14px;">
<div class="card"><div style="font-size:9px;font-weight:700;color:var(--p);letter-spacing:2px;margin-bottom:7px;">PASTES 7d</div><canvas id="pc2" height="100"></canvas></div>
<div class="card"><div style="font-size:9px;font-weight:700;color:var(--green);letter-spacing:2px;margin-bottom:7px;">VIEWS 7d</div><canvas id="vc2" height="100"></canvas></div></div>
<!-- Pastes -->
<div style="font-size:11px;font-weight:700;color:var(--sub);letter-spacing:.8px;text-transform:uppercase;margin-bottom:8px;">Pastes</div>
{pl}
</div>
</div>'''
    return base(c,username,theme)

# ━━━ SETTINGS + 2FA ━━━
@app.route('/settings',methods=['GET','POST'])
def settings():
    if not session.get('user'): return redirect('/login')
    db=get_db(); user=db.execute("SELECT * FROM users WHERE username=?",(session['user'],)).fetchone(); db.close()
    if request.method=='POST':
        action=request.form.get('action','profile')
        if action=='profile':
            bio=request.form.get('bio','').strip(); tg=request.form.get('telegram','').strip().lstrip('@')
            av=request.form.get('avatar','👤'); th=request.form.get('theme','cyan')
            if th not in THEMES: th='cyan'
            db=get_db(); db.execute("UPDATE users SET bio=?,telegram=?,avatar=?,theme=? WHERE username=?",(bio,tg,av,th,session['user'])); db.commit(); db.close()
            session['avatar']=av; session['theme']=th; flash('Saved!','green')
        elif action=='gen_api':
            new_key='zs_'+secrets.token_hex(20)
            db=get_db(); db.execute("UPDATE users SET api_key=? WHERE username=?",(new_key,session['user'])); db.commit(); db.close()
            flash(f'API Key: {new_key}','green')
            return redirect('/settings')
        elif action=='enable_2fa':
            code=request.form.get('totp_code','').strip()
            secret=session.get('totp_setup_secret','')
            if secret and totp_verify(secret,code):
                db=get_db(); db.execute("UPDATE users SET totp_secret=?,totp_enabled=1 WHERE username=?",(secret,session['user'])); db.commit(); db.close()
                session.pop('totp_setup_secret',None); flash('2FA Enabled!','green')
            else: flash('Wrong code!','red')
        elif action=='disable_2fa':
            db=get_db(); db.execute("UPDATE users SET totp_enabled=0,totp_secret='' WHERE username=?",(session['user'],)); db.commit(); db.close()
            flash('2FA Disabled!','green')
        elif action=='setup_2fa':
            secret=totp_gen_secret(); session['totp_setup_secret']=secret; flash('Scan QR then enter code!','green')
        return redirect('/settings')
    ct=user['theme'] or 'cyan'; ca=user['avatar'] or '👤'
    api_key=user['api_key'] or ''
    th_html=''.join(f'<div class="th-btn {"act" if k==ct else ""}" style="background:{v};" onclick="st(\'{k}\')" title="{k}"></div>' for k,v in THEMES.items())
    av_html=''.join(f'<span class="ao {"sel" if a==ca else ""}" onclick="sa(\'{a}\')">{a}</span>' for a in AVATARS)
    # 2FA section
    setup_secret=session.get('totp_setup_secret','')
    if setup_secret:
        qr_uri=totp_uri(setup_secret,session['user'])
        totp_html=f'''<div style="background:rgba(0,245,255,.05);border:1px solid rgba(0,245,255,.2);border-radius:8px;padding:14px;margin-top:10px;">
<div style="font-size:11px;font-weight:700;color:var(--p);margin-bottom:8px;">📱 SCAN QR OR ENTER KEY</div>
<div style="font-family:'Share Tech Mono',monospace;font-size:11px;background:rgba(0,0,0,.3);padding:8px;border-radius:5px;word-break:break-all;margin-bottom:8px;">{setup_secret}</div>
<div style="font-size:10px;color:var(--dim);margin-bottom:8px;">Use Google Authenticator, Authy, or any TOTP app</div>
<form method="POST"><input type="hidden" name="action" value="enable_2fa">
<div style="display:flex;gap:7px;"><input name="totp_code" placeholder="6-digit code" maxlength="6" style="max-width:140px;"><button type="submit" class="btn btn-p" style="font-size:11px;">✅ Verify</button></div>
</form></div>'''
    elif user['totp_enabled']:
        totp_html=f'''<div style="background:rgba(0,204,102,.05);border:1px solid rgba(0,204,102,.2);border-radius:8px;padding:12px;margin-top:10px;">
<div style="color:var(--green);font-size:12px;font-weight:700;margin-bottom:7px;">✅ 2FA is ENABLED</div>
<form method="POST"><input type="hidden" name="action" value="disable_2fa">
<button type="submit" class="btn btn-r" style="font-size:11px;" onclick="return confirm('Disable 2FA?')">🔓 Disable 2FA</button></form></div>'''
    else:
        totp_html=f'''<div style="background:rgba(255,45,85,.05);border:1px solid rgba(255,45,85,.2);border-radius:8px;padding:12px;margin-top:10px;">
<div style="color:var(--dim);font-size:11px;margin-bottom:7px;">2FA is disabled. Enable for extra security.</div>
<form method="POST"><input type="hidden" name="action" value="setup_2fa">
<button type="submit" class="btn btn-o" style="font-size:11px;">🔒 Setup 2FA</button></form></div>'''
    api_html=f'''<div style="margin-top:10px;"><div style="font-size:10px;color:var(--dim);margin-bottom:5px;text-transform:uppercase;letter-spacing:1px;">API Key</div>
<div style="font-family:'Share Tech Mono',monospace;font-size:11px;background:rgba(0,0,0,.3);padding:7px 10px;border-radius:5px;word-break:break-all;color:var(--p);margin-bottom:6px;">{api_key or "Not generated"}</div>
<form method="POST"><input type="hidden" name="action" value="gen_api"><button type="submit" class="btn btn-o" style="font-size:10px;">🔑 Generate New Key</button></form>
<div style="margin-top:5px;font-size:9px;color:var(--dim);">Use at <a href="/api/v1/docs" style="color:var(--p);">API docs</a></div></div>'''
    c=f'''<div style="max-width:540px;margin:0 auto;">
<div class="card">
<div style="font-size:14px;font-weight:700;color:var(--p);letter-spacing:2px;margin-bottom:13px;">⚙️ SETTINGS</div>
<form method="POST"><input type="hidden" name="action" value="profile">
<input type="hidden" name="avatar" id="ai" value="{ca}">
<input type="hidden" name="theme" id="ti" value="{ct}">
<div class="fg"><label>Avatar</label><div style="display:flex;flex-wrap:wrap;gap:3px;margin-top:3px;">{av_html}</div></div>
<div class="fg"><label>Theme</label><div style="display:flex;gap:5px;margin-top:3px;">{th_html}</div></div>
<div class="fg"><label>Bio</label><input name="bio" value="{user['bio'] or ''}" placeholder="Short bio..."></div>
<div class="fg"><label>Telegram</label><input name="telegram" value="{user['telegram'] or ''}" placeholder="without @"></div>
<button type="submit" class="btn btn-p" style="width:100%;padding:10px;font-size:12px;">💾 Save Profile</button>
</form></div>
<div class="card"><div style="font-size:12px;font-weight:700;color:var(--yellow);margin-bottom:6px;">🔒 TWO-FACTOR AUTH</div>{totp_html}</div>
<div class="card"><div style="font-size:12px;font-weight:700;color:var(--green);margin-bottom:6px;">🌐 API ACCESS</div>{api_html}</div>
</div>
<script>
function sa(a){{document.getElementById('ai').value=a;document.querySelectorAll('.ao').forEach(e=>e.classList.remove('sel'));event.target.classList.add('sel');}}
function st(t){{document.getElementById('ti').value=t;document.querySelectorAll('.th-btn').forEach(e=>e.classList.remove('act'));event.target.classList.add('act');}}
</script>'''
    return base(c,"Settings",ct)

# ━━━ ADMIN + ANALYTICS ━━━
@app.route('/admin')
def admin():
    if not session.get('is_admin'): flash('Admin only!','red'); return redirect('/')
    cleanup_expired()
    db=get_db()
    users=db.execute("SELECT * FROM users ORDER BY total_views DESC").fetchall()
    pastes=db.execute("SELECT p.*,u.username FROM pastes p LEFT JOIN users u ON p.user_id=u.id ORDER BY p.created_at DESC LIMIT 50").fetchall()
    ads=db.execute("SELECT * FROM ads ORDER BY created_at DESC").fetchall()
    tv=db.execute("SELECT COALESCE(SUM(views),0) FROM pastes").fetchone()[0]
    tc=db.execute("SELECT COUNT(*) FROM comments").fetchone()[0]
    tf=db.execute("SELECT COUNT(*) FROM follows").fetchone()[0]
    # analytics data - last 14 days
    an_labels=[]; an_pastes=[]; an_views=[]; an_users=[]
    for i in range(13,-1,-1):
        day=(datetime.now()-timedelta(days=i)); ds=day.strftime('%Y-%m-%d')
        an_labels.append(day.strftime('%d %b'))
        an_pastes.append(db.execute("SELECT COUNT(*) FROM pastes WHERE DATE(created_at)=?",(ds,)).fetchone()[0])
        an_views.append(db.execute("SELECT COUNT(*) FROM paste_views WHERE DATE(created_at)=?",(ds,)).fetchone()[0])
        an_users.append(db.execute("SELECT COUNT(*) FROM users WHERE DATE(created_at)=?",(ds,)).fetchone()[0])
    # syntax distribution
    syn_data=db.execute("SELECT syntax,COUNT(*) as c FROM pastes GROUP BY syntax ORDER BY c DESC").fetchall()
    syn_labels=json.dumps([s['syntax'] for s in syn_data])
    syn_vals=json.dumps([s['c'] for s in syn_data])
    db.close()
    uh=''.join(f'<tr><td><a href="/profile/{u["username"]}" style="color:var(--p);text-decoration:none;">{u["avatar"] or "👤"} {u["username"]}</a></td><td style="color:var(--dim)">{u["created_at"][:10]}</td><td style="color:var(--green)">{u["total_views"]}</td><td>{"👑" if u["is_admin"] else "👤"}</td><td><a href="/admin/del-user/{u["id"]}" class="btn btn-r" style="font-size:8px;padding:2px 5px;" onclick="return confirm(\'Delete?\')">Del</a></td></tr>' for u in users)
    ph=''.join(f'<tr><td><a href="/paste/{p["slug"]}" style="color:var(--p);text-decoration:none;">{p["title"][:20]}</a></td><td style="color:var(--dim)">{p["username"] or "Anon"}</td><td style="color:var(--green)">{p["views"]}</td><td style="color:var(--dim)">{p["created_at"][:10]}</td><td><a href="/admin/del-paste/{p["slug"]}" class="btn btn-r" style="font-size:8px;padding:2px 5px;">Del</a></td></tr>' for p in pastes)
    adh=''.join(f'<tr><td>{a["title"]}</td><td style="color:var(--dim)">{a["content"][:26]}</td><td style="color:{"var(--green)" if a["active"] else "var(--red)"}">{"✅" if a["active"] else "❌"}</td><td><a href="/admin/toggle-ad/{a["id"]}" class="btn btn-o" style="font-size:8px;padding:2px 5px;">Toggle</a> <a href="/admin/del-ad/{a["id"]}" class="btn btn-r" style="font-size:8px;padding:2px 5px;">Del</a></td></tr>' for a in ads)
    lj=json.dumps(an_labels); pj=json.dumps(an_pastes); vj=json.dumps(an_views); uj=json.dumps(an_users)
    c=f'''<div style="font-size:15px;font-weight:700;color:var(--p);letter-spacing:2px;margin-bottom:13px;">⚙️ ADMIN PANEL v7</div>
<div class="g4" style="margin-bottom:13px;">
<div class="sb"><span class="sn" style="color:var(--p);">{len(users)}</span><span class="sl">Users</span></div>
<div class="sb"><span class="sn" style="color:var(--green);">{len(pastes)}</span><span class="sl">Pastes</span></div>
<div class="sb"><span class="sn" style="color:var(--yellow);">{tv}</span><span class="sl">Views</span></div>
<div class="sb"><span class="sn" style="color:var(--dim);">{tc}</span><span class="sl">Comments</span></div>
</div>
<div class="card"><div style="font-size:11px;font-weight:700;color:var(--p);letter-spacing:2px;margin-bottom:10px;">📊 ANALYTICS — 14 DAYS</div>
<div style="margin-bottom:14px;"><canvas id="ac" height="70"></canvas></div>
<div class="g2"><div><canvas id="ac2" height="100"></canvas></div><div><canvas id="ac3" height="100"></canvas></div></div>
</div>
<div class="card"><div style="font-size:11px;font-weight:700;color:var(--yellow);margin-bottom:8px;">📢 Add Ad</div>
<form method="POST" action="/admin/add-ad"><div class="g2"><div class="fg"><label>Title</label><input name="title" required></div><div class="fg"><label>URL</label><input name="url"></div></div>
<div class="fg"><label>Content</label><input name="content" required></div>
<button type="submit" class="btn btn-p" style="font-size:10px;">Add</button></form></div>
<div class="card"><div style="font-size:10px;font-weight:700;color:var(--yellow);margin-bottom:7px;">📢 ADS</div><div style="overflow-x:auto;"><table class="at"><tr><th>Title</th><th>Content</th><th>Status</th><th>Action</th></tr>{adh}</table></div></div>
<div class="card"><div style="font-size:10px;font-weight:700;color:var(--p);margin-bottom:7px;">👤 USERS ({len(users)})</div><div style="overflow-x:auto;"><table class="at"><tr><th>Username</th><th>Joined</th><th>Views</th><th>Role</th><th>Del</th></tr>{uh}</table></div></div>
<div class="card"><div style="font-size:10px;font-weight:700;color:var(--p);margin-bottom:7px;">📝 PASTES</div><div style="overflow-x:auto;"><table class="at"><tr><th>Title</th><th>Author</th><th>Views</th><th>Date</th><th>Del</th></tr>{ph}</table></div></div>
<script>
const lb={lj},pad={pj},vad={vj},uad={uj},sl={syn_labels},sv={syn_vals};
const colors=['#00f5ff','#ff79c6','#50fa7b','#bd93f9','#f1fa8c','#ff5555','#8be9fd','#ffb86c'];
const base_opts={{plugins:{{legend:{{display:false}}}},scales:{{x:{{ticks:{{color:"#4a6a80",font:{{size:8}}}},grid:{{color:"rgba(128,128,128,.06)"}}}},y:{{ticks:{{color:"#4a6a80",font:{{size:8}}}},grid:{{color:"rgba(128,128,128,.06)"}}}}}} }};
new Chart(document.getElementById('ac'),{{type:'line',data:{{labels:lb,datasets:[{{label:'Pastes',data:pad,borderColor:'#00f5ff',tension:.4,fill:false}},{{label:'Views',data:vad,borderColor:'#00cc66',tension:.4,fill:false}},{{label:'Users',data:uad,borderColor:'#bd93f9',tension:.4,fill:false}}]}},options:{{...base_opts,plugins:{{legend:{{display:true,labels:{{color:'#4a6a80',font:{{size:9}}}}}}}}}}}});
new Chart(document.getElementById('ac2'),{{type:'bar',data:{{labels:lb,datasets:[{{label:'New Users',data:uad,backgroundColor:'#bd93f933',borderColor:'#bd93f9',borderWidth:2,borderRadius:4}}]}},options:{{...base_opts,plugins:{{legend:{{display:true,labels:{{color:'#4a6a80',font:{{size:9}}}}}}}}}} }});
new Chart(document.getElementById('ac3'),{{type:'doughnut',data:{{labels:sl,datasets:[{{data:sv,backgroundColor:colors.map(c=>c+'88'),borderColor:colors,borderWidth:2}}]}},options:{{plugins:{{legend:{{position:'right',labels:{{color:'#4a6a80',font:{{size:9}}}}}}}}}} }});
</script>'''
    return base(c,"Admin",session.get('theme','cyan'))

@app.route('/admin/add-ad',methods=['POST'])
def add_ad():
    if not session.get('is_admin'): return redirect('/')
    t=request.form.get('title',''); ct=request.form.get('content',''); u=request.form.get('url','')
    if t and ct:
        db=get_db(); db.execute("INSERT INTO ads(title,content,url) VALUES(?,?,?)",(t,ct,u)); db.commit(); db.close(); flash('Ad added!','green')
    return redirect('/admin')
@app.route('/admin/toggle-ad/<int:i>')
def toggle_ad(i):
    if not session.get('is_admin'): return redirect('/')
    db=get_db(); db.execute("UPDATE ads SET active=1-active WHERE id=?",(i,)); db.commit(); db.close(); return redirect('/admin')
@app.route('/admin/del-ad/<int:i>')
def del_ad(i):
    if not session.get('is_admin'): return redirect('/')
    db=get_db(); db.execute("DELETE FROM ads WHERE id=?",(i,)); db.commit(); db.close(); return redirect('/admin')
@app.route('/admin/del-user/<int:i>')
def del_user(i):
    if not session.get('is_admin'): return redirect('/')
    db=get_db(); db.execute("DELETE FROM users WHERE id=?",(i,)); db.execute("DELETE FROM pastes WHERE user_id=?",(i,)); db.commit(); db.close(); flash('Deleted!','green'); return redirect('/admin')
@app.route('/admin/del-paste/<slug>')
def del_paste(slug):
    if not session.get('is_admin'): return redirect('/')
    db=get_db(); db.execute("DELETE FROM pastes WHERE slug=?",(slug,)); db.commit(); db.close(); return redirect('/admin')

# ━━━ AUTH ━━━
@app.route('/register',methods=['GET','POST'])
def register():
    if request.method=='POST':
        u=request.form.get('username','').strip(); email=request.form.get('email','').strip().lower()
        pw=request.form.get('password',''); pw2=request.form.get('password2','')
        tg=request.form.get('telegram','').strip().lstrip('@')
        if not u or not pw: return _auth('Register','Fill all fields!')
        if len(u)<3: return _auth('Register','Username min 3 chars!')
        if len(pw)<6: return _auth('Register','Password min 6 chars!')
        if pw!=pw2: return _auth('Register','Passwords do not match!')
        if email and '@' not in email: return _auth('Register','Invalid email!')
        db=get_db()
        if db.execute("SELECT id FROM users WHERE username=?",(u,)).fetchone(): db.close(); return _auth('Register','Username taken!')
        if email and db.execute("SELECT id FROM users WHERE email=?",(email,)).fetchone(): db.close(); return _auth('Register','Email already used!')
        ia=1 if db.execute("SELECT COUNT(*) FROM users").fetchone()[0]==0 else 0
        db.execute("INSERT INTO users(username,email,password,telegram,is_admin) VALUES(?,?,?,?,?)",(u,email,hash_pw(pw),tg,ia))
        db.commit(); user=db.execute("SELECT * FROM users WHERE username=?",(u,)).fetchone(); db.close()
        session.update({'user':u,'user_id':user['id'],'is_admin':user['is_admin'],'avatar':user['avatar'] or '👤','theme':user['theme'] or 'cyan'})
        return redirect(f'/profile/{u}')
    return _auth('Register')

@app.route('/login',methods=['GET','POST'])
def login():
    if request.method=='POST':
        lid=request.form.get('username','').strip(); pw=request.form.get('password','')
        totp_code=request.form.get('totp_code','').strip()
        db=get_db()
        user=db.execute("SELECT * FROM users WHERE (username=? OR email=?) AND password=?",(lid,lid.lower(),hash_pw(pw))).fetchone()
        db.close()
        if not user: return _auth('Login','Wrong credentials!')
        if user['totp_enabled']:
            if not totp_code: return _auth('Login','','2fa_needed',user['username'])
            if not totp_verify(user['totp_secret'],totp_code): return _auth('Login','Wrong 2FA code!','2fa_needed',user['username'])
        session.update({'user':user['username'],'user_id':user['id'],'is_admin':user['is_admin'],'avatar':user['avatar'] or '👤','theme':user['theme'] or 'cyan'})
        return redirect(f'/profile/{user["username"]}')
    return _auth('Login')

def _auth(title,err='',mode='',hidden_user=''):
    import html as hm
    extra=''
    if title=='Register':
        extra='<div class="fg"><label>Email (optional)</label><input name="email" type="email" placeholder="example@gmail.com"></div><div class="fg"><label>Confirm Password</label><input name="password2" type="password" placeholder="Repeat password..." required></div><div class="fg"><label>Telegram (optional)</label><input name="telegram" placeholder="without @"></div>'
    totp_field=''
    if mode=='2fa_needed':
        totp_field=f'<input type="hidden" name="username" value="{hm.escape(hidden_user)}"><div class="fg"><label>🔒 2FA Code</label><input name="totp_code" placeholder="6-digit code" maxlength="6" autofocus required style="letter-spacing:6px;text-align:center;font-size:18px;"></div>'
        err=err or 'Enter your 2FA code'
    alt='Account? <a href="/login" style="color:var(--p);">Login</a>' if title=='Register' else 'New? <a href="/register" style="color:var(--p);">Register</a>'
    eh=f'<div class="alert {"ag" if not err else "ar"}">{err}</div>' if err else ''
    un_field='' if mode=='2fa_needed' else '<div class="fg"><label>Username or Email</label><input name="username" required autocomplete="off"></div>'
    pw_field='' if mode=='2fa_needed' else '<div class="fg"><label>Password</label><input name="password" type="password" required></div>'
    c=f'''<div style="max-width:360px;margin:38px auto;"><div class="card">
<div style="text-align:center;font-size:28px;margin-bottom:6px;">⚡</div>
<div style="text-align:center;font-size:15px;font-weight:700;color:var(--p);letter-spacing:2px;margin-bottom:12px;">{title.upper()}</div>
{eh}<form method="POST">
{un_field}{pw_field}{extra}{totp_field}
<button type="submit" class="btn btn-p" style="width:100%;padding:10px;font-size:13px;">{title if mode!="2fa_needed" else "Verify →"}</button>
</form><div style="text-align:center;margin-top:9px;font-size:10px;color:var(--dim);">{alt}</div>
</div></div>'''
    return base(c,title)

@app.route('/logout')
def logout():
    session.clear(); return redirect('/')

if __name__=='__main__':
    init_db()
    cleaned=cleanup_expired()
    if cleaned: print(f"🧹 Cleaned {cleaned} expired pastes")
    port=int(os.environ.get('PORT',5000))
    print(f"\n{'='*50}\n  ⚡  ZEROSHELL v7.5\n  🌐  http://localhost:{port}\n{'='*50}\n")
    app.run(host='0.0.0.0',port=port,debug=False)
