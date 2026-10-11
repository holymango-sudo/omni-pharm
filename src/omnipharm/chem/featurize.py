"""Molecule standardization and graph featurization.

Turns a SMILES string into (a) a standardized RDKit molecule with stable
identifiers and (b) a PyTorch Geometric graph for the GNN.

Design rules (clinical-grade input handling):
* Never crash on user input. Every failure raises ``MoleculeError`` with a
  machine-readable ``code`` and a human-readable message.
* Standardize before featurizing: drug databases store salts and charged
  forms (e.g. "sertraline hydrochloride"); the active moiety must be used so
  the same drug always produces the same graph.
* Features are one-hot with an explicit "other" bucket, so unseen elements or
  states never index out of range.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import TYPE_CHECKING

from rdkit import Chem, RDLogger
from rdkit.Chem.MolStandardize import rdMolStandardize

if TYPE_CHECKING:
    from torch_geometric.data import Data

RDLogger.DisableLog("rdApp.*")  # we report errors ourselves

MAX_SMILES_LEN = 2000
MAX_HEAVY_ATOMS = 250  # small molecules; biologics (antibodies etc.) are out of scope for the GNN


class MoleculeErrorCode(StrEnum):
    EMPTY = "empty_input"
    TOO_LONG = "input_too_long"
    PARSE_FAILED = "smiles_parse_failed"
    STANDARDIZE_FAILED = "standardization_failed"
    NO_HEAVY_ATOMS = "no_heavy_atoms"
    TOO_LARGE = "molecule_too_large"


class MoleculeError(ValueError):
    def __init__(self, code: MoleculeErrorCode, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


# --------------------------------------------------------------------------- #
# Standardization
# --------------------------------------------------------------------------- #


@dataclass(frozen=True, slots=True)
class StandardMolecule:
    mol: Chem.Mol
    canonical_smiles: str
    inchikey: str
    input_smiles: str
    removed_fragments: int


_cleanup_params = rdMolStandardize.CleanupParameters()
_fragment_chooser = rdMolStandardize.LargestFragmentChooser(preferOrganic=True)
_uncharger = rdMolStandardize.Uncharger()


def standardize(smiles: str) -> StandardMolecule:
    """Parse and standardize a SMILES string to its active (parent) moiety."""
    if smiles is None or not smiles.strip():
        raise MoleculeError(MoleculeErrorCode.EMPTY, "SMILES is empty")
    smiles = smiles.strip()
    if len(smiles) > MAX_SMILES_LEN:
        raise MoleculeError(
            MoleculeErrorCode.TOO_LONG, f"SMILES longer than {MAX_SMILES_LEN} characters"
        )

    mol = Chem.MolFromSmiles(smiles)
    if mol is None:
        raise MoleculeError(MoleculeErrorCode.PARSE_FAILED, f"Invalid SMILES: {smiles!r}")

    n_frags_before = len(Chem.GetMolFrags(mol))
    try:
        mol = rdMolStandardize.Cleanup(mol, _cleanup_params)
        mol = _fragment_chooser.choose(mol)
        mol = _uncharger.uncharge(mol)
        Chem.SanitizeMol(mol)
    except Exception as exc:  # RDKit raises a variety of C++-mapped exceptions
        raise MoleculeError(
            MoleculeErrorCode.STANDARDIZE_FAILED, f"Standardization failed: {exc}"
        ) from exc

    n_heavy = mol.GetNumHeavyAtoms()
    if n_heavy == 0:
        raise MoleculeError(MoleculeErrorCode.NO_HEAVY_ATOMS, "Molecule has no heavy atoms")
    if n_heavy > MAX_HEAVY_ATOMS:
        raise MoleculeError(
            MoleculeErrorCode.TOO_LARGE,
            f"Molecule has {n_heavy} heavy atoms (limit {MAX_HEAVY_ATOMS}); "
            "biologics are handled by the evidence layer, not the structure model",
        )

    return StandardMolecule(
        mol=mol,
        canonical_smiles=Chem.MolToSmiles(mol),
        inchikey=Chem.MolToInchiKey(mol),
        input_smiles=smiles,
        removed_fragments=n_frags_before - len(Chem.GetMolFrags(mol)),
    )


# --------------------------------------------------------------------------- #
# Featurization
# --------------------------------------------------------------------------- #

_ELEMENTS = [
    "B", "C", "N", "O", "F", "Na", "Mg", "Si", "P", "S", "Cl", "K", "Ca", "Fe", "Co",
    "Cu", "Zn", "As", "Se", "Br", "Ag", "I", "Pt", "Au", "Hg", "Bi", "Gd", "Li",
]  # fmt: skip
_DEGREES = [0, 1, 2, 3, 4, 5, 6]
_FORMAL_CHARGES = [-2, -1, 0, 1, 2]
_NUM_HS = [0, 1, 2, 3, 4]
_HYBRIDIZATIONS = [
    Chem.HybridizationType.SP,
    Chem.HybridizationType.SP2,
    Chem.HybridizationType.SP3,
    Chem.HybridizationType.SP3D,
    Chem.HybridizationType.SP3D2,
]
_CHIRAL_TAGS = [
    Chem.ChiralType.CHI_UNSPECIFIED,
    Chem.ChiralType.CHI_TETRAHEDRAL_CW,
    Chem.ChiralType.CHI_TETRAHEDRAL_CCW,
]
_RING_SIZES = [3, 4, 5, 6, 7, 8]

_BOND_TYPES = [
    Chem.BondType.SINGLE,
    Chem.BondType.DOUBLE,
    Chem.BondType.TRIPLE,
    Chem.BondType.AROMATIC,
]
_BOND_STEREO = [
    Chem.BondStereo.STEREONONE,
    Chem.BondStereo.STEREOANY,
    Chem.BondStereo.STEREOZ,
    Chem.BondStereo.STEREOE,
    Chem.BondStereo.STEREOCIS,
    Chem.BondStereo.STEREOTRANS,
]


def _one_hot(value: object, choices: list) -> list[float]:  # type: ignore[type-arg]
    """One-hot with a trailing 'other' slot (never raises)."""
    vec = [0.0] * (len(choices) + 1)
    try:
        vec[choices.index(value)] = 1.0
    except ValueError:
        vec[-1] = 1.0
    return vec


def atom_features(atom: Chem.Atom) -> list[float]:
    ring_info = atom.GetOwningMol().GetRingInfo()
    idx = atom.GetIdx()
    return (
        _one_hot(atom.GetSymbol(), _ELEMENTS)
        + _one_hot(atom.GetTotalDegree(), _DEGREES)
        + _one_hot(atom.GetFormalCharge(), _FORMAL_CHARGES)
        + _one_hot(atom.GetTotalNumHs(), _NUM_HS)
        + _one_hot(atom.GetHybridization(), _HYBRIDIZATIONS)
        + _one_hot(atom.GetChiralTag(), _CHIRAL_TAGS)
        + [float(atom.GetIsAromatic()), float(atom.IsInRing())]
        + [float(ring_info.IsAtomInRingOfSize(idx, s)) for s in _RING_SIZES]
        + [atom.GetMass() / 100.0]
    )


def bond_features(bond: Chem.Bond) -> list[float]:
    return (
        _one_hot(bond.GetBondType(), _BOND_TYPES)
        + _one_hot(bond.GetStereo(), _BOND_STEREO)
        + [float(bond.GetIsConjugated()), float(bond.IsInRing())]
    )


def _dummy_mol_dims() -> tuple[int, int]:
    m = Chem.MolFromSmiles("CC")
    return len(atom_features(m.GetAtomWithIdx(0))), len(bond_features(m.GetBondWithIdx(0)))


ATOM_DIM, BOND_DIM = _dummy_mol_dims()


def mol_to_graph(std: StandardMolecule) -> Data:
    """Convert a standardized molecule to a PyG ``Data`` graph (undirected edges)."""
    import torch
    from torch_geometric.data import Data

    mol = std.mol
    x = torch.tensor([atom_features(a) for a in mol.GetAtoms()], dtype=torch.float32)

    src: list[int] = []
    dst: list[int] = []
    eattr: list[list[float]] = []
    for bond in mol.GetBonds():
        i, j = bond.GetBeginAtomIdx(), bond.GetEndAtomIdx()
        f = bond_features(bond)
        src += [i, j]
        dst += [j, i]
        eattr += [f, f]

    if src:
        edge_index = torch.tensor([src, dst], dtype=torch.long)
        edge_attr = torch.tensor(eattr, dtype=torch.float32)
    else:  # single-atom molecule (e.g. lithium): valid graph with no edges
        edge_index = torch.empty((2, 0), dtype=torch.long)
        edge_attr = torch.empty((0, BOND_DIM), dtype=torch.float32)

    return Data(
        x=x,
        edge_index=edge_index,
        edge_attr=edge_attr,
        num_nodes=x.size(0),
        smiles=std.canonical_smiles,
        inchikey=std.inchikey,
    )


def smiles_to_graph(smiles: str) -> Data:
    """Convenience: standardize + featurize. Raises ``MoleculeError`` on bad input."""
    return mol_to_graph(standardize(smiles))
