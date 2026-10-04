import random
import torch
import torch.nn as nn
import torch.nn.functional as F
from intent_nn import DATA

# ---------------- ⚙️ Settings (hyperparameters) ----------------
block_size = 16      # how many previous characters the model can see
batch_size = 32
n_embd = 32          # 🐭 smaller brain (was 64) → can't just memorize
n_head = 4
n_layer = 2
dropout = 0.3        # 💧 forget 30% at random while training (was 0.1)
max_steps = 1500
eval_every = 100
learning_rate = 3e-3
device = "mps" if torch.backends.mps.is_available() else "cpu"
torch.manual_seed(1337)

# ---------------- 📚 Data (✂️ split by MESSAGE, after shuffling) ----------------
messages = [t for t, _ in DATA]
random.Random(0).shuffle(messages)
cut = int(len(messages) * 0.85)
train_msgs, val_msgs = messages[:cut], messages[cut:]

chars = sorted(set("\n".join(messages) + "\n"))
V = len(chars)
stoi = {c: i for i, c in enumerate(chars)}
itos = {i: c for c, i in stoi.items()}
encode = lambda s: [stoi[c] for c in s]
decode = lambda ids: "".join(itos[i] for i in ids)

train_data = torch.tensor(encode("\n".join(train_msgs) + "\n"), dtype=torch.long)
val_data = torch.tensor(encode("\n".join(val_msgs) + "\n"), dtype=torch.long)


def get_batch(split):
    d = train_data if split == "train" else val_data
    ix = torch.randint(len(d) - block_size, (batch_size,))
    x = torch.stack([d[i:i + block_size] for i in ix])
    y = torch.stack([d[i + 1:i + block_size + 1] for i in ix])
    return x.to(device), y.to(device)


# ---------------- 🧠 The model ----------------
class Head(nn.Module):
    """One self-attention head: Query · Key → weights → mix Values (with a causal mask)."""
    def __init__(self, head_size):
        super().__init__()
        self.key = nn.Linear(n_embd, head_size, bias=False)
        self.query = nn.Linear(n_embd, head_size, bias=False)
        self.value = nn.Linear(n_embd, head_size, bias=False)
        self.register_buffer("tril", torch.tril(torch.ones(block_size, block_size)))
        self.dropout = nn.Dropout(dropout)

    def forward(self, x):
        B, T, C = x.shape
        k, q, v = self.key(x), self.query(x), self.value(x)
        wei = q @ k.transpose(-2, -1) * k.shape[-1] ** -0.5
        wei = wei.masked_fill(self.tril[:T, :T] == 0, float("-inf"))
        wei = F.softmax(wei, dim=-1)
        wei = self.dropout(wei)
        return wei @ v


class MultiHead(nn.Module):
    def __init__(self):
        super().__init__()
        head_size = n_embd // n_head
        self.heads = nn.ModuleList([Head(head_size) for _ in range(n_head)])
        self.proj = nn.Linear(n_embd, n_embd)
        self.dropout = nn.Dropout(dropout)

    def forward(self, x):
        out = torch.cat([h(x) for h in self.heads], dim=-1)
        return self.dropout(self.proj(out))


class FeedForward(nn.Module):
    def __init__(self):
        super().__init__()
        self.net = nn.Sequential(
            nn.Linear(n_embd, 4 * n_embd), nn.ReLU(),
            nn.Linear(4 * n_embd, n_embd), nn.Dropout(dropout),
        )

    def forward(self, x):
        return self.net(x)


class Block(nn.Module):
    def __init__(self):
        super().__init__()
        self.sa = MultiHead()
        self.ffwd = FeedForward()
        self.ln1 = nn.LayerNorm(n_embd)
        self.ln2 = nn.LayerNorm(n_embd)

    def forward(self, x):
        x = x + self.sa(self.ln1(x))
        x = x + self.ffwd(self.ln2(x))
        return x


class MiniGPT(nn.Module):
    def __init__(self):
        super().__init__()
        self.tok_emb = nn.Embedding(V, n_embd)
        self.pos_emb = nn.Embedding(block_size, n_embd)
        self.blocks = nn.Sequential(*[Block() for _ in range(n_layer)])
        self.ln_f = nn.LayerNorm(n_embd)
        self.head = nn.Linear(n_embd, V)

    def forward(self, idx, targets=None):
        B, T = idx.shape
        x = self.tok_emb(idx) + self.pos_emb(torch.arange(T, device=idx.device))
        x = self.blocks(x)
        logits = self.head(self.ln_f(x))
        loss = None
        if targets is not None:
            loss = F.cross_entropy(logits.view(B * T, V), targets.view(B * T))
        return logits, loss


@torch.no_grad()
def estimate_loss(model):
    model.eval()
    out = {}
    for split in ("train", "val"):
        losses = [model(*get_batch(split))[1].item() for _ in range(20)]
        out[split] = sum(losses) / len(losses)
    model.train()
    return out


@torch.no_grad()
def write_message(model, max_len=40):
    model.eval()
    idx = torch.tensor([[stoi["\n"]]], device=device)
    out = []
    for _ in range(max_len):
        logits, _ = model(idx[:, -block_size:])
        probs = F.softmax(logits[:, -1, :], dim=-1)
        nxt = torch.multinomial(probs, num_samples=1)
        if nxt.item() == stoi["\n"]:
            break
        out.append(nxt.item())
        idx = torch.cat([idx, nxt], dim=1)
    return decode(out)


if __name__ == "__main__":
    model = MiniGPT().to(device)
    print(f"🧠 Mini-GPT: {sum(p.numel() for p in model.parameters()):,} parameters · device: {device}")
    print(f"📚 Train: {len(train_msgs)} messages · Val: {len(val_msgs)} hidden messages")
    optimizer = torch.optim.AdamW(model.parameters(), lr=learning_rate)

    best_val, best_state, best_step = float("inf"), None, 0
    for step in range(max_steps + 1):
        if step % eval_every == 0:
            l = estimate_loss(model)
            mark = ""
            if l["val"] < best_val:      # ⏱️ early stopping: remember the best moment
                best_val, best_step = l["val"], step
                best_state = {k: v.detach().clone() for k, v in model.state_dict().items()}
                mark = "  ⭐ best so far"
            print(f"step {step:4d} | train {l['train']:.3f} | val {l['val']:.3f}{mark}")
        xb, yb = get_batch("train")
        _, loss = model(xb, yb)
        optimizer.zero_grad(set_to_none=True)
        loss.backward()
        optimizer.step()

    model.load_state_dict(best_state)
    print(f"\n⏱️ Using the model from step {best_step} (val loss {best_val:.3f})")

    print("\n🤖 Messages written by YOUR GPT:")
    known = set(messages)
    generated = [write_message(model) for _ in range(12)]
    for m in generated:
        tag = "📋 copied" if m in known else "✨ NEW"
        print(f"   {tag}  {m}")
    new_count = sum(m not in known for m in generated)
    print(f"\n📏 Novelty: {new_count}/{len(generated)} messages are new (not copied from DATA)")

    torch.save(model.state_dict(), "mini_gpt.pt")
    print("💾 Saved to mini_gpt.pt")