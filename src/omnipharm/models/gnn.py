"""Model architectures.

MoleculeEncoder   molecule graph -> embedding (GINE message passing over atoms/bonds)
OmniPharmModel    drug PAIR -> adverse-event logits        (labels: TWOSIDES signals)
RegimenPatientModel  drug SET + PATIENT features -> adverse-event logits (labels: FAERS reports)

Both heads consume a drug-embedding table, so one forward pass of the encoder over all
molecules serves a whole batch; a never-seen molecule only needs its own graph encoded.
"""

from __future__ import annotations

import torch
import torch.nn.functional as F
from torch import nn
from torch_geometric.nn import GINEConv, global_add_pool, global_mean_pool

from omnipharm.chem.featurize import ATOM_DIM, BOND_DIM


class MoleculeEncoder(nn.Module):
    def __init__(self, hidden_dim: int = 256, num_layers: int = 4, dropout: float = 0.1):
        super().__init__()
        self.hidden_dim = hidden_dim
        self.node_emb = nn.Linear(ATOM_DIM, hidden_dim)
        self.edge_emb = nn.Linear(BOND_DIM, hidden_dim)
        self.convs = nn.ModuleList()
        self.norms = nn.ModuleList()
        for _ in range(num_layers):
            mlp = nn.Sequential(nn.Linear(hidden_dim, hidden_dim), nn.ReLU(), nn.Linear(hidden_dim, hidden_dim))
            self.convs.append(GINEConv(mlp, edge_dim=hidden_dim))
            self.norms.append(nn.LayerNorm(hidden_dim))
        self.dropout = dropout
        self.out = nn.Sequential(nn.Linear(hidden_dim * 2, hidden_dim), nn.LayerNorm(hidden_dim))

    def forward(self, x: torch.Tensor, edge_index: torch.Tensor, edge_attr: torch.Tensor,
                batch: torch.Tensor) -> torch.Tensor:
        h = self.node_emb(x)
        e = self.edge_emb(edge_attr) if edge_attr.size(0) > 0 else h.new_empty((0, h.size(-1)))
        for conv, norm in zip(self.convs, self.norms, strict=True):
            h = h + F.dropout(F.relu(norm(conv(h, edge_index, e))), self.dropout, self.training)
        n = int(batch.max()) + 1 if batch.numel() else 0
        pooled = torch.cat([global_add_pool(h, batch, n), global_mean_pool(h, batch, n)], dim=-1)
        return self.out(pooled)

    def encode(self, g) -> torch.Tensor:  # g: torch_geometric Batch/Data
        batch = getattr(g, "batch", None)
        if batch is None:
            batch = torch.zeros(g.x.size(0), dtype=torch.long, device=g.x.device)
        return self(g.x, g.edge_index, g.edge_attr, batch)


class FingerprintEncoder(nn.Module):
    """Baseline: Morgan fingerprint bits -> embedding (no message passing). Used only to
    measure how much the graph network adds over a classic cheminformatics representation."""

    def __init__(self, n_bits: int = 2048, hidden_dim: int = 256, dropout: float = 0.1):
        super().__init__()
        self.hidden_dim = hidden_dim
        self.net = nn.Sequential(nn.Linear(n_bits, hidden_dim * 2), nn.ReLU(), nn.Dropout(dropout),
                                 nn.Linear(hidden_dim * 2, hidden_dim), nn.LayerNorm(hidden_dim))

    def encode(self, fp: torch.Tensor) -> torch.Tensor:
        return self.net(fp)


class OmniPharmModel(nn.Module):
    """Symmetric drug-pair model: f(a, b) == f(b, a)."""

    def __init__(self, num_classes: int, hidden_dim: int = 256, dropout: float = 0.2,
                 encoder: nn.Module | None = None):
        super().__init__()
        self.encoder = encoder if encoder is not None else MoleculeEncoder(hidden_dim)
        self.classifier = nn.Sequential(
            nn.Linear(hidden_dim * 3, hidden_dim * 2), nn.ReLU(), nn.Dropout(dropout),
            nn.Linear(hidden_dim * 2, hidden_dim), nn.ReLU(),
            nn.Linear(hidden_dim, num_classes),
        )

    def forward(self, emb: torch.Tensor, i: torch.Tensor, j: torch.Tensor) -> torch.Tensor:
        z1, z2 = emb[i], emb[j]
        return self.classifier(torch.cat([z1 * z2, z1 + z2, (z1 - z2).abs()], dim=-1))


class RegimenPatientModel(nn.Module):
    """Set of drugs (2+, order-free) fused with the patient's state.

    drug embeddings -> self-attention over the regimen (drugs "see" each other)
                    -> pooled regimen vector  ++  patient vector  -> MLP -> event logits
    """

    def __init__(self, num_classes: int, patient_dim: int, hidden_dim: int = 256, heads: int = 4,
                 layers: int = 2, dropout: float = 0.1):
        super().__init__()
        self.encoder = MoleculeEncoder(hidden_dim)
        layer = nn.TransformerEncoderLayer(hidden_dim, heads, hidden_dim * 2, dropout, batch_first=True,
                                           norm_first=True)
        self.regimen = nn.TransformerEncoder(layer, layers, enable_nested_tensor=False)
        self.patient = nn.Sequential(nn.Linear(patient_dim, 64), nn.ReLU(), nn.Linear(64, 64), nn.ReLU())
        self.head = nn.Sequential(
            nn.Linear(hidden_dim * 2 + 64, hidden_dim), nn.ReLU(), nn.Dropout(dropout),
            nn.Linear(hidden_dim, hidden_dim // 2), nn.ReLU(),
            nn.Linear(hidden_dim // 2, num_classes),
        )

    def forward(self, emb: torch.Tensor, drug_idx: torch.Tensor, patient: torch.Tensor) -> torch.Tensor:
        """emb [n_drugs, D]; drug_idx [B, K] (-1 = padding, every row has >= 1 drug); patient [B, P]."""
        mask = drug_idx >= 0
        h = self.regimen(emb[drug_idx.clamp(min=0)], src_key_padding_mask=~mask)
        m = mask.unsqueeze(-1)
        mean = (h * m).sum(1) / m.sum(1).clamp(min=1)
        mx = h.masked_fill(~m, -1e4).max(1).values
        return self.head(torch.cat([mean, mx, self.patient(patient)], dim=-1))
