"""ZeroShell v7.0 - PWA + API + 2FA + AI + Diff + Analytics"""
import os,hashlib,secrets,json,re,hmac,struct,time,base64,urllib.request,urllib.parse
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
  CREATE TABLE IF NOT EXISTS bookmarks(id INTEGER PRIMARY KEY AUTOINCREMENT,user_id INTEGER,paste_id INTEGER,created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,UNIQUE(user_id,paste_id));
  CREATE TABLE IF NOT EXISTS revisions(id INTEGER PRIMARY KEY AUTOINCREMENT,paste_id INTEGER,content TEXT,title TEXT,syntax TEXT,editor_id INTEGER,created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP);
  CREATE TABLE IF NOT EXISTS email_verifications(id INTEGER PRIMARY KEY AUTOINCREMENT,user_id INTEGER,token TEXT,created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP);
  CREATE TABLE IF NOT EXISTS rate_limits(id INTEGER PRIMARY KEY AUTOINCREMENT,ip TEXT,action TEXT,created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP);
  CREATE TABLE IF NOT EXISTS payment_requests(id INTEGER PRIMARY KEY AUTOINCREMENT,user_id INTEGER,plan TEXT,coin TEXT,tx_hash TEXT,amount TEXT,status TEXT DEFAULT 'pending',created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP);
  """)
  safe=[("users","avatar","TEXT DEFAULT '👤'"),("users","theme","TEXT DEFAULT 'cyan'"),("users","is_admin","INTEGER DEFAULT 0"),("users","email","TEXT DEFAULT ''"),("users","totp_secret","TEXT DEFAULT ''"),("users","totp_enabled","INTEGER DEFAULT 0"),("users","api_key","TEXT DEFAULT ''"),("pastes","password","TEXT DEFAULT ''"),("pastes","pinned","INTEGER DEFAULT 0"),("pastes","expires_at","TIMESTAMP DEFAULT NULL"),("pastes","tags","TEXT DEFAULT ''"),("pastes","likes","INTEGER DEFAULT 0"),("pastes","dislikes","INTEGER DEFAULT 0"),("pastes","ai_summary","TEXT DEFAULT ''"),("users","is_premium","INTEGER DEFAULT 0"),("users","premium_note","TEXT DEFAULT ''"),("users","email_verified","INTEGER DEFAULT 0"),("users","link1","TEXT DEFAULT ''"),("users","link2","TEXT DEFAULT ''"),("users","link3","TEXT DEFAULT ''"),("users","link4","TEXT DEFAULT ''"),("users","link5","TEXT DEFAULT ''")]
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
  if views>=1000: return('Popular','','#ff2d55')
  if p30>=5: return('Active','🏃','#00f5ff')
  return('Newcomer','⭐','#8899aa')

THEMES={'cyan':'#00f5ff','red':'#ff2d55','green':'#00ff88','gold':'#ffd60a','purple':'#bf5af2','blue':'#2979ff'}
AVATARS=['👤','⚡','','💀','🤖','👾','🦊','🐉','🎭','🔮','🦅','🐺']
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
nav{{background:{nav_bg};border-bottom:1px solid var(--border);position:sticky;top:0;z-index:200;backdrop-filter:blur(20px);}}
.logo{{font-family:'Share Tech Mono',monospace;font-size:20px;color:var(--p);text-decoration:none;letter-spacing:3px;text-shadow:0 0 18px {p}66;font-weight:700;flex-shrink:0;}}
.nav-links{{display:flex;gap:4px;align-items:center;flex:1;flex-wrap:nowrap;overflow:hidden;margin-left:8px;}}
.nav-links a{{color:var(--text);text-decoration:none;font-size:14px;font-weight:600;padding:6px 12px;border-radius:8px;transition:all .15s;white-space:nowrap;}}
.nav-links a:hover{{background:rgba(128,128,128,.12);color:var(--p);}}
.hamburger{{display:none;flex-direction:column;gap:5px;cursor:pointer;padding:7px;border-radius:7px;border:1px solid var(--border);background:transparent;}}
.hamburger span{{display:block;width:20px;height:2px;background:var(--text);border-radius:2px;}}
.mob-menu{{display:none;flex-direction:column;gap:3px;padding:10px 14px 16px;background:{nav_bg};border-bottom:1px solid var(--border);}}
.mob-menu a{{color:var(--text);text-decoration:none;font-size:15px;font-weight:600;padding:10px 14px;border-radius:8px;}}
.mob-menu a:hover{{background:rgba(128,128,128,.08);}}
@media(max-width:800px){{.nav-links{{display:none;}}.hamburger{{display:flex;}}.mob-menu.open{{display:flex;}}}}
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
    nav_r=f'<a href="/notifications" title="Notifications">Notifs{nb}</a><a href="/profile/{u}" style="display:inline-flex;align-items:center;gap:6px;font-weight:700;color:var(--p);font-size:14px;"><span style="font-size:18px;">{session.get("avatar","👤")}</span>{u}</a>{adm}<a href="/logout" style="color:var(--s);font-size:13px;">Logout</a>'
    mob_r=f'<a href="/notifications">🔔 Notifs{nb}</a><a href="/feed">📊 Feed</a><a href="/profile/{u}">{session.get("avatar","👤")} {u}</a><a href="/settings">⚙️ Settings</a>{"<a href=/admin>👑 Admin</a>" if session.get("is_admin") else ""}<a href="/logout">🚪 Logout</a>'
  else:
    nav_r='<a href="/login" style="font-weight:600;font-size:14px;">Login</a><a href="/register" class="btn btn-p" style="padding:7px 18px;font-size:14px;border-radius:8px;">Register</a>'
    mob_r='<a href="/login">Login</a><a href="/register">Register</a>'
  p_color=THEMES.get(theme,'#00f5ff')
  return f'''<!DOCTYPE html><html lang="en"><head>
<meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<meta name="theme-color" content="{p_color}">
<meta name="apple-mobile-web-app-capable" content="yes">
<meta name="apple-mobile-web-app-title" content="ZeroShell">
<link rel="manifest" href="/manifest.json">
<title>{title} - ZeroShell</title>{s}</head><body>
{TOAST_JS}{MOB_JS}{PWA_JS}
<nav>
<div style="max-width:1280px;margin:0 auto;padding:0 24px;height:62px;display:flex;align-items:center;gap:8px;">
 <a class="logo" href="/">⚡ ZeroShell</a>
 <div class="nav-links">
  <a href="/">Home</a>
  <a href="/leaderboard">Board</a>
  <a href="/api/v1/docs">API</a>
  <a href="/premium" style="background:linear-gradient(135deg,#ffd700,#ff8c00);color:#000;font-weight:800;border-radius:8px;padding:6px 14px;font-size:13px;">&#11088; Premium</a>
  <a href="https://t.me/ZeroShell_help" target="_blank" style="background:#229ed9;color:#fff;font-weight:700;border-radius:8px;padding:6px 14px;font-size:13px;">Telegram</a>
 </div>
 <div style="display:flex;gap:5px;align-items:center;margin-left:auto;flex-shrink:0;">
  {nav_r}
  <button class="install-btn" id="installBtn" onclick="installPWA()">Install</button>
  <a href="/toggle-mode" class="mode-btn">{mi}</a>
 </div>
 <div class="hamburger" onclick="toggleMenu()"><span></span><span></span><span></span></div>
</div>
</nav>
<div class="mob-menu" id="mm">
 <a href="/">Home</a>
 <a href="/leaderboard">Leaderboard</a>
 <a href="/api/v1/docs">API</a>
 <a href="/premium" style="color:#ffd700;font-weight:700;">Premium</a>
 <a href="https://t.me/ZeroShell_help" target="_blank" style="color:#229ed9;">Telegram</a>
 {mob_r}
 <a href="https://t.me/ZeroShell_help" target="_blank">Telegram Help</a>
 <a href="/toggle-mode">{mi} Dark/Light</a>
</div>
<div class="wrap">{alerts}{ad_html}{content}</div>
<!-- Telegram helpline -->
<div style="position:fixed;bottom:22px;left:18px;z-index:998;">
 <a href="https://t.me/ZeroShell_help" target="_blank" style="display:flex;align-items:center;gap:7px;padding:9px 16px;border-radius:99px;background:#229ed9;color:#fff;text-decoration:none;font-size:13px;font-weight:700;box-shadow:0 4px 20px rgba(34,158,217,.4);transition:all .2s;">
  <svg width="15" height="15" viewBox="0 0 24 24" fill="white"><path d="M12 0C5.373 0 0 5.373 0 12s5.373 12 12 12 12-5.373 12-12S18.627 0 12 0zm5.894 8.221l-1.97 9.28c-.145.658-.537.818-1.084.508l-3-2.21-1.447 1.394c-.16.16-.295.295-.605.295l.213-3.053 5.56-5.023c.242-.213-.054-.333-.373-.12l-6.871 4.326-2.962-.924c-.643-.204-.657-.643.136-.953l11.57-4.461c.537-.194 1.006.131.833.941z"/></svg>
  Help
 </a>
</div>
<footer>
 <div style="display:flex;justify-content:center;gap:22px;flex-wrap:wrap;margin-bottom:5px;">
  <span style="font-weight:700;color:var(--p);font-family:'Share Tech Mono',monospace;">ZeroShell v7.5</span>
  <a href="https://t.me/ZeroShell_help" target="_blank" style="color:#229ed9;text-decoration:none;font-weight:600;">Telegram</a>
  <a href="/api/v1/docs" style="color:var(--dim);text-decoration:none;">API</a>
  <a href="/leaderboard" style="color:var(--dim);text-decoration:none;">Leaderboard</a>
 </div>
 <div style="font-size:11px;color:var(--dim);opacity:.5;">© 2025 ZeroShell · Paste &amp; Share</div>
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

# ━━━ ALL PASTES ━━━
@app.route('/pastes')
def all_pastes():
  page=max(1,int(request.args.get('page',1)))
  per=20; offset=(page-1)*per
  syntax=request.args.get('syntax','')
  db=get_db()
  if syntax:
    pastes=db.execute("SELECT p.*,u.username,u.avatar,u.is_premium FROM pastes p LEFT JOIN users u ON p.user_id=u.id WHERE p.visibility='public' AND p.syntax=? ORDER BY u.is_premium DESC,p.created_at DESC LIMIT ? OFFSET ?",(syntax,per,offset)).fetchall()
    total=db.execute("SELECT COUNT(*) FROM pastes WHERE visibility='public' AND syntax=?",(syntax,)).fetchone()[0]
  else:
    pastes=db.execute("SELECT p.*,u.username,u.avatar,u.is_premium FROM pastes p LEFT JOIN users u ON p.user_id=u.id WHERE p.visibility='public' ORDER BY u.is_premium DESC,p.created_at DESC LIMIT ? OFFSET ?",(per,offset)).fetchall()
    total=db.execute("SELECT COUNT(*) FROM pastes WHERE visibility='public'").fetchone()[0]
  db.close()
  pages=max(1,(total+per-1)//per)
  pl=''.join(f'<a href="/paste/{p["slug"]}" class="pi"><div><div class="pt">{"🔒 " if p["password"] else ""}{p["title"]}</div><div class="pm">{p["avatar"] or "👤"} {p["username"] or "Anon"} · {p["created_at"][:10]} · {p["syntax"]}</div></div><div class="pv">👁 {p["views"]}</div></a>' for p in pastes if not is_expired(p)) or '<div style="text-align:center;color:var(--dim);padding:24px;">No pastes!</div>'
  syn_opts='<option value="">All</option>'+"".join(f'<option value="{s}" {"selected" if syntax==s else ""}>{s}</option>' for s in ["python","javascript","html","css","bash","json","sql","text"])
  # pagination
  prev_btn=f'<a href="/pastes?page={page-1}&syntax={syntax}" class="btn btn-o">← Prev</a>' if page>1 else ''
  next_btn=f'<a href="/pastes?page={page+1}&syntax={syntax}" class="btn btn-o">Next →</a>' if page<pages else ''
  c=f'''<div style="max-width:860px;margin:0 auto;">
<div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:18px;flex-wrap:wrap;gap:10px;">
 <div>
  <div style="font-size:22px;font-weight:800;color:var(--text);">📝 All Pastes</div>
  <div style="font-size:12px;color:var(--dim);margin-top:2px;">{total} public pastes</div>
 </div>
 <form method="GET" style="display:flex;gap:7px;align-items:center;">
  <select name="syntax" style="width:auto;padding:6px 10px;font-size:13px;">{syn_opts}</select>
  <button type="submit" class="btn btn-o" style="font-size:12px;">Filter</button>
 </form>
</div>
<div class="card">{pl}</div>
<div style="display:flex;justify-content:space-between;align-items:center;margin-top:10px;">
 <div>{prev_btn}</div>
 <span style="font-size:12px;color:var(--dim);">Page {page} / {pages}</span>
 <div>{next_btn}</div>
</div>
</div>'''
  return base(c,"All Pastes",session.get('theme','cyan'))

# ━━━ ALL USERS (Premium Only) ━━━
@app.route('/users')
def all_users():
  q=request.args.get('q','').strip()
  db=get_db()
  if q:
    users=db.execute(
      "SELECT u.*,COUNT(p.id) as pc FROM users u LEFT JOIN pastes p ON u.id=p.user_id WHERE u.is_premium=1 AND u.username LIKE ? GROUP BY u.id ORDER BY u.total_views DESC",
      (f'%{q}%',)).fetchall()
  else:
    users=db.execute(
      "SELECT u.*,COUNT(p.id) as pc FROM users u LEFT JOIN pastes p ON u.id=p.user_id WHERE u.is_premium=1 GROUP BY u.id ORDER BY u.total_views DESC"
    ).fetchall()
  total_all=db.execute("SELECT COUNT(*) FROM users").fetchone()[0]
  db.close()

  def _row(u):
    note=u['premium_note'] or 'Premium'
    bio_short=u['bio'][:50]+'...' if u['bio'] and len(u['bio'])>50 else (u['bio'] or '')
    tg_link=f'<a href="https://t.me/{u["telegram"]}" target="_blank" style="color:#229ed9;font-size:11px;text-decoration:none;">✈️ @{u["telegram"]}</a>' if u['telegram'] else ''
    return f'''<div style="display:flex;align-items:center;gap:14px;padding:14px 16px;background:var(--bg);border:1px solid var(--border);border-left:3px solid #ffd700;border-radius:10px;margin-bottom:8px;transition:all .15s;" onmouseover="this.style.transform='translateX(3px)'" onmouseout="this.style.transform='translateX(0)'">
<div style="font-size:30px;flex-shrink:0;filter:drop-shadow(0 0 6px #ffd70066);">{u["avatar"] or "👤"}</div>
<div style="flex:1;min-width:0;">
 <div style="display:flex;align-items:center;gap:7px;flex-wrap:wrap;margin-bottom:3px;">
  <a href="/profile/{u["username"]}" style="color:var(--p);text-decoration:none;font-size:16px;font-weight:700;">{u["username"]}</a>
  <span style="background:linear-gradient(135deg,#ffd700,#ff8c00);color:#000;border-radius:99px;padding:1px 9px;font-size:10px;font-weight:800;letter-spacing:.5px;">VIP</span>
  {"<span style='background:rgba(63,185,80,.15);color:#3fb950;border:1px solid rgba(63,185,80,.3);border-radius:99px;padding:1px 7px;font-size:10px;font-weight:700;'> Verified</span>" if u["email_verified"] else ""}
 </div>
 <div style="font-size:12px;color:var(--dim);margin-bottom:3px;">{bio_short}</div>
 {tg_link}
</div>
<div style="text-align:right;flex-shrink:0;">
 <div style="font-family:monospace;color:var(--green);font-size:14px;font-weight:700;">👁 {u["total_views"]}</div>
 <div style="font-size:11px;color:var(--dim);margin-top:2px;">{u["pc"]} pastes</div>
</div>
</div>'''

  rows=''.join(_row(u) for u in users)
  empty=f'''<div style="text-align:center;padding:60px 20px;">
<div style="font-size:56px;margin-bottom:14px;">💎</div>
<div style="font-size:18px;font-weight:700;color:var(--text);margin-bottom:6px;">No Premium Members Yet</div>
<div style="font-size:13px;color:var(--dim);">Premium members will appear here.</div>
</div>'''

  c=f'''<div style="max-width:760px;margin:0 auto;">
<!-- Header -->
<div style="text-align:center;padding:28px 0 20px;">
 <div style="font-size:40px;margin-bottom:8px;">💎</div>
 <div style="font-size:26px;font-weight:800;color:var(--text);">Premium Members</div>
 <div style="font-size:13px;color:var(--dim);margin-top:5px;">{len(users)} premium · {total_all} total members</div>
</div>
<!-- Search -->
<form method="GET" style="display:flex;gap:8px;margin-bottom:18px;">
 <input name="q" value="{q}" placeholder="Search premium members..." style="flex:1;">
 <button type="submit" class="btn btn-o">🔍 Search</button>
</form>
<!-- Premium badge info -->
<div style="background:linear-gradient(135deg,rgba(255,215,0,.08),rgba(255,140,0,.06));border:1px solid rgba(255,215,0,.25);border-radius:10px;padding:12px 16px;margin-bottom:18px;display:flex;align-items:center;gap:10px;">
 <span style="font-size:20px;">💎</span>
 <div>
  <div style="font-size:13px;font-weight:700;color:#ffd700;">Premium Members</div>
  <div style="font-size:11px;color:var(--dim);">Admin দ্বারা verified বিশেষ সদস্য। Premium badge পেতে Admin এর সাথে যোগাযোগ করুন।</div>
 </div>
 <a href="https://t.me/ZeroShell_help" target="_blank" class="btn" style="background:#229ed9;color:#fff;border-color:#229ed9;font-size:12px;margin-left:auto;flex-shrink:0;">✈️ Contact</a>
</div>
<!-- List -->
{rows or empty}
</div>'''
  return base(c,"Premium Members",session.get('theme','cyan'))

# ━━━ AUTO VERIFY ━━━
PAYMENT_ADDRS={'USDT':'TBWUnddB2J5cckALZenPo6KQJwLzysEohE','BTC':'1N39KVvVK8itaGr7odbrTKnBdbwt4n7PoY','ETH':'0xd4c1ff57a77ce3a7b99ff96b410f05501b84b838','LTC':'LcU6RqsSHQ8XUUP6xDEWDBWUts8wUe5adf'}
PLAN_PRICES={'1month':10,'6month':40,'1year':80}

def auto_verify_tx(coin,tx_hash,plan):
  import urllib.request,json as _j
  addr=PAYMENT_ADDRS.get(coin,''); expected=PLAN_PRICES.get(plan,0)
  try:
    if coin=='USDT':
      url=f"https://apilist.tronscanapi.com/api/transaction-info?hash={tx_hash}"
      with urllib.request.urlopen(urllib.request.Request(url,headers={'User-Agent':'Mozilla/5.0'}),timeout=8) as r:
        d=_j.loads(r.read())
        for t in d.get('trc20TransferInfo',[]):
          if t.get('to_address','').lower()==addr.lower():
            amt=float(t.get('amount_str','0'))/1e6
            if amt>=expected*0.95: return True,amt
    elif coin=='BTC':
      url=f"https://blockstream.info/api/tx/{tx_hash}"
      with urllib.request.urlopen(urllib.request.Request(url,headers={'User-Agent':'Mozilla/5.0'}),timeout=8) as r:
        d=_j.loads(r.read())
        for out in d.get('vout',[]):
          if out.get('scriptpubkey_address','').lower()==addr.lower(): return True,out.get('value',0)/1e8
    elif coin=='ETH':
      url=f"https://api.etherscan.io/api?module=proxy&action=eth_getTransactionByHash&txhash={tx_hash}"
      with urllib.request.urlopen(urllib.request.Request(url,headers={'User-Agent':'Mozilla/5.0'}),timeout=8) as r:
        d=_j.loads(r.read()); res=d.get('result',{})
        if res and res.get('to','').lower()==addr.lower(): return True,int(res.get('value','0x0'),16)/1e18
    elif coin=='LTC':
      url=f"https://api.blockcypher.com/v1/ltc/main/txs/{tx_hash}"
      with urllib.request.urlopen(urllib.request.Request(url,headers={'User-Agent':'Mozilla/5.0'}),timeout=8) as r:
        d=_j.loads(r.read())
        for out in d.get('outputs',[]):
          if addr in out.get('addresses',[]): return True,out.get('value',0)/1e8
  except: pass
  return False,0

# ━━━ SUBMIT PAYMENT ━━━
@app.route('/submit-payment',methods=['POST'])
def submit_payment():
  if not session.get('user_id'):
    flash('Login করুন','error'); return redirect('/login')
  uid=session['user_id']; plan=request.form.get('plan',''); coin=request.form.get('coin',''); tx=request.form.get('tx_hash','').strip()
  if not tx: flash('Transaction hash দিন','error'); return redirect('/premium')
  db=get_db()
  dup=db.execute("SELECT id FROM payment_requests WHERE tx_hash=?",(tx,)).fetchone()
  if dup: db.close(); flash('এই TX আগেই submit হয়েছে!','error'); return redirect('/premium')
  verified,amount=auto_verify_tx(coin,tx,plan)
  if verified:
    db.execute("INSERT INTO payment_requests(user_id,plan,coin,tx_hash,status,amount) VALUES(?,?,?,?,'approved',?)",(uid,plan,coin,tx,str(amount)))
    db.execute("UPDATE users SET is_premium=1,premium_note=? WHERE id=?",(plan,uid))
    db.execute("INSERT INTO notifications(user_id,message,link) VALUES(?,?,?)",(uid,'Payment verified! আপনি এখন Premium Member!','/premium'))
    db.commit(); db.close()
    flash('Payment auto-verified! আপনি এখন Premium! ','success')
  else:
    db.execute("INSERT INTO payment_requests(user_id,plan,coin,tx_hash,status) VALUES(?,?,?,?,'pending')",(uid,plan,coin,tx))
    for a in db.execute("SELECT id FROM users WHERE is_admin=1").fetchall():
      db.execute("INSERT INTO notifications(user_id,message,link) VALUES(?,?,?)",(a['id'],f"Payment verify করুন: {session.get('user')} ({coin} {plan})",'/admin/payments'))
    db.commit(); db.close()
    flash('Submitted! Blockchain verify হচ্ছে... Admin confirm করবে শীঘ্রই ⏳','success')
  return redirect('/premium')

# ━━━ ADMIN PAYMENTS ━━━
@app.route('/admin/payments')
def admin_payments():
  if not session.get('is_admin'): return redirect('/')
  db=get_db()
  reqs=db.execute("SELECT pr.*,u.username,u.email FROM payment_requests pr JOIN users u ON pr.user_id=u.id ORDER BY pr.created_at DESC").fetchall()
  db.close()
  def _row(r):
    status_col={'pending':'#ffd700','approved':'#3fb950','rejected':'#f85149'}.get(r['status'],'#7d8590')
    btns=''
    if r['status']=='pending':
      btns=f'<a href="/admin/approve-payment/{r["id"]}" class="btn btn-g" style="font-size:11px;padding:4px 10px;">Approve</a> <a href="/admin/reject-payment/{r["id"]}" class="btn btn-r" style="font-size:11px;padding:4px 10px;">Reject</a>'
    return f'<tr><td>{r["id"]}</td><td><a href="/profile/{r["username"]}" style="color:var(--p);">{r["username"]}</a></td><td style="color:#ffd700;">{r["plan"]}</td><td style="color:{status_col};">{r["coin"]}</td><td style="font-family:monospace;font-size:11px;max-width:200px;overflow:hidden;text-overflow:ellipsis;" title="{r["tx_hash"]}">{r["tx_hash"][:20]}...</td><td style="color:{status_col};font-weight:700;">{r["status"].upper()}</td><td>{r["created_at"][:16]}</td><td>{btns}</td></tr>'
  rows=''.join(_row(r) for r in reqs) or '<tr><td colspan=8 style="text-align:center;color:var(--dim);padding:24px;">No payment requests</td></tr>'
  c=f'''<div style="max-width:1000px;margin:0 auto;">
<div style="font-size:22px;font-weight:800;margin-bottom:18px;">&#128200; Payment Requests</div>
<div class="card" style="overflow-x:auto;">
<table class="at">
<thead><tr><th>ID</th><th>User</th><th>Plan</th><th>Coin</th><th>TX Hash</th><th>Status</th><th>Date</th><th>Action</th></tr></thead>
<tbody>{rows}</tbody>
</table>
</div>
</div>'''
  return base(c,"Payments",session.get('theme','cyan'))

@app.route('/admin/approve-payment/<int:rid>')
def approve_payment(rid):
  if not session.get('is_admin'): return redirect('/')
  db=get_db()
  req=db.execute("SELECT * FROM payment_requests WHERE id=?",(rid,)).fetchone()
  if req:
    db.execute("UPDATE payment_requests SET status='approved' WHERE id=?",(rid,))
    db.execute("UPDATE users SET is_premium=1,premium_note=? WHERE id=?",(req['plan'],req['user_id']))
    db.execute("INSERT INTO notifications(user_id,message,link) VALUES(?,?,?)",(req['user_id'],'&#128142; আপনার Premium approved হয়েছে! Welcome to Premium!','/premium'))
    db.commit()
    flash(f'Payment approved! User is now Premium ','success')
  db.close()
  return redirect('/admin/payments')

@app.route('/admin/reject-payment/<int:rid>')
def reject_payment(rid):
  if not session.get('is_admin'): return redirect('/')
  db=get_db()
  req=db.execute("SELECT * FROM payment_requests WHERE id=?",(rid,)).fetchone()
  if req:
    db.execute("UPDATE payment_requests SET status='rejected' WHERE id=?",(rid,))
    db.execute("INSERT INTO notifications(user_id,message,link) VALUES(?,?,?)",(req['user_id'],'&#10060; আপনার payment request reject হয়েছে। সঠিক TX hash দিয়ে আবার try করুন।','/premium'))
    db.commit()
    flash('Payment rejected','success')
  db.close()
  return redirect('/admin/payments')


# ━━━ ANNOUNCEMENTS ━━━
@app.route('/announcements')
def announcements():
  db=get_db()
  ads=db.execute("SELECT * FROM ads WHERE active=1 ORDER BY created_at DESC").fetchall()
  db.close()
  def _ann(a):
    return f'''<div class="card">
<div style="display:flex;justify-content:space-between;align-items:flex-start;gap:10px;flex-wrap:wrap;">
 <div style="font-size:16px;font-weight:700;color:var(--p);">📢 {a["title"]}</div>
 <div style="font-size:10px;color:var(--dim);font-family:monospace;flex-shrink:0;">{a["created_at"][:10]}</div>
</div>
<div style="font-size:13px;color:var(--text);margin:8px 0;line-height:1.6;">{a["content"]}</div>
{f'<a href="{a["url"]}" target="_blank" class="btn btn-o" style="font-size:12px;">🔗 Learn more</a>' if a["url"] else ""}
</div>'''
  rows=''.join(_ann(a) for a in ads) or '<div style="text-align:center;padding:48px;color:var(--dim);"><div style="font-size:48px;margin-bottom:10px;">📢</div><div>No announcements yet.</div></div>'
  c=f'''<div style="max-width:760px;margin:0 auto;">
<div style="text-align:center;padding:24px 0 20px;">
 <div style="font-size:36px;margin-bottom:8px;">📢</div>
 <div style="font-size:24px;font-weight:800;color:var(--text);">Announcements</div>
 <div style="font-size:13px;color:var(--dim);margin-top:4px;">Latest news from ZeroShell</div>
</div>
{rows}
<div style="text-align:center;margin-top:20px;">
 <a href="https://t.me/ZeroShell_help" target="_blank" class="btn" style="background:#229ed9;color:#fff;border-color:#229ed9;font-size:13px;padding:8px 20px;">
  <svg width="14" height="14" viewBox="0 0 24 24" fill="white" style="flex-shrink:0;"><path d="M12 0C5.373 0 0 5.373 0 12s5.373 12 12 12 12-5.373 12-12S18.627 0 12 0zm5.894 8.221l-1.97 9.28c-.145.658-.537.818-1.084.508l-3-2.21-1.447 1.394c-.16.16-.295.295-.605.295l.213-3.053 5.56-5.023c.242-.213-.054-.333-.373-.12l-6.871 4.326-2.962-.924c-.643-.204-.657-.643.136-.953l11.57-4.461c.537-.194 1.006.131.833.941z"/></svg>
  Join Telegram for live updates
 </a>
</div>
</div>'''
  return base(c,"Announcements",session.get('theme','cyan'))


# ━━━ PREMIUM PAGE ━━━
@app.route('/premium')
def premium_page():
  uid=session.get('user_id')
  is_prem=False
  if uid:
    db=get_db(); u=db.execute("SELECT is_premium,premium_note FROM users WHERE id=?",(uid,)).fetchone(); db.close()
    is_prem = u and u['is_premium']
  db2=get_db(); prem_count=db2.execute("SELECT COUNT(*) FROM users WHERE is_premium=1").fetchone()[0]; db2.close()

  plans=[
    {"label":"3 MONTHS","price":"$20","period":"/ 3 months","dur":"3 Months","color":"#3fb950","icon":"plant","perks":["VIP Badge","10 posts/day","Glowing pastes","5 profile links","Premium banner"]},
    {"label":"6 MONTHS","price":"$40","period":"/ 6 months","dur":"6 Months","color":"#00f5ff","icon":"bolt","perks":["VIP Badge","10 posts/day","Glowing pastes","5 profile links","Premium banner","Save $20!"],"pop":True},
    {"label":"1 YEAR","price":"$60","period":"/ year","dur":"1 Year","color":"#ffd700","icon":"crown","perks":["VIP Badge","10 posts/day","Glowing pastes","5 profile links","Premium banner","Best Value!"]},
    {"label":"LIFETIME","price":"$80","period":"/ forever","dur":"Lifetime","color":"#ff4d94","icon":"star","perks":["VIP Badge FOREVER","10 posts/day","Glowing pastes","5 profile links","Premium banner","Never pay again!"]},
  ]

  def plan_card(p):
    pop=p.get('pop',False)
    pop_badge='<div style="position:absolute;top:-13px;left:50%;transform:translateX(-50%);background:var(--p);color:#000;font-size:10px;font-weight:800;padding:3px 14px;border-radius:99px;letter-spacing:1px;white-space:nowrap;">MOST POPULAR</div>' if pop else ''
    ic={'plant':'&#127807;','bolt':'&#9889;','crown':'&#128081;','star':'&#9733;'}.get(p['icon'],'&#9733;')
    perks=''.join(f'<div style="display:flex;align-items:center;gap:8px;padding:5px 0;font-size:13px;"><span style="color:{p["color"]};">&#10003;</span> {k}</div>' for k in p['perks'])
    bdr=f'border-color:{p["color"]};box-shadow:0 0 24px {p["color"]}22;' if pop else ''
    return f'<div style="position:relative;background:var(--card);border:2px solid var(--border);{bdr}border-radius:14px;padding:28px 22px;text-align:center;transition:transform .2s;" onmouseover="this.style.transform=\'translateY(-4px)\'" onmouseout="this.style.transform=\'translateY(0)\'">{pop_badge}<div style="font-size:36px;margin-bottom:8px;">{ic}</div><div style="font-size:11px;font-weight:800;color:{p["color"]};letter-spacing:2px;text-transform:uppercase;margin-bottom:10px;">{p["label"]}</div><div style="font-size:44px;font-weight:800;color:var(--text);line-height:1;">{p["price"]}</div><div style="font-size:12px;color:var(--dim);margin-bottom:18px;">{p["period"]}</div><div style="border-top:1px solid var(--border);padding-top:14px;margin-bottom:18px;text-align:left;">{perks}</div><a href="https://t.me/ZeroShell_help" target="_blank" class="btn" style="width:100%;justify-content:center;background:{p["color"]};color:#000;border-color:{p["color"]};font-weight:800;font-size:13px;padding:10px;display:flex;">Get {p["dur"]}</a></div>'

  cards=''.join(plan_card(p) for p in plans)

  # coin cards
  COINS=[('USDT','TBWUnddB2J5cckALZenPo6KQJwLzysEohE','TRC20 (Tron)','#26a17b'),('BTC','1N39KVvVK8itaGr7odbrTKnBdbwt4n7PoY','Bitcoin','#f7931a'),('ETH','0xd4c1ff57a77ce3a7b99ff96b410f05501b84b838','ERC20','#627eea'),('LTC','LcU6RqsSHQ8XUUP6xDEWDBWUts8wUe5adf','Litecoin','#bfbbbb')]
  coin_cards=''.join(
    '<div style="background:var(--card);border:1px solid var(--border);border-top:3px solid '+cl+';border-radius:12px;padding:16px;text-align:center;">'
    '<div style="font-size:14px;font-weight:800;color:'+cl+';margin-bottom:10px;">'+cn+'</div>'
    '<img src="https://api.qrserver.com/v1/create-qr-code/?size=140x140&data='+addr+'" style="width:120px;height:120px;border-radius:6px;background:#fff;padding:5px;margin-bottom:8px;" loading="lazy">'
    '<div style="font-size:10px;color:var(--dim);margin-bottom:6px;">'+net+'</div>'
    '<div style="background:var(--bg);border:1px solid var(--border);border-radius:6px;padding:6px;font-family:monospace;font-size:10px;word-break:break-all;margin-bottom:8px;">'+addr+'</div>'
    '<button onclick="navigator.clipboard.writeText(\''+addr+'\').then(()=>{this.textContent=\'Copied!\';setTimeout(()=>this.textContent=\'Copy\',1500)})" style="background:'+cl+';color:#000;border:none;border-radius:6px;padding:6px 14px;font-size:12px;font-weight:700;cursor:pointer;width:100%;">Copy</button></div>'
    for cn,addr,net,cl in COINS)

  already=''
  if is_prem:
    already='<div style="background:linear-gradient(135deg,#3d2b00,#5a3f00);border:2px solid #ffd700;border-radius:14px;padding:18px 22px;margin-bottom:24px;display:flex;align-items:center;gap:16px;"><div style="font-size:36px;">&#128081;</div><div><div style="font-weight:800;color:#ffd700;font-size:17px;">You are a Premium Member!</div><div style="font-size:13px;color:rgba(255,255,255,.7);margin-top:3px;">Thank you for your support!</div></div></div>'

  pay_form=''
  if uid and not is_prem:
    pay_form='''<div id="pay" style="background:linear-gradient(135deg,#0a1520,#0d2035);border:1px solid rgba(0,245,255,.2);border-radius:16px;padding:24px;margin-bottom:22px;">
<div style="font-size:18px;font-weight:800;color:#fff;margin-bottom:6px;">Submit Payment</div>
<div style="font-size:13px;color:rgba(255,255,255,.55);margin-bottom:18px;">Address এ pay করুন, TxID দিন, auto verify হবে!</div>
<form method="POST" action="/submit-payment">
<div style="display:grid;grid-template-columns:1fr 1fr;gap:12px;margin-bottom:12px;">
<div><label style="color:rgba(255,255,255,.6);font-size:11px;font-weight:700;display:block;margin-bottom:5px;letter-spacing:.5px;">PLAN</label>
<select name="plan" style="background:#0a1525;border:1px solid rgba(0,245,255,.25);border-radius:8px;color:#fff;padding:9px 12px;width:100%;font-size:13px;outline:none;">
<option value="3month">3 Months - $20</option>
<option value="6month">6 Months - $40</option>
<option value="1year">1 Year - $60</option>
</select></div>
<div><label style="color:rgba(255,255,255,.6);font-size:11px;font-weight:700;display:block;margin-bottom:5px;letter-spacing:.5px;">COIN</label>
<select name="coin" style="background:#0a1525;border:1px solid rgba(0,245,255,.25);border-radius:8px;color:#fff;padding:9px 12px;width:100%;font-size:13px;outline:none;">
<option value="USDT">USDT (TRC20)</option>
<option value="BTC">BTC (Bitcoin)</option>
<option value="ETH">ETH (ERC20)</option>
<option value="LTC">LTC (Litecoin)</option>
</select></div>
</div>
<div style="margin-bottom:14px;"><label style="color:rgba(255,255,255,.6);font-size:11px;font-weight:700;display:block;margin-bottom:5px;letter-spacing:.5px;">TRANSACTION HASH / TxID</label>
<input type="text" name="tx_hash" placeholder="Paste your TxID here..." required style="background:#0a1525;border:1px solid rgba(0,245,255,.25);border-radius:8px;color:#fff;padding:10px 14px;width:100%;font-size:13px;outline:none;">
<div style="font-size:11px;color:rgba(255,255,255,.35);margin-top:5px;">Blockchain explorer থেকে TxID copy করুন</div>
</div>
<button type="submit" style="width:100%;padding:13px;background:linear-gradient(135deg,#00c8ff,#0066cc);color:#fff;border:none;border-radius:10px;font-size:15px;font-weight:800;cursor:pointer;letter-spacing:.5px;">Verify &amp; Get Premium</button>
</form></div>'''
  login_note='' if uid else '<div style="background:#1a2030;border:1px solid #ffd70044;border-radius:10px;padding:12px 16px;margin-bottom:16px;font-size:13px;color:#ffd700;"><a href="/login" style="color:#ffd700;font-weight:800;">Login করুন</a> তারপর payment submit করুন।</div>'
  c=f'''<div style="max-width:1000px;margin:0 auto;padding:20px 0 40px;">

<div style="text-align:center;padding:28px 20px 22px;margin-bottom:24px;">
<div style="font-size:11px;font-weight:800;color:#ffd700;letter-spacing:4px;text-transform:uppercase;margin-bottom:8px;">ZEROSHELL PREMIUM</div>
<div style="font-size:32px;font-weight:900;color:#fff;margin-bottom:6px;">Upgrade Your Account</div>
<div style="font-size:14px;color:rgba(255,255,255,.4);">{prem_count} active premium members</div>
</div>

{already}

<!-- 4 plan cards in 2x2 grid -->
<div style="display:grid;grid-template-columns:repeat(2,1fr);gap:16px;margin-bottom:32px;">
{cards}
</div>

<!-- 4 crypto QR cards in one row -->
<div style="font-size:18px;font-weight:800;color:#fff;margin-bottom:14px;padding-bottom:10px;border-bottom:1px solid rgba(255,255,255,.08);">Pay with Crypto</div>
<div style="display:grid;grid-template-columns:repeat(4,1fr);gap:14px;margin-bottom:24px;">
{coin_cards}
</div>

<!-- Payment form full width below -->
<div style="background:linear-gradient(135deg,#0a1520,#0d2035);border:1px solid rgba(0,245,255,.15);border-radius:14px;padding:22px;margin-bottom:24px;">
  <div style="font-size:16px;font-weight:800;color:#fff;margin-bottom:16px;">Submit Payment</div>
  {login_note}
  {pay_form}
</div>

<!-- How it works -->
<div style="background:rgba(0,245,255,.04);border:1px solid rgba(0,245,255,.12);border-radius:12px;padding:18px;margin-bottom:20px;">
  <div style="font-size:14px;font-weight:800;color:#00f5ff;margin-bottom:14px;">How it works</div>
  <div style="display:grid;grid-template-columns:repeat(4,1fr);gap:12px;text-align:center;">
    <div style="padding:14px;background:rgba(255,255,255,.04);border-radius:10px;"><div style="font-size:22px;font-weight:900;color:#fff;margin-bottom:6px;">1</div><div style="font-size:12px;font-weight:700;color:#fff;margin-bottom:3px;">Plan বেছে নিন</div><div style="font-size:11px;color:rgba(255,255,255,.4);">3M / 6M / 1Y / LT</div></div>
    <div style="padding:14px;background:rgba(255,255,255,.04);border-radius:10px;"><div style="font-size:22px;font-weight:900;color:#fff;margin-bottom:6px;">2</div><div style="font-size:12px;font-weight:700;color:#fff;margin-bottom:3px;">Crypto পাঠান</div><div style="font-size:11px;color:rgba(255,255,255,.4);">QR scan বা address copy</div></div>
    <div style="padding:14px;background:rgba(255,255,255,.04);border-radius:10px;"><div style="font-size:22px;font-weight:900;color:#fff;margin-bottom:6px;">3</div><div style="font-size:12px;font-weight:700;color:#fff;margin-bottom:3px;">TxID দিন</div><div style="font-size:11px;color:rgba(255,255,255,.4);">Form এ paste করুন</div></div>
    <div style="padding:14px;background:rgba(0,245,255,.08);border-radius:10px;border:1px solid rgba(0,245,255,.2);"><div style="font-size:18px;font-weight:900;color:#00f5ff;margin-bottom:6px;">Auto</div><div style="font-size:12px;font-weight:800;color:#00f5ff;margin-bottom:3px;">Verified!</div><div style="font-size:11px;color:rgba(255,255,255,.4);">Instant Premium</div></div>
  </div>
  <div style="text-align:center;margin-top:12px;font-size:11px;color:rgba(255,255,255,.25);">Help: <a href="https://t.me/ZeroShell_help" target="_blank" style="color:#229ed9;">@ZeroShell_help</a></div>
</div>
</div>'''
  return base(c,"Premium",session.get('theme','cyan'))

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
        for l in al[i1:i2]: html_lines.append(f'<span class="diff-eq"> {__import__("html").escape(l) or " "}</span>'); same+=1
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

  # sidebar
  try:
    _db=get_db()
    _hot=_db.execute("SELECT slug,title,views,likes FROM pastes WHERE visibility='public' ORDER BY views DESC LIMIT 5").fetchall()
    _top=_db.execute("SELECT username,avatar,total_views FROM users ORDER BY total_views DESC LIMIT 5").fetchall()
    _db.close()
  except: _hot=[]; _top=[]
  hot_html=''
  for _p in _hot:
    hot_html+=f'<div style="padding:6px 0;border-bottom:1px solid var(--border);"><a href="/paste/{_p["slug"]}" style="color:var(--p);text-decoration:none;font-size:13px;font-weight:600;display:block;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;">{_p["title"]}</a><span style="font-size:11px;color:var(--dim);">👁 {_p["views"]} ❤️ {_p["likes"]}</span></div>'
  top_html=''
  for _u in _top:
    top_html+=f'<div style="display:flex;align-items:center;gap:8px;padding:6px 0;border-bottom:1px solid var(--border);"><span style="font-size:17px;">{_u["avatar"] or "👤"}</span><a href="/profile/{_u["username"]}" style="color:var(--p);text-decoration:none;font-size:13px;font-weight:600;flex:1;">{_u["username"]}</a><span style="font-family:monospace;color:var(--green);font-size:12px;">👁 {_u["total_views"]}</span></div>'
  tl_html=''
  tl_html_v=''
  for t in ALL_TAGS:
    active_class='active' if tag==t else ''
    tl_html+=f'<a href="/?tag={t}" class="tag {active_class}">{t}</a>'
    bc='var(--p)' if tag==t else 'transparent'
    tc='var(--p)' if tag==t else 'var(--t)'
    tl_html_v+=f'<a href="/?tag={t}" style="display:block;padding:6px 14px;color:{tc};text-decoration:none;font-size:12px;font-weight:600;border-left:3px solid {bc};">{t}</a>'
  sidebar=f'''<div style="display:flex;flex-direction:column;gap:12px;width:280px;flex-shrink:0;">
<div class="card" style="padding:14px;">
 <div style="font-size:11px;font-weight:700;color:var(--dim);letter-spacing:.7px;text-transform:uppercase;margin-bottom:10px;padding-bottom:7px;border-bottom:1px solid var(--border);"> Trending Pastes</div>
 {hot_html or '<p style="color:var(--dim);font-size:13px;">No pastes yet</p>'}
</div>
<div class="card" style="padding:14px;">
 <div style="font-size:11px;font-weight:700;color:var(--dim);letter-spacing:.7px;text-transform:uppercase;margin-bottom:10px;padding-bottom:7px;border-bottom:1px solid var(--border);">🏆 Top Users</div>
 {top_html or '<p style="color:var(--dim);font-size:13px;">No users yet</p>'}
 <a href="/leaderboard" style="display:block;text-align:center;margin-top:10px;color:var(--p);font-size:12px;font-weight:600;text-decoration:none;">View all →</a>
</div>
<div class="card" style="padding:14px;">
 <div style="font-size:11px;font-weight:700;color:var(--dim);letter-spacing:.7px;text-transform:uppercase;margin-bottom:10px;padding-bottom:7px;border-bottom:1px solid var(--border);">📊 Stats</div>
 <div style="display:grid;grid-template-columns:1fr 1fr;gap:7px;">
  <div class="sb"><span class="sn" style="color:var(--p);font-size:18px;">{tp}</span><span class="sl">Pastes</span></div>
  <div class="sb"><span class="sn" style="color:var(--green);font-size:18px;">{tv}</span><span class="sl">Views</span></div>
  <div class="sb"><span class="sn" style="color:var(--yellow);font-size:18px;">{tu}</span><span class="sl">Users</span></div>
  <div class="sb"><span class="sn" style="color:var(--dim);font-size:11px;">v7.5</span><span class="sl">Version</span></div>
 </div>
</div>
<div class="card" style="padding:14px;">
 <div style="font-size:11px;font-weight:700;color:var(--dim);letter-spacing:.7px;text-transform:uppercase;margin-bottom:10px;padding-bottom:7px;border-bottom:1px solid var(--border);">⚡ Quick</div>
 <div style="display:flex;flex-direction:column;gap:5px;">
  <a href="/new" class="btn btn-p" style="justify-content:center;">📝 New Paste</a>
  <a href="/diff" class="btn btn-o" style="justify-content:center;">🔀 Diff Tool</a>
  <a href="/feed" class="btn btn-o" style="justify-content:center;">📊 Activity</a>
 </div>
</div>
</div>'''
  c=f'''<div style="display:grid;grid-template-columns:1fr 1fr;gap:12px;margin-bottom:18px;margin-top:16px;">
<a href="https://t.me/ZeroShell_Store" target="_blank" style="display:flex;align-items:center;justify-content:center;gap:10px;padding:16px 20px;background:linear-gradient(135deg,#1a1f2e,#0d1520);border:1px solid #229ed944;border-radius:12px;text-decoration:none;transition:all .2s;" onmouseover="this.style.borderColor='#229ed9';this.style.transform='translateY(-2px)'" onmouseout="this.style.borderColor='#229ed944';this.style.transform='translateY(0)'">
<svg width="22" height="22" viewBox="0 0 24 24" fill="#229ed9"><path d="M12 0C5.373 0 0 5.373 0 12s5.373 12 12 12 12-5.373 12-12S18.627 0 12 0zm5.894 8.221l-1.97 9.28c-.145.658-.537.818-1.084.508l-3-2.21-1.447 1.394c-.16.16-.295.295-.605.295l.213-3.053 5.56-5.023c.242-.213-.054-.333-.373-.12l-6.871 4.326-2.962-.924c-.643-.204-.657-.643.136-.953l11.57-4.461c.537-.194 1.006.131.833.94z"/></svg>
<div><div style="font-size:14px;font-weight:800;color:#fff;">ZeroShell Store</div><div style="font-size:11px;color:#229ed9;">t.me/ZeroShell_Store</div></div>
</a>
<a href="https://t.me/ZeroShell_Shop" target="_blank" style="display:flex;align-items:center;justify-content:center;gap:10px;padding:16px 20px;background:linear-gradient(135deg,#1a1f2e,#0d1520);border:1px solid #229ed944;border-radius:12px;text-decoration:none;transition:all .2s;" onmouseover="this.style.borderColor='#229ed9';this.style.transform='translateY(-2px)'" onmouseout="this.style.borderColor='#229ed944';this.style.transform='translateY(0)'">
<svg width="22" height="22" viewBox="0 0 24 24" fill="#229ed9"><path d="M12 0C5.373 0 0 5.373 0 12s5.373 12 12 12 12-5.373 12-12S18.627 0 12 0zm5.894 8.221l-1.97 9.28c-.145.658-.537.818-1.084.508l-3-2.21-1.447 1.394c-.16.16-.295.295-.605.295l.213-3.053 5.56-5.023c.242-.213-.054-.333-.373-.12l-6.871 4.326-2.962-.924c-.643-.204-.657-.643.136-.953l11.57-4.461c.537-.194 1.006.131.833.94z"/></svg>
<div><div style="font-size:14px;font-weight:800;color:#fff;">ZeroShell Shop</div><div style="font-size:11px;color:#229ed9;">t.me/ZeroShell_Shop</div></div>
</a>
</div>
<div style="display:grid;grid-template-columns:200px 1fr 280px;gap:16px;align-items:start;">

<!-- LEFT SIDEBAR -->
<div style="display:flex;flex-direction:column;gap:8px;position:sticky;top:70px;">
  <a href="/new" style="display:flex;align-items:center;justify-content:center;gap:7px;padding:10px 14px;background:var(--p);color:#000;border-radius:9px;font-weight:800;font-size:14px;text-decoration:none;transition:opacity .15s;" onmouseover="this.style.opacity='.85'" onmouseout="this.style.opacity='1'">
    + New Paste
  </a>
  <div style="background:var(--card);border:1px solid var(--bd);border-radius:10px;overflow:hidden;margin-top:4px;">
    <div style="font-size:10px;font-weight:800;color:var(--s);text-transform:uppercase;letter-spacing:1px;padding:10px 14px 6px;">Menu</div>
    <a href="/" style="display:flex;align-items:center;gap:8px;padding:9px 14px;color:var(--t);text-decoration:none;font-size:13px;font-weight:600;border-left:3px solid {"var(--p)" if not tag else "transparent"};background:{"rgba(128,128,128,.06)" if not tag else "transparent"};" onmouseover="this.style.background='rgba(128,128,128,.06)'" onmouseout="this.style.background='{"rgba(128,128,128,.06)" if not tag else "transparent"}'">
      <span style="font-size:15px;">&#9776;</span> Home
    </a>
    <a href="/pastes" style="display:flex;align-items:center;gap:8px;padding:9px 14px;color:var(--t);text-decoration:none;font-size:13px;font-weight:600;" onmouseover="this.style.background='rgba(128,128,128,.06)'" onmouseout="this.style.background='transparent'">
      <span style="font-size:15px;">&#128196;</span> Archive
    </a>
    <a href="/leaderboard" style="display:flex;align-items:center;gap:8px;padding:9px 14px;color:var(--t);text-decoration:none;font-size:13px;font-weight:600;" onmouseover="this.style.background='rgba(128,128,128,.06)'" onmouseout="this.style.background='transparent'">
      <span style="font-size:15px;">&#127942;</span> Board
    </a>
    <a href="/search" style="display:flex;align-items:center;gap:8px;padding:9px 14px;color:var(--t);text-decoration:none;font-size:13px;font-weight:600;" onmouseover="this.style.background='rgba(128,128,128,.06)'" onmouseout="this.style.background='transparent'">
      <span style="font-size:15px;">&#128269;</span> Search
    </a>
    {'<a href="/bookmarks" style="display:flex;align-items:center;gap:8px;padding:9px 14px;color:var(--t);text-decoration:none;font-size:13px;font-weight:600;"><span style="font-size:15px;">&#9733;</span> Saved</a>' if session.get("user_id") else ""}
  </div>
  <div style="background:var(--card);border:1px solid var(--bd);border-radius:10px;overflow:hidden;">
    <div style="font-size:10px;font-weight:800;color:var(--s);text-transform:uppercase;letter-spacing:1px;padding:10px 14px 6px;">Filter</div>
    {tl_html_v}
  </div>
</div>

<!-- MAIN CONTENT -->
<div>
  <div style="font-size:12px;font-weight:700;color:var(--s);letter-spacing:.7px;text-transform:uppercase;margin-bottom:10px;">Recent Pastes{f" · #{tag}" if tag else ""}</div>
  {pl}
</div>

<!-- RIGHT SIDEBAR -->
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
  def _lbrow(i,u2):
    medals2=['🥇','🥈','🥉']; mc2=['#ffd700','#c0c0c0','#cd7f32']
    rank2=medals2[i] if i<3 else '#'+str(i+1); rc2=mc2[i] if i<3 else 'var(--dim)'; bc2='border-color:'+mc2[i]+'44;' if i<3 else ''
    return '<div style="display:flex;align-items:center;gap:14px;padding:13px 16px;background:var(--bg);border:1px solid var(--border);'+bc2+'border-radius:10px;margin-bottom:8px;"><div style="font-size:20px;width:34px;text-align:center;font-weight:700;color:'+rc2+';">'+rank2+'</div><div style="font-size:26px;">'+(u2["avatar"] or "👤")+'</div><div style="flex:1;min-width:0;"><a href="/profile/'+u2["username"]+'" style="color:var(--p);text-decoration:none;font-size:15px;font-weight:700;">'+u2["username"]+'</a><div style="font-size:12px;color:var(--dim);">'+str(u2["pc"])+' pastes</div></div><div style="text-align:right;"><div style="font-family:monospace;color:var(--green);font-size:16px;font-weight:700;">👁 '+str(u2["total_views"])+'</div></div></div>'
  rows=''.join(_lbrow(i,u) for i,u in enumerate(users)) or f'<div style="text-align:center;padding:48px;color:var(--dim);"><div style="font-size:52px;margin-bottom:12px;">🏆</div><div>No users yet! <a href="/register" style="color:var(--p);">Register →</a></div></div>'
  c=f'''<div style="max-width:680px;margin:0 auto;">
<div style="text-align:center;padding:24px 0 20px;">
<div style="font-size:44px;margin-bottom:8px;">🏆</div>
<div style="font-size:26px;font-weight:800;color:var(--text);">Leaderboard</div>
<div style="font-size:13px;color:var(--dim);margin-top:4px;">Top users by total paste views</div>
</div>
<div class="card">{rows}</div>
</div>'''
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
function cp(){{navigator.clipboard.writeText(document.getElementById('pc').innerText).then(()=>toast('Copied!'));}}
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
  all_b=[('Active','🏃','#00f5ff',p30>=5),('Popular','','#ff2d55',user['total_views']>=1000),('Famous','⚡','#ff6b00',user['total_views']>=5000),('Legendary','👑','#ffd700',user['total_views']>=10000)]
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
  # badge progress
  badges=[
    ('Active','#00f5ff',p30,15,'Pastes last 30 days'),
    ('Popular','#ff2d55',user['total_views'],1000,'Views last 30 days'),
    ('Famous','#ff6b00',user['total_views'],5000,'Total views'),
    ('Legendary','#ffd700',user['total_views'],10000,'All Badges Together'),
  ]
  def badge_card(name,col,val,target,desc):
    done=val>=target
    pct=min(100,int(val/target*100)) if target else 100
    op='1' if done else '.5'
    return (f'<div style="background:var(--card);border:1px solid {col}{"44" if done else "18"};border-radius:12px;padding:18px;opacity:{op};">'
      f'<div style="display:flex;justify-content:space-between;margin-bottom:6px;">'
      f'<div style="font-size:15px;font-weight:800;color:{col};">{name}</div>'
      f'<div style="font-size:10px;color:rgba(255,255,255,.4);">{desc}</div></div>'
      f'<div style="font-size:24px;font-weight:900;color:#fff;">{val}<span style="font-size:13px;color:rgba(255,255,255,.3);">/{target}</span></div>'
      f'<div style="background:rgba(255,255,255,.08);border-radius:99px;height:4px;margin-top:10px;">'
      f'<div style="background:{col};width:{pct}%;height:4px;border-radius:99px;"></div>'
      f'</div></div>')
  badge_cards=''.join(badge_card(*b) for b in badges)
  # social links
  _ud=dict(user); links_raw=[_ud.get('link1','') or '',_ud.get('link2','') or '',_ud.get('link3','') or '',_ud.get('link4','') or '',_ud.get('link5','') or '']
  def mk_link(url):
    if not url: return ''
    ul=url.lower()
    if 'github' in ul: lbl='GitHub'
    elif 't.me' in ul or 'telegram' in ul: lbl='Telegram'
    elif 'twitter' in ul or 'x.com' in ul: lbl='Twitter/X'
    elif 'youtube' in ul: lbl='YouTube'
    elif 'instagram' in ul: lbl='Instagram'
    elif 'linkedin' in ul: lbl='LinkedIn'
    else:
      try:
        from urllib.parse import urlparse; lbl=urlparse(url).netloc or url[:20]
      except: lbl=url[:20]
    return (f'<a href="{url}" target="_blank" rel="noopener" '
      f'style="display:flex;align-items:center;gap:8px;padding:9px 14px;'
      f'background:var(--bg);border:1px solid var(--bd);border-radius:8px;'
      f'font-size:13px;font-weight:600;color:var(--p);text-decoration:none;">'
      f'<svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">'
      f'<path d="M10 13a5 5 0 0 0 7.54.54l3-3a5 5 0 0 0-7.07-7.07l-1.72 1.71"/>'
      f'<path d="M14 11a5 5 0 0 0-7.54-.54l-3 3a5 5 0 0 0 7.07 7.07l1.71-1.71"/></svg>{lbl}</a>')
  links_html=''.join(mk_link(l) for l in links_raw if l)
  is_own=session.get('user')==username
  prem_banner=''
  if _ud.get('is_premium',0):
    prem_banner=(f'<div style="background:linear-gradient(135deg,#7b2ff7,#f107a3,#ffd700);'
      f'border-radius:10px;padding:10px 16px;margin-bottom:12px;display:flex;align-items:center;gap:10px;">'
      f'<div style="flex:1;"><div style="font-size:13px;font-weight:800;color:#fff;">PREMIUM MEMBER</div>'
      f'<div style="font-size:11px;color:rgba(255,255,255,.7);">{user["premium_note"] or "VIP"}</div></div>'
      f'<div style="font-size:16px;font-weight:900;color:#ffd700;border:2px solid #ffd700;border-radius:6px;padding:2px 10px;">VIP</div></div>')
  def menu_link(href,icon_path,label,danger=False):
    col='#ff453a' if danger else 'var(--t)'
    hov='rgba(255,69,58,.06)' if danger else 'rgba(128,128,128,.06)'
    return (f'<a href="{href}" style="display:flex;align-items:center;gap:10px;padding:10px 14px;'
      f'border-radius:8px;color:{col};text-decoration:none;font-size:13px;font-weight:600;transition:background .15s;" '
      f'onmouseover="this.style.background=\'{hov}\'" onmouseout="this.style.background=\'transparent\'">'
      f'<svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">{icon_path}</svg>{label}</a>')
  own_menu=''
  if is_own:
    own_menu=(
      '<div style="background:var(--card);border:1px solid var(--bd);border-radius:12px;overflow:hidden;padding:5px;">'
      +'<a href="/profile/'+username+'" style="display:flex;align-items:center;gap:10px;padding:10px 14px;border-radius:8px;background:rgba(128,128,128,.1);color:var(--p);text-decoration:none;font-size:13px;font-weight:700;">'
      +'<svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><rect x="3" y="3" width="7" height="7"/><rect x="14" y="3" width="7" height="7"/><rect x="3" y="14" width="7" height="7"/><rect x="14" y="14" width="7" height="7"/></svg>Dashboard</a>'
      +menu_link('/settings','<path d="M12 20h9"/><path d="M16.5 3.5a2.121 2.121 0 0 1 3 3L7 19l-4 1 1-4L16.5 3.5z"/>','Profile Settings')
      +menu_link('/new','<path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/><polyline points="14 2 14 8 20 8"/><line x1="12" y1="18" x2="12" y2="12"/><line x1="9" y1="15" x2="15" y2="15"/>','My Pastes')
      +menu_link('/api/v1/docs','<polyline points="16 18 22 12 16 6"/><polyline points="8 6 2 12 8 18"/>','API')
      +menu_link('/logout','<path d="M9 21H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h4"/><polyline points="16 17 21 12 16 7"/><line x1="21" y1="12" x2="9" y2="12"/>','Logout',danger=True)
      +'</div>'
    )
  follow_btn=(f'<a href="/follow/{username}" class="btn {'follow-btn following' if is_following else 'follow-btn'}" style="width:100%;justify-content:center;display:flex;padding:10px;margin-top:8px;">{"Unfollow" if is_following else "+ Follow"}</a>') if not is_own and session.get('user_id') else ''
  c=f'''<div style="max-width:1100px;margin:0 auto;display:grid;grid-template-columns:230px 1fr;gap:18px;align-items:start;">
<div style="position:sticky;top:70px;display:flex;flex-direction:column;gap:10px;">
{own_menu}{follow_btn}
</div>
<div style="display:flex;flex-direction:column;gap:14px;">
  <div style="background:var(--card);border:1px solid var(--bd);border-radius:14px;overflow:hidden;">
    <div style="height:72px;background:linear-gradient(135deg,{p}22,{p}06);"></div>
    <div style="padding:0 20px 20px;">
      <div style="display:flex;align-items:flex-end;gap:12px;margin-top:-30px;margin-bottom:10px;">
        <div style="width:60px;height:60px;background:var(--bg);border:3px solid var(--bd);border-radius:50%;display:flex;align-items:center;justify-content:center;font-size:28px;flex-shrink:0;">{av}</div>
        <div style="flex:1;padding-bottom:2px;">
          <div style="font-size:20px;font-weight:800;color:var(--p);">{username}</div>
          <div style="font-size:12px;color:var(--s);">{followers} followers · {following} following</div>
        </div>
        <div style="display:flex;gap:6px;">{fb}{eb}</div>
      </div>
      {prem_banner}
      {('<div style="font-size:13px;color:var(--s);margin-bottom:10px;">'+user["bio"]+'</div>') if user["bio"] else ''}
      {('<div style="display:flex;flex-direction:column;gap:6px;margin-bottom:10px;">'+links_html+'</div>') if links_html else ''}
      {tg}
    </div>
  </div>
  <div style="display:grid;grid-template-columns:1fr 1fr;gap:12px;">{badge_cards}</div>
  <div style="display:grid;grid-template-columns:1fr 1fr;gap:12px;">
    <div class="card"><div style="font-size:10px;font-weight:700;color:var(--p);letter-spacing:1px;margin-bottom:8px;">PASTES 7d</div><canvas id="pc2" height="100"></canvas></div>
    <div class="card"><div style="font-size:10px;font-weight:700;color:#00cc66;letter-spacing:1px;margin-bottom:8px;">VIEWS 7d</div><canvas id="vc2" height="100"></canvas></div>
  </div>
  <div class="card"><div style="font-size:11px;font-weight:700;color:var(--s);letter-spacing:1px;text-transform:uppercase;margin-bottom:10px;">Pastes ({len(pastes)})</div>{pl}</div>
</div></div>
<script>
const lb={lj},pd={pj},vd2={vj};
const co={{plugins:{{legend:{{display:false}}}},scales:{{x:{{ticks:{{color:"#4a6a80",font:{{size:8}}}},grid:{{color:"rgba(128,128,128,.07)"}}}},y:{{ticks:{{color:"#4a6a80",font:{{size:8}},stepSize:1}},grid:{{color:"rgba(128,128,128,.07)"}}}}}}}}; 
new Chart(document.getElementById('pc2'),{{type:'bar',data:{{labels:lb,datasets:[{{data:pd,backgroundColor:'{p}33',borderColor:'{p}',borderWidth:2,borderRadius:4}}]}},options:co}});
new Chart(document.getElementById('vc2'),{{type:'line',data:{{labels:lb,datasets:[{{data:vd2,backgroundColor:'#00cc6618',borderColor:'#00cc66',borderWidth:2,pointBackgroundColor:'#00cc66',tension:.4,fill:true}}]}},options:co}});
</script>'''
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
<div style="display:flex;gap:7px;"><input name="totp_code" placeholder="6-digit code" maxlength="6" style="max-width:140px;"><button type="submit" class="btn btn-p" style="font-size:11px;"> Verify</button></div>
</form></div>'''
  elif user['totp_enabled']:
    totp_html=f'''<div style="background:rgba(0,204,102,.05);border:1px solid rgba(0,204,102,.2);border-radius:8px;padding:12px;margin-top:10px;">
<div style="color:var(--green);font-size:12px;font-weight:700;margin-bottom:7px;"> 2FA is ENABLED</div>
<form method="POST"><input type="hidden" name="action" value="disable_2fa">
<button type="submit" class="btn btn-r" style="font-size:11px;" onclick="return confirm('Disable 2FA?')">🔓 Disable 2FA</button></form></div>'''
  else:
    totp_html=f'''<div style="background:rgba(255,45,85,.05);border:1px solid rgba(255,45,85,.2);border-radius:8px;padding:12px;margin-top:10px;">
<div style="color:var(--dim);font-size:11px;margin-bottom:7px;">2FA is disabled. Enable for extra security.</div>
<form method="POST"><input type="hidden" name="action" value="setup_2fa">
<button type="submit" class="btn btn-o" style="font-size:11px;">🔒 Setup 2FA</button></form></div>'''
  api_html=f'''<div style="margin-top:10px;"><div style="font-size:10px;color:var(--dim);margin-bottom:5px;text-transform:uppercase;letter-spacing:1px;">API Key</div>
<div style="font-family:'Share Tech Mono',monospace;font-size:11px;background:rgba(0,0,0,.3);padding:7px 10px;border-radius:5px;word-break:break-all;color:var(--p);margin-bottom:6px;">{api_key or "Not generated"}</div>
<form method="POST"><input type="hidden" name="action" value="gen_api"><button type="submit" class="btn btn-o" style="font-size:10px;"> Generate New Key</button></form>
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
  adh=''.join(f'<tr><td>{a["title"]}</td><td style="color:var(--dim)">{a["content"][:26]}</td><td style="color:{"var(--green)" if a["active"] else "var(--red)"}">{"" if a["active"] else ""}</td><td><a href="/admin/toggle-ad/{a["id"]}" class="btn btn-o" style="font-size:8px;padding:2px 5px;">Toggle</a> <a href="/admin/del-ad/{a["id"]}" class="btn btn-r" style="font-size:8px;padding:2px 5px;">Del</a></td></tr>' for a in ads)
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
  if cleaned: print(f" {cleaned} expired pastes")
  port=int(os.environ.get('PORT',5000))
  print(f"\n{'='*50}\n ⚡ ZEROSHELL v7.5\n 🌐 http://localhost:{port}\n{'='*50}\n")
  app.run(host='0.0.0.0',port=port,debug=False)
