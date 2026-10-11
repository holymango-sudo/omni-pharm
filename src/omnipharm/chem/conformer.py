"""3D conformer generation for the molecule viewer (RDKit ETKDGv3 + MMFF)."""

from __future__ import annotations

from rdkit import Chem
from rdkit.Chem import AllChem, Descriptors, rdMolDescriptors

MAX_ATOMS_WITH_H = 90  # above this, hydrogens are dropped so the viewer stays readable


def mol_3d(smiles: str, seed: int = 7) -> dict | None:
    """SMILES -> {"atoms": [[el, x, y, z], ...], "bonds": [[i, j, order], ...], "props": {...}} or None."""
    mol = Chem.MolFromSmiles(smiles)
    if mol is None:
        return None
    props = {
        "formula": rdMolDescriptors.CalcMolFormula(mol),
        "mw": round(Descriptors.MolWt(mol), 2),
        "logp": round(Descriptors.MolLogP(mol), 2),
        "tpsa": round(rdMolDescriptors.CalcTPSA(mol), 1),
        "hbd": rdMolDescriptors.CalcNumHBD(mol),
        "hba": rdMolDescriptors.CalcNumHBA(mol),
        "rings": rdMolDescriptors.CalcNumRings(mol),
        "heavy_atoms": mol.GetNumHeavyAtoms(),
    }
    mh = Chem.AddHs(mol)
    params = AllChem.ETKDGv3()
    params.randomSeed = seed
    params.maxIterations = 200
    if AllChem.EmbedMolecule(mh, params) != 0:
        params.useRandomCoords = True
        if AllChem.EmbedMolecule(mh, params) != 0:
            return {"atoms": [], "bonds": [], "props": props}
    try:
        AllChem.MMFFOptimizeMolecule(mh, maxIters=400)
    except Exception:
        pass
    if mh.GetNumAtoms() > MAX_ATOMS_WITH_H:
        mh = Chem.RemoveHs(mh)
    conf = mh.GetConformer()
    pos = conf.GetPositions()
    c = pos.mean(axis=0)
    atoms = [[a.GetSymbol(), *(round(float(v), 3) for v in pos[a.GetIdx()] - c)] for a in mh.GetAtoms()]
    bonds = [[b.GetBeginAtomIdx(), b.GetEndAtomIdx(),
              1.5 if b.GetIsAromatic() else float(b.GetBondTypeAsDouble())] for b in mh.GetBonds()]
    return {"atoms": atoms, "bonds": bonds, "props": props}
