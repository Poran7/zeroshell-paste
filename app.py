"""
ZeroShell v2.0 - Full Featured Paste Site
"""
import os, hashlib, secrets
from datetime import datetime, timedelta
from flask import Flask, request, render_template_string, redirect, session, flash, get_flashed_messages

app = Flask(__name__)
app.secret_key = secrets.token_hex(32)
import sqlite3
DB = "zeroshell.db"

def get_db():
    db = sqlite3.connect(DB)
    db.row_factory = sqlite3.Row
    return db

def init_db():
    db = get_db()
    db.executescript("""
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT UNIQUE NOT NULL,
            password TEXT NOT NULL,
            bio TEXT DEFAULT '',
            telegram TEXT DEFAULT '',
            avatar TEXT DEFAULT '👤',
            theme TEXT DEFAULT 'cyan',
            is_admin INTEGER DEFAULT 0,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            total_views INTEGER DEFAULT 0
        );
        CREATE TABLE IF NOT EXISTS pastes (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            slug TEXT UNIQUE NOT NULL,
            title TEXT NOT NULL,
            content TEXT NOT NULL,
            syntax TEXT DEFAULT 'text',
            visibility TEXT DEFAULT 'public',
            views INTEGER DEFAULT 0,
            user_id INTEGER,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY(user_id) REFERENCES users(id)
        );
        CREATE TABLE IF NOT EXISTS ads (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            title TEXT NOT NULL,
            content TEXT NOT NULL,
            url TEXT DEFAULT '',
            active INTEGER DEFAULT 1,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
    """)
    for col in [("avatar","TEXT DEFAULT '👤'"),("theme","TEXT DEFAULT 'cyan'"),("is_admin","INTEGER DEFAULT 0")]:
        try: db.execute(f"ALTER TABLE users ADD COLUMN {col[0]} {col[1]}")
        except: pass
    db.commit(); db.close()

def hash_pw(pw): return hashlib.sha256(pw.encode()).hexdigest()
def rand_slug(n=8): return secrets.token_urlsafe(n)[:n]

def get_badge(views, p30):
    if views>=10000: return ('Legendary','👑','#ffd700')
    if views>=5000:  return ('Famous','⚡','#ff6b00')
    if views>=1000:  return ('Popular','🔥','#ff2d55')
    if p30>=5:       return ('Active','🏃','#00f5ff')
    return ('Newcomer','⭐','#8899aa')

THEMES = {
    'cyan':  '#00f5ff','red':'#ff2d55','green':'#00ff88',
    'gold':  '#ffd60a','purple':'#bf5af2','blue':'#2979ff'
}
AVATARS = ['👤','⚡','🔥','💀','🤖','👾','🦊','🐉','🎭','🔮','🦅','🐺']

def style(theme='cyan'):
    p = THEMES.get(theme,'#00f5ff')
    return f"""<link href="https://fonts.googleapis.com/css2?family=Share+Tech+Mono&family=Rajdhani:wght@400;600;700&display=swap" rel="stylesheet">
<style>
:root{{--bg:#04080f;--card:#0b1623;--border:#0f2a40;--p:{p};
--green:#00ff88;--red:#ff2d55;--yellow:#ffd60a;--text:#c8e0f0;--dim:#4a6a80;}}
*{{margin:0;padding:0;box-sizing:border-box;}}
body{{background:var(--bg);color:var(--text);font-family:'Rajdhani',sans-serif;min-height:100vh;}}
body::before{{content:'';position:fixed;inset:0;
background-image:linear-gradient(rgba(0,245,255,.02) 1px,transparent 1px),linear-gradient(90deg,rgba(0,245,255,.02) 1px,transparent 1px);
background-size:40px 40px;pointer-events:none;z-index:0;}}
.wrap{{position:relative;z-index:1;max-width:1100px;margin:0 auto;padding:20px;}}
nav{{display:flex;align-items:center;justify-content:space-between;padding:12px 24px;
background:rgba(11,22,35,.95);border-bottom:1px solid var(--border);
position:sticky;top:0;z-index:100;backdrop-filter:blur(10px);flex-wrap:wrap;gap:8px;}}
.logo{{font-family:'Share Tech Mono',monospace;font-size:20px;color:var(--p);
text-decoration:none;text-shadow:0 0 15px {p}88;letter-spacing:2px;}}
.nav-links{{display:flex;gap:10px;align-items:center;flex-wrap:wrap;}}
.nav-links a{{color:var(--text);text-decoration:none;font-size:13px;font-weight:600;
padding:5px 10px;border-radius:6px;transition:all .2s;}}
.nav-links a:hover{{color:var(--p);background:rgba(255,255,255,.04);}}
.btn{{padding:8px 16px;border-radius:6px;border:none;cursor:pointer;
font-family:'Rajdhani',sans-serif;font-size:13px;font-weight:700;
letter-spacing:1px;text-decoration:none;display:inline-block;transition:all .2s;}}
.btn-p{{background:var(--p);color:#000;}}
.btn-p:hover{{box-shadow:0 0 20px {p}66;transform:translateY(-1px);}}
.btn-o{{background:transparent;border:1px solid var(--border);color:var(--text);}}
.btn-o:hover{{border-color:var(--p);color:var(--p);}}
.btn-r{{background:rgba(255,45,85,.12);border:1px solid rgba(255,45,85,.3);color:var(--red);}}
.card{{background:var(--card);border:1px solid var(--border);border-radius:12px;
padding:22px;margin-bottom:18px;position:relative;overflow:hidden;}}
.card::before{{content:'';position:absolute;top:0;left:0;right:0;height:1px;
background:linear-gradient(90deg,transparent,var(--p),transparent);opacity:.5;}}
input,textarea,select{{width:100%;padding:9px 13px;background:rgba(0,0,0,.4);
border:1px solid var(--border);border-radius:7px;color:var(--text);
font-family:'Rajdhani',sans-serif;font-size:14px;outline:none;transition:border .2s;}}
input:focus,textarea:focus,select:focus{{border-color:var(--p);}}
label{{display:block;font-size:11px;color:var(--dim);margin-bottom:5px;
text-transform:uppercase;letter-spacing:1px;}}
.fg{{margin-bottom:15px;}}
.pi{{display:flex;justify-content:space-between;align-items:center;
padding:11px 16px;background:rgba(0,0,0,.2);border:1px solid var(--border);
border-radius:9px;margin-bottom:7px;transition:all .2s;text-decoration:none;color:var(--text);}}
.pi:hover{{border-color:var(--p);transform:translateX(3px);}}
.pt{{font-size:14px;font-weight:700;color:var(--p);margin-bottom:2px;}}
.pm{{font-size:10px;color:var(--dim);font-family:'Share Tech Mono',monospace;}}
.pv{{font-family:'Share Tech Mono',monospace;color:var(--green);font-size:11px;}}
.badge{{display:inline-flex;align-items:center;gap:4px;padding:3px 9px;
border-radius:99px;font-size:10px;font-weight:700;letter-spacing:1px;}}
.code{{background:#020810;border:1px solid var(--border);border-radius:8px;
padding:18px;overflow-x:auto;font-family:'Share Tech Mono',monospace;
font-size:13px;line-height:1.7;white-space:pre-wrap;word-break:break-all;
color:#a8d0e0;max-height:600px;overflow-y:auto;}}
.sg{{display:grid;grid-template-columns:repeat(auto-fit,minmax(110px,1fr));gap:10px;margin-bottom:18px;}}
.sb{{background:rgba(0,0,0,.3);border:1px solid var(--border);border-radius:8px;
padding:12px;text-align:center;}}
.sn{{font-family:'Share Tech Mono',monospace;font-size:22px;font-weight:700;display:block;}}
.sl{{font-size:10px;color:var(--dim);text-transform:uppercase;letter-spacing:1px;}}
.g2{{display:grid;grid-template-columns:1fr 1fr;gap:14px;}}
.g3{{display:grid;grid-template-columns:repeat(3,1fr);gap:10px;}}
.alert{{padding:9px 14px;border-radius:7px;margin-bottom:12px;font-size:13px;}}
.ar{{background:rgba(255,45,85,.1);border:1px solid rgba(255,45,85,.3);color:var(--red);}}
.ag{{background:rgba(0,255,136,.1);border:1px solid rgba(0,255,136,.3);color:var(--green);}}
.av{{width:64px;height:64px;border-radius:50%;background:rgba(255,255,255,.05);
display:flex;align-items:center;justify-content:center;font-size:28px;
border:2px solid var(--p);box-shadow:0 0 15px {p}44;}}
.ad-bar{{background:rgba(255,214,10,.06);border:1px solid rgba(255,214,10,.2);
border-radius:7px;padding:9px 16px;margin-bottom:14px;}}
.sb-wrap{{display:flex;gap:10px;margin-bottom:16px;}}
.sb-wrap input{{flex:1;}}
.ao{{font-size:26px;cursor:pointer;padding:6px;border-radius:7px;
border:2px solid transparent;transition:all .2s;display:inline-block;}}
.ao:hover,.ao.sel{{border-color:var(--p);background:rgba(255,255,255,.05);}}
.th-btn{{width:32px;height:32px;border-radius:50%;border:3px solid transparent;
cursor:pointer;transition:all .2s;display:inline-block;}}
.th-btn:hover,.th-btn.act{{border-color:#fff;transform:scale(1.2);}}
.at{{width:100%;border-collapse:collapse;font-size:12px;}}
.at th{{padding:8px;text-align:left;color:var(--dim);border-bottom:1px solid var(--border);
font-size:10px;letter-spacing:1px;text-transform:uppercase;}}
.at td{{padding:8px;border-bottom:1px solid rgba(15,42,64,.4);}}
.scan{{position:fixed;inset:0;pointer-events:none;z-index:999;
background:repeating-linear-gradient(0deg,transparent,transparent 2px,rgba(0,0,0,.04) 2px,rgba(0,0,0,.04) 4px);}}
footer{{text-align:center;padding:20px;color:var(--dim);font-family:'Share Tech Mono',monospace;
font-size:11px;letter-spacing:2px;border-top:1px solid var(--border);margin-top:24px;}}
@media(max-width:600px){{.g2,.g3{{grid-template-columns:1fr;}}}}
</style>"""

def base(content, title="ZeroShell", theme='cyan'):
    s = style(theme)
    msgs = get_flashed_messages(with_categories=True)
    alerts = ''.join(f'<div class="alert {"ag" if c=="green" else "ar"}">{m}</div>' for c,m in msgs)
    try:
        db=get_db(); ad=db.execute("SELECT * FROM ads WHERE active=1 ORDER BY RANDOM() LIMIT 1").fetchone(); db.close()
    except: ad=None
    ad_html = f'<div class="ad-bar"><span style="color:var(--yellow);font-size:10px;font-weight:700;">📢 AD</span> <a href="{ad["url"] or "#"}" target="_blank" style="color:var(--yellow);text-decoration:none;font-size:13px;">{ad["title"]} — {ad["content"]}</a></div>' if ad else ''
    u = session.get('user','')
    nav_r = f'<a href="/profile/{u}">{session.get("avatar","👤")} {u}</a>{"<a href=/admin>⚙️Admin</a>" if session.get("is_admin") else ""}<a href="/logout">Logout</a>' if u else '<a href="/login">Login</a><a href="/register" class="btn btn-p">Register</a>'
    return f'''<!DOCTYPE html><html lang="en"><head><meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>{title} - ZeroShell</title>{s}</head><body>
<div class="scan"></div>
<nav><a class="logo" href="/">⚡ ZeroShell</a>
<div class="nav-links"><a href="/">Home</a><a href="/new">+ New</a><a href="/search">🔍 Search</a>{nav_r}</div></nav>
<div class="wrap">{alerts}{ad_html}{content}</div>
<footer>⚡ ZEROSHELL v2.0 · <a href="https://t.me/ZeroShell_Store" style="color:var(--p);text-decoration:none;">✈️ t.me/ZeroShell_Store</a></footer>
</body></html>'''

@app.route('/')
def home():
    db=get_db()
    pastes=db.execute("SELECT p.*,u.username,u.avatar FROM pastes p LEFT JOIN users u ON p.user_id=u.id WHERE p.visibility='public' ORDER BY p.created_at DESC LIMIT 20").fetchall()
    tp=db.execute("SELECT COUNT(*) FROM pastes").fetchone()[0]
    tv=db.execute("SELECT COALESCE(SUM(views),0) FROM pastes").fetchone()[0]
    tu=db.execute("SELECT COUNT(*) FROM users").fetchone()[0]
    db.close()
    pl=''.join(f'<a href="/paste/{p["slug"]}" class="pi"><div><div class="pt">{p["title"]}</div><div class="pm">{p["avatar"] or "👤"} {p["username"] or "Anonymous"} · {p["created_at"][:10]} · {p["syntax"]}</div></div><div class="pv">👁 {p["views"]}</div></a>' for p in pastes) or '<div style="text-align:center;color:var(--dim);padding:24px;">No pastes yet!</div>'
    c=f'''<div style="text-align:center;padding:44px 20px 26px;">
<div style="font-family:'Share Tech Mono',monospace;font-size:clamp(24px,6vw,48px);color:var(--p);text-shadow:0 0 30px var(--p)88;letter-spacing:4px;margin-bottom:8px;">⚡ ZEROSHELL</div>
<div style="color:var(--dim);font-size:13px;letter-spacing:3px;margin-bottom:24px;">PASTE · SHARE · TRACK</div>
<a href="/new" class="btn btn-p" style="font-size:15px;padding:11px 32px;letter-spacing:2px;">+ Create New Paste</a></div>
<div class="g3" style="margin-bottom:20px;">
<div class="sb"><span class="sn" style="color:var(--p);">{tp}</span><span class="sl">Pastes</span></div>
<div class="sb"><span class="sn" style="color:var(--green);">{tv}</span><span class="sl">Views</span></div>
<div class="sb"><span class="sn" style="color:var(--yellow);">{tu}</span><span class="sl">Users</span></div></div>
<div class="card"><div style="font-size:15px;font-weight:700;color:var(--p);letter-spacing:2px;margin-bottom:14px;">🕐 RECENT PASTES</div>{pl}</div>'''
    return base(c,"Home",session.get('theme','cyan'))

@app.route('/search')
def search():
    q=request.args.get('q','').strip()
    res=[]
    if q:
        db=get_db(); res=db.execute("SELECT p.*,u.username,u.avatar FROM pastes p LEFT JOIN users u ON p.user_id=u.id WHERE p.visibility='public' AND (p.title LIKE ? OR p.content LIKE ?) ORDER BY p.created_at DESC LIMIT 30",(f'%{q}%',f'%{q}%')).fetchall(); db.close()
    rl=''.join(f'<a href="/paste/{p["slug"]}" class="pi"><div><div class="pt">{p["title"]}</div><div class="pm">{p["avatar"] or "👤"} {p["username"] or "Anon"} · {p["created_at"][:10]}</div></div><div class="pv">👁 {p["views"]}</div></a>' for p in res) or (f'<div style="text-align:center;color:var(--dim);padding:20px;">No results for "{q}"</div>' if q else '')
    c=f'''<div class="card"><div style="font-size:15px;font-weight:700;color:var(--p);letter-spacing:2px;margin-bottom:14px;">🔍 SEARCH</div>
<form method="GET"><div class="sb-wrap"><input name="q" value="{q}" placeholder="Search paste title or content..." autofocus><button type="submit" class="btn btn-p">Search</button></div></form>{rl}</div>'''
    return base(c,"Search",session.get('theme','cyan'))

@app.route('/new', methods=['GET','POST'])
def new_paste():
    if request.method=='POST':
        title=request.form.get('title','').strip(); content=request.form.get('content','').strip()
        syntax=request.form.get('syntax','text'); vis=request.form.get('visibility','public')
        if not title or not content: flash('Fill all fields!','red')
        else:
            slug=rand_slug(); db=get_db()
            db.execute("INSERT INTO pastes (slug,title,content,syntax,visibility,user_id) VALUES (?,?,?,?,?,?)",(slug,title,content,syntax,vis,session.get('user_id')))
            db.commit(); db.close(); return redirect(f'/paste/{slug}')
    c='''<div style="max-width:800px;margin:0 auto;"><div class="card">
<div style="font-size:16px;font-weight:700;color:var(--p);letter-spacing:2px;margin-bottom:18px;">📝 CREATE NEW PASTE</div>
<form method="POST">
<div class="fg"><label>Title</label><input name="title" placeholder="Enter paste title..." required></div>
<div class="fg"><label>Content <span id="lc" style="color:var(--dim);font-size:10px;"></span></label>
<textarea name="content" id="pc" rows="13" placeholder="Paste content here..." required style="font-family:'Share Tech Mono',monospace;font-size:12px;resize:vertical;" oninput="u(this)"></textarea></div>
<div class="g2">
<div class="fg"><label>Syntax</label><select name="syntax"><option value="text">Plain Text</option><option value="python">Python</option><option value="javascript">JavaScript</option><option value="html">HTML</option><option value="css">CSS</option><option value="bash">Bash</option><option value="json">JSON</option><option value="sql">SQL</option></select></div>
<div class="fg"><label>Visibility</label><select name="visibility"><option value="public">🌐 Public</option><option value="private">🔒 Private</option></select></div></div>
<button type="submit" class="btn btn-p" style="width:100%;font-size:15px;padding:12px;">🚀 Create Paste</button>
</form></div></div>
<script>function u(el){const l=el.value.split('\\n').length,c=el.value.length;document.getElementById('lc').textContent=l+' lines · '+c+' chars';}</script>'''
    return base(c,"New Paste",session.get('theme','cyan'))

@app.route('/paste/<slug>')
def view_paste(slug):
    db=get_db(); paste=db.execute("SELECT * FROM pastes WHERE slug=?",(slug,)).fetchone()
    if not paste: db.close(); return base('<div class="card" style="text-align:center;padding:40px;"><div style="font-size:40px;">🔍</div><p style="color:var(--dim);margin-top:10px;">Paste not found!</p></div>',"404")
    db.execute("UPDATE pastes SET views=views+1 WHERE slug=?",(slug,))
    auth=None; av='👤'; ath='cyan'
    if paste['user_id']:
        db.execute("UPDATE users SET total_views=total_views+1 WHERE id=?",(paste['user_id'],))
        u=db.execute("SELECT username,avatar,theme FROM users WHERE id=?",(paste['user_id'],)).fetchone()
        if u: auth=u['username']; av=u['avatar'] or '👤'; ath=u['theme'] or 'cyan'
    db.commit(); db.close()
    lines=len(paste['content'].split('\n')); chars=len(paste['content'])
    del_btn=f'<a href="/delete/{paste["slug"]}" class="btn btn-r" style="font-size:11px;padding:5px 10px;" onclick="return confirm(\'Delete?\')">🗑</a>' if session.get('user_id')==paste['user_id'] else ''
    al=f'<a href="/profile/{auth}" style="color:var(--p);text-decoration:none;">{av} {auth}</a>' if auth else 'Anonymous'
    c=f'''<div style="max-width:900px;margin:0 auto;">
<div class="card"><div style="display:flex;justify-content:space-between;align-items:flex-start;flex-wrap:wrap;gap:10px;">
<div><div style="font-size:19px;font-weight:700;color:var(--p);margin-bottom:5px;">{paste["title"]}</div>
<div style="font-size:11px;color:var(--dim);font-family:'Share Tech Mono',monospace;">by {al} · {paste["created_at"][:16]} · {paste["syntax"]}</div></div>
<div style="display:flex;gap:7px;align-items:center;flex-wrap:wrap;">
<span style="font-family:'Share Tech Mono',monospace;color:var(--green);font-size:12px;">👁 {paste["views"]+1}</span>
<span style="font-family:'Share Tech Mono',monospace;color:var(--dim);font-size:10px;">{lines} lines · {chars} chars</span>
<button onclick="cp()" class="btn btn-o" style="font-size:11px;padding:5px 10px;">📋 Copy</button>
{del_btn}</div></div></div>
<div class="card" style="padding:0;">
<div style="padding:10px 16px;border-bottom:1px solid var(--border);display:flex;justify-content:space-between;">
<span style="font-family:'Share Tech Mono',monospace;font-size:11px;color:var(--dim);">{paste["syntax"].upper()}</span>
<span style="font-size:10px;color:var(--dim);">{lines} lines · {chars} chars</span></div>
<div class="code" id="pc">{paste["content"]}</div></div></div>
<script>function cp(){{navigator.clipboard.writeText(document.getElementById('pc').innerText);alert('✅ Copied!');}}</script>'''
    return base(c, paste['title'], ath)

@app.route('/profile/<username>')
def profile(username):
    db=get_db(); user=db.execute("SELECT * FROM users WHERE username=?",(username,)).fetchone()
    if not user: db.close(); return redirect('/')
    pastes=db.execute("SELECT * FROM pastes WHERE user_id=? AND visibility='public' ORDER BY created_at DESC",(user['id'],)).fetchall()
    p30=db.execute("SELECT COUNT(*) FROM pastes WHERE user_id=? AND created_at>=?",(user['id'],(datetime.now()-timedelta(days=30)).strftime('%Y-%m-%d'))).fetchone()[0]
    db.close()
    badge=get_badge(user['total_views'],p30); theme=user['theme'] or 'cyan'; av=user['avatar'] or '👤'
    all_b=[('Active','🏃','#00f5ff',p30>=5),('Popular','🔥','#ff2d55',user['total_views']>=1000),('Famous','⚡','#ff6b00',user['total_views']>=5000),('Legendary','👑','#ffd700',user['total_views']>=10000)]
    bh=''.join(f'<span class="badge" style="background:{b[2]}{"22" if b[3] else "08"};color:{b[2] if b[3] else "#4a6a80"};border:1px solid {b[2]}{"44" if b[3] else "18"};">{b[1]} {b[0]}</span>' for b in all_b)
    pl=''.join(f'<a href="/paste/{p["slug"]}" class="pi"><div><div class="pt">{p["title"]}</div><div class="pm">{p["created_at"][:10]} · {p["syntax"]} · {len(p["content"].split(chr(10)))} lines</div></div><div class="pv">👁 {p["views"]}</div></a>' for p in pastes) or '<div style="text-align:center;color:var(--dim);padding:16px;">No pastes yet.</div>'
    eb=f'<a href="/settings" class="btn btn-o" style="font-size:12px;padding:5px 12px;">⚙️ Edit</a>' if session.get('user')==username else ''
    tg=f'<a href="https://t.me/{user["telegram"]}" target="_blank" style="color:#00aaff;font-size:12px;text-decoration:none;">✈️ @{user["telegram"]}</a>' if user['telegram'] else ''
    c=f'''<div style="max-width:900px;margin:0 auto;">
<div class="card"><div style="display:flex;align-items:center;gap:18px;flex-wrap:wrap;margin-bottom:18px;">
<div class="av">{av}</div>
<div style="flex:1;"><div style="font-size:22px;font-weight:700;color:var(--p);margin-bottom:5px;">{username}</div>
<div style="margin-bottom:6px;">{bh}</div>
<div style="color:var(--dim);font-size:13px;">{user["bio"] or ""}</div>{tg}</div>{eb}</div>
<div class="sg"><div class="sb"><span class="sn" style="color:var(--p);">{len(pastes)}</span><span class="sl">Pastes</span></div>
<div class="sb"><span class="sn" style="color:var(--green);">{user["total_views"]}</span><span class="sl">Views</span></div>
<div class="sb"><span class="sn" style="color:var(--yellow);">{p30}</span><span class="sl">30d Pastes</span></div>
<div class="sb"><span class="sn" style="color:var(--dim);font-size:13px;">{user["created_at"][:10]}</span><span class="sl">Joined</span></div></div></div>
<div class="card"><div style="font-size:14px;font-weight:700;color:var(--p);letter-spacing:2px;margin-bottom:12px;">📝 PASTES</div>{pl}</div></div>'''
    return base(c,username,theme)

@app.route('/settings', methods=['GET','POST'])
def settings():
    if not session.get('user'): return redirect('/login')
    if request.method=='POST':
        bio=request.form.get('bio','').strip(); tg=request.form.get('telegram','').strip().lstrip('@')
        av=request.form.get('avatar','👤'); th=request.form.get('theme','cyan')
        if th not in THEMES: th='cyan'
        db=get_db(); db.execute("UPDATE users SET bio=?,telegram=?,avatar=?,theme=? WHERE username=?",(bio,tg,av,th,session['user'])); db.commit(); db.close()
        session['avatar']=av; session['theme']=th; flash('Profile updated!','green')
        return redirect(f'/profile/{session["user"]}')
    db=get_db(); user=db.execute("SELECT * FROM users WHERE username=?",(session['user'],)).fetchone(); db.close()
    ct=user['theme'] or 'cyan'; ca=user['avatar'] or '👤'
    th_html=''.join(f'<div class="th-btn {"act" if k==ct else ""}" style="background:{v};" onclick="st(\'{k}\')" title="{k}"></div>' for k,v in THEMES.items())
    av_html=''.join(f'<span class="ao {"sel" if a==ca else ""}" onclick="sa(\'{a}\')">{a}</span>' for a in AVATARS)
    c=f'''<div style="max-width:580px;margin:0 auto;"><div class="card">
<div style="font-size:16px;font-weight:700;color:var(--p);letter-spacing:2px;margin-bottom:18px;">⚙️ SETTINGS</div>
<form method="POST" id="sf">
<input type="hidden" name="avatar" id="ai" value="{ca}">
<input type="hidden" name="theme" id="ti" value="{ct}">
<div class="fg"><label>Avatar</label><div style="display:flex;flex-wrap:wrap;gap:4px;margin-top:4px;">{av_html}</div></div>
<div class="fg"><label>Theme</label><div style="display:flex;gap:8px;margin-top:4px;">{th_html}</div></div>
<div class="fg"><label>Bio</label><input name="bio" value="{user['bio'] or ''}" placeholder="Short bio..."></div>
<div class="fg"><label>Telegram</label><input name="telegram" value="{user['telegram'] or ''}" placeholder="username without @"></div>
<button type="submit" class="btn btn-p" style="width:100%;padding:12px;font-size:14px;">💾 Save Changes</button>
</form></div></div>
<script>
function sa(a){{document.getElementById('ai').value=a;document.querySelectorAll('.ao').forEach(e=>e.classList.remove('sel'));event.target.classList.add('sel');}}
function st(t){{document.getElementById('ti').value=t;document.querySelectorAll('.th-btn').forEach(e=>e.classList.remove('act'));event.target.classList.add('act');}}
</script>'''
    return base(c,"Settings",ct)

@app.route('/admin')
def admin():
    if not session.get('is_admin'): flash('Admin only!','red'); return redirect('/')
    db=get_db()
    users=db.execute("SELECT * FROM users ORDER BY created_at DESC").fetchall()
    pastes=db.execute("SELECT p.*,u.username FROM pastes p LEFT JOIN users u ON p.user_id=u.id ORDER BY p.created_at DESC LIMIT 50").fetchall()
    ads=db.execute("SELECT * FROM ads ORDER BY created_at DESC").fetchall()
    tv=db.execute("SELECT COALESCE(SUM(views),0) FROM pastes").fetchone()[0]; db.close()
    uh=''.join(f'<tr><td>{u["username"]}</td><td style="color:var(--dim)">{u["created_at"][:10]}</td><td style="color:var(--green)">{u["total_views"]}</td><td>{"👑" if u["is_admin"] else "👤"}</td><td><a href="/admin/del-user/{u["id"]}" class="btn btn-r" style="font-size:10px;padding:3px 7px;" onclick="return confirm(\'Delete?\')">Del</a></td></tr>' for u in users)
    ph=''.join(f'<tr><td><a href="/paste/{p["slug"]}" style="color:var(--p);text-decoration:none;">{p["title"][:28]}</a></td><td style="color:var(--dim)">{p["username"] or "Anon"}</td><td style="color:var(--green)">{p["views"]}</td><td style="color:var(--dim)">{p["created_at"][:10]}</td><td><a href="/admin/del-paste/{p["slug"]}" class="btn btn-r" style="font-size:10px;padding:3px 7px;">Del</a></td></tr>' for p in pastes)
    adh=''.join(f'<tr><td>{a["title"]}</td><td style="color:var(--dim)">{a["content"][:35]}</td><td style="color:{"var(--green)" if a["active"] else "var(--red)"}">{"✅" if a["active"] else "❌"}</td><td><a href="/admin/toggle-ad/{a["id"]}" class="btn btn-o" style="font-size:10px;padding:3px 7px;">Toggle</a> <a href="/admin/del-ad/{a["id"]}" class="btn btn-r" style="font-size:10px;padding:3px 7px;">Del</a></td></tr>' for a in ads)
    c=f'''<div style="font-size:18px;font-weight:700;color:var(--p);letter-spacing:2px;margin-bottom:18px;">⚙️ ADMIN PANEL</div>
<div class="g3" style="margin-bottom:18px;">
<div class="sb"><span class="sn" style="color:var(--p);">{len(users)}</span><span class="sl">Users</span></div>
<div class="sb"><span class="sn" style="color:var(--green);">{len(pastes)}</span><span class="sl">Pastes</span></div>
<div class="sb"><span class="sn" style="color:var(--yellow);">{tv}</span><span class="sl">Views</span></div></div>
<div class="card"><div style="font-size:14px;font-weight:700;color:var(--yellow);margin-bottom:12px;">📢 Add Ad</div>
<form method="POST" action="/admin/add-ad">
<div class="g2"><div class="fg"><label>Title</label><input name="title" placeholder="Ad title" required></div>
<div class="fg"><label>URL</label><input name="url" placeholder="https://..."></div></div>
<div class="fg"><label>Content</label><input name="content" placeholder="Ad text" required></div>
<button type="submit" class="btn btn-p">📢 Add Ad</button></form></div>
<div class="card"><div style="font-size:14px;font-weight:700;color:var(--yellow);margin-bottom:12px;">📢 ADS ({len(ads)})</div>
<div style="overflow-x:auto;"><table class="at"><tr><th>Title</th><th>Content</th><th>Status</th><th>Action</th></tr>{adh or "<tr><td colspan='4' style='color:var(--dim);text-align:center;padding:14px;'>No ads</td></tr>"}</table></div></div>
<div class="card"><div style="font-size:14px;font-weight:700;color:var(--p);margin-bottom:12px;">👤 USERS ({len(users)})</div>
<div style="overflow-x:auto;"><table class="at"><tr><th>Username</th><th>Joined</th><th>Views</th><th>Role</th><th>Action</th></tr>{uh}</table></div></div>
<div class="card"><div style="font-size:14px;font-weight:700;color:var(--p);margin-bottom:12px;">📝 PASTES</div>
<div style="overflow-x:auto;"><table class="at"><tr><th>Title</th><th>Author</th><th>Views</th><th>Date</th><th>Action</th></tr>{ph}</table></div></div>'''
    return base(c,"Admin",session.get('theme','cyan'))

@app.route('/admin/add-ad', methods=['POST'])
def add_ad():
    if not session.get('is_admin'): return redirect('/')
    t=request.form.get('title',''); ct=request.form.get('content',''); u=request.form.get('url','')
    if t and ct:
        db=get_db(); db.execute("INSERT INTO ads (title,content,url) VALUES (?,?,?)",(t,ct,u)); db.commit(); db.close()
        flash('Ad added!','green')
    return redirect('/admin')

@app.route('/admin/toggle-ad/<int:i>')
def toggle_ad(i):
    if not session.get('is_admin'): return redirect('/')
    db=get_db(); db.execute("UPDATE ads SET active=1-active WHERE id=?",(i,)); db.commit(); db.close()
    return redirect('/admin')

@app.route('/admin/del-ad/<int:i>')
def del_ad(i):
    if not session.get('is_admin'): return redirect('/')
    db=get_db(); db.execute("DELETE FROM ads WHERE id=?",(i,)); db.commit(); db.close()
    return redirect('/admin')

@app.route('/admin/del-user/<int:i>')
def del_user(i):
    if not session.get('is_admin'): return redirect('/')
    db=get_db(); db.execute("DELETE FROM users WHERE id=?",(i,)); db.execute("DELETE FROM pastes WHERE user_id=?",(i,)); db.commit(); db.close()
    flash('User deleted!','green'); return redirect('/admin')

@app.route('/admin/del-paste/<slug>')
def del_paste(slug):
    if not session.get('is_admin'): return redirect('/')
    db=get_db(); db.execute("DELETE FROM pastes WHERE slug=?",(slug,)); db.commit(); db.close()
    return redirect('/admin')

def auth_page(title, err=''):
    ex='<div class="fg"><label>Telegram (optional)</label><input name="telegram" placeholder="username without @"></div>' if title=='Register' else ''
    alt='Have account? <a href="/login" style="color:var(--p);">Login</a>' if title=='Register' else 'No account? <a href="/register" style="color:var(--p);">Register</a>'
    eh=f'<div class="alert ar">{err}</div>' if err else ''
    c=f'''<div style="max-width:380px;margin:44px auto;"><div class="card">
<div style="text-align:center;font-size:17px;font-weight:700;color:var(--p);letter-spacing:2px;margin-bottom:18px;">{title.upper()}</div>
{eh}<form method="POST">
<div class="fg"><label>Username</label><input name="username" required autocomplete="off"></div>
<div class="fg"><label>Password</label><input name="password" type="password" required></div>
{ex}<button type="submit" class="btn btn-p" style="width:100%;padding:12px;font-size:14px;">{title}</button>
</form><div style="text-align:center;margin-top:12px;font-size:12px;color:var(--dim);">{alt}</div>
</div></div>'''
    return base(c,title)

@app.route('/register', methods=['GET','POST'])
def register():
    if request.method=='POST':
        u=request.form.get('username','').strip(); pw=request.form.get('password',''); tg=request.form.get('telegram','').strip().lstrip('@')
        if not u or not pw: return auth_page('Register','Fill all fields!')
        db=get_db()
        if db.execute("SELECT id FROM users WHERE username=?",(u,)).fetchone(): db.close(); return auth_page('Register','Username taken!')
        ia=1 if db.execute("SELECT COUNT(*) FROM users").fetchone()[0]==0 else 0
        db.execute("INSERT INTO users (username,password,telegram,is_admin) VALUES (?,?,?,?)",(u,hash_pw(pw),tg,ia)); db.commit()
        user=db.execute("SELECT * FROM users WHERE username=?",(u,)).fetchone(); db.close()
        session.update({'user':u,'user_id':user['id'],'is_admin':user['is_admin'],'avatar':user['avatar'] or '👤','theme':user['theme'] or 'cyan'})
        return redirect(f'/profile/{u}')
    return auth_page('Register')

@app.route('/login', methods=['GET','POST'])
def login():
    if request.method=='POST':
        u=request.form.get('username','').strip(); pw=request.form.get('password','')
        db=get_db(); user=db.execute("SELECT * FROM users WHERE username=? AND password=?",(u,hash_pw(pw))).fetchone(); db.close()
        if user:
            session.update({'user':u,'user_id':user['id'],'is_admin':user['is_admin'],'avatar':user['avatar'] or '👤','theme':user['theme'] or 'cyan'})
            return redirect(f'/profile/{u}')
        return auth_page('Login','Wrong credentials!')
    return auth_page('Login')

@app.route('/logout')
def logout():
    session.clear(); return redirect('/')

@app.route('/delete/<slug>')
def delete_paste(slug):
    if not session.get('user_id'): return redirect('/login')
    db=get_db(); p=db.execute("SELECT * FROM pastes WHERE slug=?",(slug,)).fetchone()
    if p and p['user_id']==session['user_id']: db.execute("DELETE FROM pastes WHERE slug=?",(slug,)); db.commit()
    db.close(); return redirect('/')

if __name__=='__main__':
    init_db()
    port=int(os.environ.get('PORT',5000))
    print(f"\n{'='*50}\n  ⚡  ZEROSHELL v2.0\n  🌐  http://localhost:{port}\n{'='*50}\n")
    app.run(host='0.0.0.0',port=port,debug=False)
