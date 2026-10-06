import numpy as np
import torch
from transformers import AutoTokenizer, AutoModel
from intent_nn import DATA, LABELS, INTENTS, softmax, predict_intent

MODEL = "Qwen/Qwen2.5-0.5B"
device = "mps" if torch.backends.mps.is_available() else "cpu"

print("📥 Loading Qwen...")
tokenizer = AutoTokenizer.from_pretrained(MODEL)
qwen = AutoModel.from_pretrained(MODEL).to(device).eval()


@torch.no_grad()
def embed_many(sentences):
    """Each message → one 896-number 'meaning' vector from Qwen."""
    vecs = []
    for s in sentences:
        batch = tokenizer(s, return_tensors="pt").to(device)
        h = qwen(**batch).last_hidden_state[0, -1]   # last token: has "seen" the whole sentence
        vecs.append(h.float().cpu().numpy())
    return np.stack(vecs)


def train_head(X, y, epochs=300, lr=0.1):
    """Your classifier from intent_nn — same 4 steps: guess, error, gradient, fix."""
    W = np.zeros((X.shape[1], len(LABELS)))
    b = np.zeros(len(LABELS))
    Y = np.eye(len(LABELS))[y]
    for _ in range(epochs):
        p = softmax(X @ W + b)
        err = p - Y
        W -= lr * (X.T @ err / len(y))
        b -= lr * err.mean(axis=0)
    return W, b


texts = [t for t, _ in DATA]
labels = np.array([l for _, l in DATA])
print(f"🧭 Turning {len(texts)} messages into meaning vectors...")
E = embed_many(texts)

# ✂️ 1) Honest exam — SAME split as intent_nn (seed 42) so the comparison is fair
np.random.seed(42)
order = np.random.permutation(len(DATA))
cut = int(len(DATA) * 0.8)
tr, te = order[:cut], order[cut:]

mean, std = E[tr].mean(axis=0), E[tr].std(axis=0) + 1e-6   # 📏 rescale every dimension
Z = (E - mean) / std
W, b = train_head(Z[tr], labels[tr])
acc = (softmax(Z[te] @ W + b).argmax(axis=1) == labels[te]).mean()
print(f"\n🧪 Hidden test set → Qwen + your classifier: {acc:.0%}   (intent_nn: 82%)")

# 🎓 2) Final head trained on ALL the data
mean, std = E.mean(axis=0), E.std(axis=0) + 1e-6
Z = (E - mean) / std
W, b = train_head(Z, labels)
np.savez("qwen_head.npz", W=W, b=b, mean=mean, std=std)

# 🥊 3) HONEST tricky exam: clear meaning, but words that are NOT in DATA
tricky = [
    ("那就别送了", "❌ cancel"),          # "ok then, don't deliver"
    ("还没收到", "🤔 question"),          # "haven't received it yet"
    ("货已经放他门口", "✅ delivered"),    # "goods left at his door"
    ("屋里一直没人回应", "🏠 not home"),   # "no one answers inside"
    ("遇到事故绕道中", "⏰ delay"),        # "hit an accident, taking a detour"
]
print("\n🥊 Honest tricky exam (new words, same meaning):")
print(f"   {'message':<14} {'should be':<14} {'intent_nn':<16} {'Qwen + head'}")
score_old, score_new = 0, 0
for msg, expected in tricky:
    z = (embed_many([msg])[0] - mean) / std
    p = softmax(z @ W + b)
    new_label = LABELS[p.argmax()]
    old, _ = predict_intent(msg)
    old_label = LABELS[INTENTS.index(old)]
    score_old += old_label == expected
    score_new += new_label == expected
    print(f"   {msg:<14} {expected:<14} {old_label:<16} {new_label} ({p.max():.0%})")
print(f"\n🏆 Score → intent_nn: {score_old}/{len(tricky)} · Qwen + head: {score_new}/{len(tricky)}")

print("\n💾 Saved the classifier to qwen_head.npz")