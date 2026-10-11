"""OMNI-PHARM environment verification.

Run: .venv\\Scripts\\python.exe scripts\\verify_env.py

Checks that every component required for training and inference is present
and actually works on this machine (not just importable). Exits non-zero on
any failure so it can be used as a gate before training or demos.
"""

from __future__ import annotations

import importlib
import platform
import sys
import time
from collections.abc import Callable

RESULTS: list[tuple[str, bool, str]] = []


def check(name: str) -> Callable[[Callable[[], str]], Callable[[], str]]:
    def deco(fn: Callable[[], str]) -> Callable[[], str]:
        try:
            detail = fn()
            RESULTS.append((name, True, detail))
        except Exception as exc:  # report every failure, never abort the gate
            RESULTS.append((name, False, f"{type(exc).__name__}: {exc}"))
        return fn

    return deco


@check("python")
def _python() -> str:
    if sys.version_info[:2] != (3, 11):
        raise RuntimeError(f"Python 3.11 required, got {platform.python_version()}")
    return platform.python_version()


@check("torch + CUDA")
def _torch() -> str:
    import torch

    if not torch.cuda.is_available():
        raise RuntimeError("CUDA not available")
    cap = torch.cuda.get_device_capability(0)
    arch = f"sm_{cap[0]}{cap[1]}"
    if arch not in torch.cuda.get_arch_list():
        raise RuntimeError(f"{arch} not in compiled arch list {torch.cuda.get_arch_list()}")
    name = torch.cuda.get_device_name(0)
    total = torch.cuda.get_device_properties(0).total_memory / 2**30
    return f"torch {torch.__version__}, {name}, {arch}, {total:.1f} GiB"


@check("GPU compute (matmul + autograd + AMP)")
def _gpu_compute() -> str:
    import torch

    dev = torch.device("cuda")
    a = torch.randn(4096, 4096, device=dev, requires_grad=True)
    b = torch.randn(4096, 4096, device=dev)
    a16, b16 = a.detach().bfloat16(), b.bfloat16()
    for _ in range(3):  # warm-up: exclude one-time CUDA/cuBLAS initialization
        _ = a16 @ b16
    torch.cuda.synchronize()
    t0 = time.perf_counter()
    for _ in range(10):
        _ = a16 @ b16
    torch.cuda.synchronize()
    dt = time.perf_counter() - t0
    with torch.autocast("cuda", dtype=torch.bfloat16):
        c = a @ b
    c.float().sum().backward()
    torch.cuda.synchronize()
    if a.grad is None or not torch.isfinite(a.grad).all():
        raise RuntimeError("autograd produced invalid gradients")
    tflops = 10 * 2 * 4096**3 / dt / 1e12
    return f"{tflops:.1f} TFLOPS (bf16, rough)"


@check("rdkit")
def _rdkit() -> str:
    from rdkit import Chem, rdBase
    from rdkit.Chem import AllChem

    mol = Chem.AddHs(Chem.MolFromSmiles("CC(=O)Nc1ccc(O)cc1"))  # acetaminophen
    if AllChem.EmbedMolecule(mol, randomSeed=42) != 0:
        raise RuntimeError("3D embedding failed")
    return f"rdkit {rdBase.rdkitVersion}, 3D conformer OK ({mol.GetNumAtoms()} atoms)"


@check("torch_geometric (GPU forward)")
def _pyg() -> str:
    import torch
    import torch_geometric
    from torch_geometric.nn import GINEConv, global_add_pool

    x = torch.randn(10, 16, device="cuda")
    ei = torch.tensor([[0, 1, 2, 3], [1, 2, 3, 4]], device="cuda")
    ea = torch.randn(4, 16, device="cuda")
    conv = GINEConv(torch.nn.Linear(16, 16), edge_dim=16).cuda()
    out = global_add_pool(conv(x, ei, ea), torch.zeros(10, dtype=torch.long, device="cuda"))
    if out.shape != (1, 16):
        raise RuntimeError(f"unexpected shape {tuple(out.shape)}")
    return f"torch_geometric {torch_geometric.__version__}"


for mod in ["numpy", "pandas", "pyarrow", "sklearn", "fastapi", "uvicorn", "httpx", "pydantic"]:

    def _mk(m: str = mod) -> Callable[[], str]:
        def _f() -> str:
            module = importlib.import_module(m)
            return getattr(module, "__version__", "ok")

        return _f

    check(mod)(_mk())


def main() -> int:
    width = max(len(n) for n, _, _ in RESULTS)
    failed = 0
    for name, ok, detail in RESULTS:
        mark = "PASS" if ok else "FAIL"
        failed += not ok
        print(f"[{mark}] {name:<{width}}  {detail}")
    print(f"\n{len(RESULTS) - failed}/{len(RESULTS)} checks passed")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
