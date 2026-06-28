"""
自律AI対話デスクトップアプリ (pywebview版・ワンファイル)
- Claude と "Codex"(GPT人格) がネイティブ窓内で勝手に会話を続ける
- 下の入力欄から人間も割り込める (居なくてもOK)
- キャラはCSSの仮実装。idle/talkingを class で切替 -> PNG/Live2D に差し替え可

準備: pip install pywebview requests python-dotenv
      同じフォルダに .env を置く (.env.example をコピーして鍵を埋める)
実行: python app.py
"""

import os, time, threading, sqlite3, requests, random, webview
from dotenv import load_dotenv

load_dotenv(override=True)  # OSの既存環境変数(setx等)より .env を優先

def _check_key(name):
    k = os.environ.get(name, "")
    if not k:
        print(f"  {name}: (未設定)")
    else:
        print(f"  {name}: {k[:7]}...{k[-4:]}  len={len(k)}")

print("読み込んだ鍵:")
_check_key("ANTHROPIC_API_KEY")
_check_key("OPENAI_API_KEY")

DB = "chat.db"
PACE_SEC = 6
STALL_EVERY = 8

PERSONAS = {
    "Claude": ("あなたはClaude。慎重で構造化志向、前提を疑う。相手に安易に同意せず"
               "論点を1つに絞って深掘りする。日本語、2〜4文で簡潔に。"),
    "Codex":  ("あなたはCodex。実装ドリブンで直球、抽象論を嫌い『で、どう動かす?』に引き戻す。"
               "相手の案に具体例か反例を即返す。日本語、2〜4文で簡潔に。"),
}
SEED_TOPICS = [
    "AI同士の会議は人間の会議より速いが質は上がるのか",
    "Unix哲学はマルチエージェント設計にどこまで効くか",
    "自動化の皮肉(Bainbridge): 自動化が進むほど人間の役割が難しくなる件",
]

def db():
    c = sqlite3.connect(DB, check_same_thread=False)
    c.execute("CREATE TABLE IF NOT EXISTS messages("
              "id INTEGER PRIMARY KEY, author TEXT, text TEXT, ts REAL)")
    return c

conn = db()
lock = threading.Lock()

def add(author, text):
    with lock:
        conn.execute("INSERT INTO messages(author,text,ts) VALUES(?,?,?)",
                     (author, text, time.time()))
        conn.commit()

def transcript(limit=20):
    with lock:
        rows = conn.execute("SELECT author,text FROM messages ORDER BY id DESC LIMIT ?",
                            (limit,)).fetchall()
    return "\n".join(f"{a}: {t}" for a, t in reversed(rows))

def recent_authors(n):
    with lock:
        rows = conn.execute("SELECT author FROM messages ORDER BY id DESC LIMIT ?",
                            (n,)).fetchall()
    return [a for (a,) in rows]

INSTR = ("\n以下はグループチャットのログ。**直近にUser(人間)の発言があれば、進行中の話題を中断してでも"
         "最優先でそれに応答・話題転換する**。自分の番として次の1発言だけ返す。名前接頭辞は付けない。")

def call_claude(convo, persona):
    r = requests.post("https://api.anthropic.com/v1/messages",
        headers={"x-api-key": os.environ["ANTHROPIC_API_KEY"],
                 "anthropic-version": "2023-06-01", "content-type": "application/json"},
        json={"model": "claude-sonnet-4-6", "max_tokens": 300,
              "system": persona + INSTR,
              "messages": [{"role": "user", "content": convo}]}, timeout=60)
    data = r.json()
    if r.status_code != 200 or "content" not in data:
        raise RuntimeError(f"HTTP {r.status_code}: {data}")  # 真のエラー本文を出す
    return data["content"][0]["text"].strip()

def call_gpt(convo, persona):
    r = requests.post("https://api.openai.com/v1/chat/completions",
        headers={"Authorization": f"Bearer {os.environ['OPENAI_API_KEY']}",
                 "Content-Type": "application/json"},
        json={"model": "gpt-5", "max_completion_tokens": 2000,
              "reasoning_effort": "low",  # 雑談に重い推論は不要。これ無いと推論が枠を食って空応答
              "messages": [{"role": "system", "content": persona + INSTR},
                  {"role": "user", "content": convo}]}, timeout=60)
    data = r.json()
    if r.status_code != 200 or "choices" not in data:
        raise RuntimeError(f"HTTP {r.status_code}: {data}")
    msg = data["choices"][0]["message"].get("content", "").strip()
    if not msg:  # 空=推論が枠を食い切った等。理由を可視化
        fr = data["choices"][0].get("finish_reason")
        raise RuntimeError(f"空応答 (finish_reason={fr}). max_completion_tokensを上げるか reasoning_effort を下げる")
    return msg

CALLERS = {"Claude": call_claude, "Codex": call_gpt}

def loop():
    if not transcript():
        add("User", f"今日のお題: {random.choice(SEED_TOPICS)}")
    turn, speaker = 0, "Claude"
    while True:
        # 自動の話題投入は「人間が最近喋っていない=ネタ切れ」の時だけ。人間が steer 中は黙る
        if turn and turn % STALL_EVERY == 0 and "User" not in recent_authors(STALL_EVERY):
            add("User", f"話題を変える: {random.choice(SEED_TOPICS)}")
        try:
            add(speaker, CALLERS[speaker](transcript(), PERSONAS[speaker]))
        except Exception as e:
            add("System", f"[{speaker} error] {e}")
        speaker = "Codex" if speaker == "Claude" else "Claude"
        turn += 1
        time.sleep(PACE_SEC)

class Api:
    """JSから呼ぶ橋渡し"""
    def poll(self, since_id):
        with lock:
            rows = conn.execute("SELECT id,author,text FROM messages WHERE id>? ORDER BY id",
                                (since_id,)).fetchall()
        return [{"id": i, "author": a, "text": t} for i, a, t in rows]
    def send_human(self, text):
        if text.strip():
            add("User", text.strip())
        return True
    def clear(self):
        with lock:
            conn.execute("DELETE FROM messages")
            conn.commit()
        add("User", f"今日のお題: {random.choice(SEED_TOPICS)}")  # 空だと崩れるので即お題を撒く
        return True

HTML = """
<!doctype html><meta charset=utf-8>
<style>
  body{margin:0;font-family:system-ui;background:#1b1d23;color:#e8e8ea;height:100vh;display:flex;flex-direction:column}
  #stage{display:flex;justify-content:space-around;align-items:flex-end;padding:16px;gap:12px}
  .char{text-align:center;transition:.2s}
  .face{width:96px;height:96px;border-radius:50%;display:flex;align-items:center;justify-content:center;font-size:44px;border:3px solid #444;transition:.15s}
  .Claude .face{background:#caa45a}.Codex .face{background:#4a90e2}
  .talking .face{transform:scale(1.12);border-color:#fff;box-shadow:0 0 18px rgba(255,255,255,.5)}
  #log{flex:1;overflow-y:auto;padding:12px 16px;display:flex;flex-direction:column;gap:8px}
  .msg{max-width:75%;padding:8px 12px;border-radius:12px;line-height:1.4;font-size:14px}
  .Claude.msg{align-self:flex-start;background:#3a3320}
  .Codex.msg{align-self:flex-end;background:#1e3354}
  .User.msg{align-self:center;background:#333;font-style:italic;opacity:.85}
  .System.msg{align-self:center;color:#e88;font-size:12px}
  .who{font-size:11px;opacity:.6;margin-bottom:2px}
  #bar{display:flex;padding:10px;gap:8px;background:#15161b}
  #in{flex:1;padding:9px;border-radius:8px;border:1px solid #444;background:#222;color:#eee}
  #send{padding:9px 16px;border:0;border-radius:8px;background:#4a90e2;color:#fff;cursor:pointer}
</style>
<div id=stage>
  <div class="char Claude" id=cChalde><div class=face>🤔</div><div>Claude</div></div>
  <div class="char Codex"  id=cCodex><div class=face>⚙️</div><div>Codex</div></div>
</div>
<div id=log></div>
<div id=bar>
  <input id=in placeholder="割り込む… (Enterで送信)">
  <button id=send>送信</button>
  <button id=clr title="会話を消す">クリア</button>
</div>
<script>
let last=0;
const log=document.getElementById('log');
function talk(author){
  ['Claude','Codex'].forEach(n=>document.getElementById('c'+n)?.classList.remove('talking'));
  const el=document.getElementById('c'+author); if(el) el.classList.add('talking');
}
async function tick(){
  try{
    const rows=await window.pywebview.api.poll(last);
    for(const m of rows){
      last=m.id;
      const d=document.createElement('div'); d.className=m.author+' msg';
      d.innerHTML='<div class=who>'+m.author+'</div>'+m.text.replace(/</g,'&lt;');
      log.appendChild(d); log.scrollTop=log.scrollHeight;
      if(m.author==='Claude'||m.author==='Codex') talk(m.author);
    }
  }catch(e){}
}
function send(){const i=document.getElementById('in');if(i.value.trim()){window.pywebview.api.send_human(i.value);i.value='';}}
document.getElementById('send').onclick=send;
document.getElementById('in').addEventListener('keydown',e=>{if(e.key==='Enter')send();});
document.getElementById('clr').onclick=async()=>{
  await window.pywebview.api.clear();
  last=0; log.innerHTML='';   // 画面側もリセット
};
setInterval(tick,1000);
</script>
"""

if __name__ == "__main__":
    threading.Thread(target=loop, daemon=True).start()
    webview.create_window("AI雑談ルーム", html=HTML, js_api=Api(), width=520, height=720)
    webview.start()
