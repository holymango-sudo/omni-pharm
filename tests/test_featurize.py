"""Tests for molecule standardization and featurization."""

from __future__ import annotations

import pytest

from omnipharm.chem.featurize import (
    ATOM_DIM,
    BOND_DIM,
    MAX_SMILES_LEN,
    MoleculeError,
    MoleculeErrorCode,
    smiles_to_graph,
    standardize,
)

ACETAMINOPHEN = "CC(=O)Nc1ccc(O)cc1"
WARFARIN = "CC(=O)CC(c1ccccc1)c1c(O)c2ccccc2oc1=O"
SERTRALINE = "CN[C@H]1CC[C@@H](c2ccc(Cl)c(Cl)c2)c2ccccc21"
SERTRALINE_HCL = SERTRALINE + ".Cl"


class TestStandardize:
    def test_salt_and_free_base_map_to_same_identity(self) -> None:
        free = standardize(SERTRALINE)
        salt = standardize(SERTRALINE_HCL)
        assert free.inchikey == salt.inchikey
        assert free.canonical_smiles == salt.canonical_smiles
        assert salt.removed_fragments == 1

    def test_canonicalization_is_order_independent(self) -> None:
        a = standardize(ACETAMINOPHEN)
        b = standardize("Oc1ccc(NC(C)=O)cc1")
        assert a.canonical_smiles == b.canonical_smiles

    def test_charged_acid_is_neutralized(self) -> None:
        ionized = standardize("CC(=O)Nc1ccc([O-])cc1.[Na+]")
        assert ionized.inchikey == standardize(ACETAMINOPHEN).inchikey

    def test_known_inchikey(self) -> None:
        # Reference InChIKey for paracetamol (PubChem CID 1983)
        assert standardize(ACETAMINOPHEN).inchikey == "RZVAJINKPMORJF-UHFFFAOYSA-N"

    @pytest.mark.parametrize(
        ("smiles", "code"),
        [
            ("", MoleculeErrorCode.EMPTY),
            ("   ", MoleculeErrorCode.EMPTY),
            ("C1CC", MoleculeErrorCode.PARSE_FAILED),  # unclosed ring
            ("not a molecule", MoleculeErrorCode.PARSE_FAILED),
            ("C(C)(C)(C)(C)C", MoleculeErrorCode.PARSE_FAILED),  # pentavalent carbon
            ("C" * (MAX_SMILES_LEN + 1), MoleculeErrorCode.TOO_LONG),
            ("C" * 300, MoleculeErrorCode.TOO_LARGE),
        ],
    )
    def test_bad_input_raises_typed_error(self, smiles: str, code: MoleculeErrorCode) -> None:
        with pytest.raises(MoleculeError) as info:
            standardize(smiles)
        assert info.value.code == code
        assert info.value.message


class TestGraph:
    @pytest.mark.parametrize("smiles", [ACETAMINOPHEN, WARFARIN, SERTRALINE_HCL])
    def test_graph_shapes(self, smiles: str) -> None:
        std = standardize(smiles)
        g = smiles_to_graph(smiles)
        n_atoms = std.mol.GetNumAtoms()
        n_bonds = std.mol.GetNumBonds()
        assert g.x.shape == (n_atoms, ATOM_DIM)
        assert g.edge_index.shape == (2, 2 * n_bonds)
        assert g.edge_attr.shape == (2 * n_bonds, BOND_DIM)
        assert int(g.edge_index.max()) < n_atoms
        assert g.inchikey == std.inchikey

    def test_edges_are_symmetric(self) -> None:
        g = smiles_to_graph(WARFARIN)
        pairs = set(map(tuple, g.edge_index.t().tolist()))
        assert all((j, i) in pairs for i, j in pairs)

    def test_single_atom_molecule(self) -> None:
        g = smiles_to_graph("C")
        assert g.x.shape == (1, ATOM_DIM)
        assert g.edge_index.shape == (2, 0)
        assert g.edge_attr.shape == (0, BOND_DIM)

    def test_unseen_element_uses_other_bucket(self) -> None:
        g = smiles_to_graph("[U]")  # uranium is not in the element vocabulary
        assert g.x.shape == (1, ATOM_DIM)

    def test_features_are_finite_and_one_hot_valid(self) -> None:
        import torch

        g = smiles_to_graph(WARFARIN)
        assert torch.isfinite(g.x).all()
        assert torch.isfinite(g.edge_attr).all()

    def test_featurization_is_deterministic(self) -> None:
        import torch

        a, b = smiles_to_graph(SERTRALINE_HCL), smiles_to_graph(SERTRALINE)
        assert torch.equal(a.x, b.x)
        assert torch.equal(a.edge_index, b.edge_index)
