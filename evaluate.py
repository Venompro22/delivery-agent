import numpy as np
import torch
from transformers import AutoTokenizer, AutoModel
from intent_nn import LABELS, INTENTS, softmax, predict_intent
from eval_set import EVAL

MODEL = "Qwen/Qwen2.5-0.5B"
device = "mps" if torch.backends.mps.is_available() else "cpu"
print("📥 Loading Qwen + your trained head...")
tokenizer = AutoTokenizer.from_pretrained(MODEL)
qwen = AutoModel.from_pretrained(MODEL).to(device).eval()
head = np.load("qwen_head.npz")


@torch.no_grad()
def qwen_predict(text):
    batch = tokenizer(text, return_tensors="pt").to(device)
    h = qwen(**batch).last_hidden_state[0, -1].float().cpu().numpy()
    p = softmax(((h - head["mean"]) / head["std"]) @ head["W"] + head["b"])
    return INTENTS[p.argmax()], float(p.max())


rows = []
for text, truth in EVAL:
    a, ca = predict_intent(text)      # 🔤 intent_nn (characters)
    q, cq = qwen_predict(text)        # 🧠 Qwen + head (meaning)
    rows.append((text, truth, a, ca, q, cq))

short = {i: LABELS[k] for k, i in enumerate(INTENTS)}
print(f"\n{'message':<16} {'truth':<14} {'intent_nn':<18} {'Qwen + head'}")
for text, truth, a, ca, q, cq in rows:
    m1 = "✅" if a == truth else "❌"
    m2 = "✅" if q == truth else "❌"
    print(f"{text:<16} {short[truth]:<14} {m1} {short[a]:<14} {m2} {short[q]} ({cq:.0%})")

n = len(rows)
acc_a = sum(a == t for _, t, a, _, _, _ in rows) / n
acc_q = sum(q == t for _, t, _, _, q, _ in rows) / n
conf_wrong_a = sum(a != t and ca >= 0.8 for _, t, a, ca, _, _ in rows)
conf_wrong_q = sum(q != t and cq >= 0.8 for _, t, _, _, q, cq in rows)

agree = [r for r in rows if r[2] == r[4]]
agree_ok = sum(r[2] == r[1] for r in agree)
slipped = len(agree) - agree_ok

print(f"\n📊 RESULTS on {n} new messages")
print(f"   🔤 intent_nn     accuracy {acc_a:.0%} · confidently wrong (≥80%): {conf_wrong_a}")
print(f"   🧠 Qwen + head   accuracy {acc_q:.0%} · confidently wrong (≥80%): {conf_wrong_q}")
print(f"\n🤝 ENSEMBLE (act only when both agree, else ask a human)")
print(f"   agree on {len(agree)}/{n} → {agree_ok} correct, {slipped} wrong slipped through")
print(f"   sent to a human: {n - len(agree)}/{n}")