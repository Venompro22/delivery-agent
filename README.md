# 🚚 Delivery Agent

**An AI dispatcher for small delivery businesses** — plans the smartest route for your drivers,
understands customer & driver WeChat messages, and keeps everyone updated.
Built from scratch: no paid AI APIs, runs on a small Linux server.

---

## ✨ What it does

| For the shop owner | For the driver | For the customer |
|---|---|---|
| 🗺️ Tap the map to add orders | 🧭 One-tap navigation (Amap) | 🔗 Live tracking link |
| 🧠 Smart route + ETA for every stop | 📍 Live GPS updates the route | ⏰ Real arrival time |
| 🤖 Paste a WeChat message → AI handles it | 📞 One-tap call | 🇨🇳 Replies in Chinese |
| 📋 Orders history, search, edit, cancel | ✅ Mark delivered | |
| 📊 Daily stats (delivered, avg minutes) | | |
| 🔔 Sound alerts: new orders & LATE orders | | |

## 🤖 The AI agent

Paste any message and the agent decides what to do — **a human always confirms** before anything happens.

```
💬 "已送达 13912345678"
   ↓  🧠 intent model (6 intents · customer vs driver)
   ↓  🔍 parser (name · phone · address · deadline)
   ↓  🤖 decision → "🚚 Driver says 李明's order is delivered. Mark it?"  [OK]
```

| Who | Intent | Action |
|---|---|---|
| 👤 Customer | 📦 new order | fills the order form |
| 👤 Customer | ❌ cancel | finds the order by phone → confirm cancel |
| 👤 Customer | 🤔 question | replies in Chinese with the real ETA |
| 🚚 Driver | ✅ delivered | confirm → order marked delivered |
| 🚚 Driver | ⏰ delay | apology message for waiting customers |
| 🚚 Driver | 🏠 not home | one-tap call to the customer |
| ❓ | unsure | 🙋 asks a human (never guesses) |

**🎡 Data flywheel:** every decision can be confirmed (✅) or corrected (✏️) in the dashboard.
Each one becomes a new training example → `./retrain.sh` → a smarter agent.
Phone numbers are stripped before saving 🔒.

## 🧪 ML experiments (honest 30-message exam, new wording)

| Model | Accuracy | Confidently wrong |
|---|---|---|
| 🔤 **Bag-of-characters neural net (from scratch, numpy)** | **83%** 🏆 | **0** |
| 🧊 Qwen2.5-0.5B embeddings + classifier | 80% | 3 |
| 🩹 Qwen + LoRA (3 variants) | 43–60% | 4–9 |

**Takeaway:** with ~60 examples, a tiny well-understood model beats a frozen 500M-parameter LLM,
and LoRA overfits. More real data (the flywheel) is the path forward.
The repo also contains a from-scratch **Mini-GPT** (bigram → self-attention → transformer).

## 🏗️ Architecture

```
📱 Browser (dashboard · orders · tracking)
   ↓ HTTPS (ngrok)
🧱 ufw firewall (22 / 80 / 8080 only)
🚪 nginx  →  🔒 gunicorn (127.0.0.1 only)  →  🐍 Flask app
                                               ├── optimizer.py   route (nearest-neighbor + urgency)
                                               ├── geo.py         distances / ETA
                                               ├── agent.py       AI decisions
                                               ├── intent_nn.py   intent model
                                               └── order_parser.py
```

**Security:** password login (rate-limited, constant-time compare), SSH keys only (passwords disabled),
app server not exposed, secrets in environment variables, daily backups (14 days).

## 🚀 Run locally

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
export DASHBOARD_PASSWORD="choose-a-strong-one"
python3 intent_nn.py      # train the intent model
python3 fileapp.py        # → http://127.0.0.1:5000
```

## 🛠️ Deploy & improve

```bash
./deploy.sh      # checks code, smoke-tests the route, uploads, restarts, verifies
./retrain.sh     # pulls feedback from the server, retrains, deploys the smarter brain
```

## 🧰 Stack

Python · Flask · gunicorn · nginx · Leaflet + AutoNavi tiles · numpy · PyTorch · Hugging Face (Qwen, PEFT/LoRA) · Kali Linux · ufw

---

Built by [Youssef (Venompro22)](https://github.com/Venompro22) — student at Zhejiang A&F University 🇲🇦🇨🇳