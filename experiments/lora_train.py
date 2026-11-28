import torch
import torch.nn.functional as F
from transformers import AutoTokenizer, AutoModelForSequenceClassification
from peft import LoraConfig, get_peft_model
from intent_nn import DATA, INTENTS, LABELS, predict_intent
from eval_set import EVAL

MODEL = "Qwen/Qwen2.5-0.5B"
device = "mps" if torch.backends.mps.is_available() else "cpu"
torch.manual_seed(42)

# ---------------- 🧠 1) Qwen + a fresh 6-way classification layer ----------------
print("📥 Loading Qwen...")
tokenizer = AutoTokenizer.from_pretrained(MODEL)
if tokenizer.pad_token is None:
    tokenizer.pad_token = tokenizer.eos_token
model = AutoModelForSequenceClassification.from_pretrained(MODEL, num_labels=len(INTENTS))
model.config.pad_token_id = tokenizer.pad_token_id

# ---------------- 🩹 2) LIGHT LoRA: smaller patches, fewer places ----------------
lora = LoraConfig(
    task_type="SEQ_CLS",
    r=4,                                   # smaller rank (was 8) → less room to memorize
    lora_alpha=8,
    lora_dropout=0.1,
    target_modules=["q_proj", "v_proj"],   # only 2 of the 4 attention matrices
)
model = get_peft_model(model, lora).to(device)
trainable = sum(p.numel() for p in model.parameters() if p.requires_grad)
total = sum(p.numel() for p in model.parameters())
print(f"🩹 Training {trainable:,} of {total:,} parameters ({trainable / total:.3%})")

# ---------------- ✂️ 3) Split DATA: train + validation (EVAL stays locked 🔒) ----------------
texts = [t for t, _ in DATA]
labels = torch.tensor([l for _, l in DATA])
g = torch.Generator().manual_seed(0)
perm = torch.randperm(len(texts), generator=g)
cut = int(len(texts) * 0.8)
tr_idx, va_idx = perm[:cut], perm[cut:]
print(f"✂️ Train: {len(tr_idx)} · Validation: {len(va_idx)} · Final exam (locked): {len(EVAL)}")


@torch.no_grad()
def val_score():
    model.eval()
    batch = tokenizer([texts[j] for j in va_idx], padding=True, return_tensors="pt").to(device)
    out = model(**batch, labels=labels[va_idx].to(device))
    acc = (out.logits.argmax(-1).cpu() == labels[va_idx]).float().mean().item()
    model.train()
    return out.loss.item(), acc


# ---------------- 🏋️ 4) Train with early stopping ----------------
lora_params = [p for n, p in model.named_parameters() if p.requires_grad and "lora_" in n]
head_params = [p for n, p in model.named_parameters() if p.requires_grad and "score" in n]
optimizer = torch.optim.AdamW([
    {"params": lora_params, "lr": 1e-4},   # 🩹 small, careful changes to Qwen
    {"params": head_params, "lr": 2e-3},   # 🆕 the brand-new head learns 20× faster
], weight_decay=0.01)
MAX_EPOCHS, BATCH, PATIENCE = 50, 8, 5     # more room to learn, early stop decides when
print(f"⚙️ LoRA params: {sum(p.numel() for p in lora_params):,} · head params: {sum(p.numel() for p in head_params):,}")
best_loss, best_state, bad_epochs = float("inf"), None, 0

model.train()
for epoch in range(1, MAX_EPOCHS + 1):
    order = tr_idx[torch.randperm(len(tr_idx))]
    for i in range(0, len(order), BATCH):
        idx = order[i:i + BATCH]
        batch = tokenizer([texts[j] for j in idx], padding=True, return_tensors="pt").to(device)
        out = model(**batch, labels=labels[idx].to(device))
        optimizer.zero_grad()
        out.loss.backward()
        optimizer.step()

    v_loss, v_acc = val_score()
    mark = ""
    if v_loss < best_loss:
        best_loss, bad_epochs = v_loss, 0
        best_state = {n: p.detach().clone() for n, p in model.named_parameters() if p.requires_grad}
        mark = "  ⭐ best"
    else:
        bad_epochs += 1
    print(f"epoch {epoch:2d} | val loss {v_loss:.3f} | val acc {v_acc:.0%}{mark}")
    if bad_epochs >= PATIENCE:
        print(f"⏱️ Early stop: validation hasn't improved for {PATIENCE} epochs")
        break

with torch.no_grad():
    for n, p in model.named_parameters():
        if n in best_state:
            p.copy_(best_state[n])


# ---------------- 📊 5) The final exam — looked at ONCE ----------------
@torch.no_grad()
def lora_predict(text):
    model.eval()
    batch = tokenizer(text, return_tensors="pt").to(device)
    p = F.softmax(model(**batch).logits[0], dim=-1)
    return INTENTS[int(p.argmax())], float(p.max())


short = {i: LABELS[k] for k, i in enumerate(INTENTS)}
rows = []
for text, truth in EVAL:
    a, ca = predict_intent(text)
    q, cq = lora_predict(text)
    rows.append((text, truth, a, ca, q, cq))

print(f"\n{'message':<16} {'truth':<14} {'intent_nn':<18} {'Qwen + LoRA'}")
for text, truth, a, ca, q, cq in rows:
    m1 = "✅" if a == truth else "❌"
    m2 = "✅" if q == truth else "❌"
    print(f"{text:<16} {short[truth]:<14} {m1} {short[a]:<14} {m2} {short[q]} ({cq:.0%})")

n = len(rows)
acc_a = sum(a == t for _, t, a, _, _, _ in rows) / n
acc_q = sum(q == t for _, t, _, _, q, _ in rows) / n
cw_a = sum(a != t and ca >= 0.8 for _, t, a, ca, _, _ in rows)
cw_q = sum(q != t and cq >= 0.8 for _, t, _, _, q, cq in rows)
agree = [r for r in rows if r[2] == r[4]]
agree_ok = sum(r[2] == r[1] for r in agree)

print(f"\n📊 RESULTS on {n} new messages")
print(f"   🔤 intent_nn          accuracy {acc_a:.0%} · confidently wrong: {cw_a}")
print(f"   🧊 Qwen frozen        accuracy 80%  · confidently wrong: 3")
print(f"   🩹 LoRA (heavy, v1)   accuracy 53%  · confidently wrong: 4")
print(f"   🪶 LoRA (light, v2)   accuracy 43%  · confidently wrong: 7")
print(f"   🎯 LoRA (2 lrs, v3)   accuracy {acc_q:.0%} · confidently wrong: {cw_q}")
print(f"\n🤝 ENSEMBLE (intent_nn + LoRA v2): agree {len(agree)}/{n} → "
      f"{agree_ok} correct, {len(agree) - agree_ok} slipped · to human: {n - len(agree)}")

model.save_pretrained("lora_intent")
print("\n💾 LoRA patches saved to lora_intent/")
