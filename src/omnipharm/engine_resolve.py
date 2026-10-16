"""Names -> ingredients (ResolveMixin of Engine): Korean / English / Japanese product names, ingredient
names, foods, controlled substances, raw SMILES, the online PubChem fallback, search and display names."""

from __future__ import annotations

import json
import re
import unicodedata

import pandas as pd

from omnipharm import recreational
from omnipharm.chem.conformer import mol_3d
from omnipharm.chem.featurize import MoleculeError, standardize
from omnipharm.config import settings
from omnipharm.engine_base import (
    _LABEL_PREFIX,
    KO_NAME,
    Ingredient,
    ResolveError,
    _curated_smiles,
    _key,
    _ko_prefix,
    script,
)
from omnipharm.knowledge import (
    DRUG_SYNONYMS,
    FOODS,
    bio_key,
    drug_groups,
    food_key,
    lab_name,
    normalize_ingredient,
    resolve_korean,
    structureless_category,
)
from omnipharm.release import release_of


class ResolveMixin:
    # ------------------------------------------------------------------ resolution
    def _db(self, i: int, display: str | None = None) -> Ingredient:
        r = self.master.iloc[i]
        return Ingredient(r["name"], display or r["name"], r["std_smiles"], i, r["rxcui"])

    def _seen(self, g: Ingredient) -> bool:
        """Was this molecule part of the pair model's training pairs?"""
        return g.idx is not None and g.key in self.pair_seen

    def resolve(self, text: str) -> list[Ingredient]:
        return self.resolve_full(text)[0]

    def resolve_full(self, text: str) -> tuple[list[Ingredient], dict]:
        """Name / brand / Korean product / 'a+b' / SMILES -> (ingredients, info).
        info = {"product": 식약처 product name or None, "unresolved": [ingredient strings not analysable]}.
        Raises ResolveError (with candidates when a product name is ambiguous)."""
        text = (text or "").strip()
        if not text:
            raise ResolveError("빈 입력")
        info: dict = {"product": None, "country": None, "unresolved": [], "strength": None}
        fk = food_key(text)  # 술, 자몽주스, 우유, 흡연 ...
        if fk:
            ings = [self._ext_ingredient(f"food:{fk}")]
            food = self._food(text)  # what the nutrient DB adds: '시금치' is a vitamin K food and also rich in potassium
            if food:
                ings += [g for g in food[0] if g.key not in {x.key for x in ings}]
                return ings, {**info, "nutrients": food[1]["nutrients"]}
            return ings, info
        rk = recreational.key_of(text)  # 대마, 엑스터시, 물뽕, 파퍼스 ... (harm reduction; before products: 'cannabis'
        if rk:                          # is also in the name of a cosmetic)
            return self._controlled(rk), {**info, "controlled": recreational.info(rk)}
        smiles_like = text.isascii() and " " not in text and any(ch in text for ch in "()=#[]@0123456789")
        if smiles_like:
            return [self._from_smiles(text)], info
        jan = self.jp_names.get(unicodedata.normalize("NFKC", text))  # Japanese ingredient name (JAN)
        if jan:
            try:
                return self._resolve_names(jan.lower()), info
            except ResolveError:
                pass
        try:
            return self._resolve_names(text), info
        except ResolveError as e:
            if script(text) == "KR":  # Korean ingredient name ('인플릭시맵', '제미글립틴타르타르산염')
                from omnipharm.pipeline.strengths import KO_SALT

                k = self.kr_names.get(KO_SALT.sub("", re.sub(r"^(dl|d|l)-", "", text.replace(" ", ""))))
                if k and (k in self.name_idx or k in self.ext_ing or k.startswith(("bio:", "lab:"))):
                    return [self._ext_ingredient(k)], info
            food = self._food(text)  # 아메리카노, 스타벅스 아메리카노, 시금치, 바나나 ... (by what they contain)
            if food:
                return food[0], {**info, **food[1]}
            row = self._product_lookup(text)  # raises ResolveError with candidates if ambiguous
            if row is not None:
                ings = [self._ext_ingredient(k) for k in row["ingredients"]]
                return ings, {"product": row["name"], "country": row["country"], "release": release_of(row["name"]),
                              "unresolved": list(row["unresolved"]),
                              "strength": self.strengths.get((row["country"], str(row["item_seq"]))),
                              "label_ko": (self.label_ko.get(f"{_LABEL_PREFIX.get(row['kind'], 'D')}:{row['item_seq']}")
                                           if row["country"] == "KR" else None)}
            b = bio_key(text)  # peptide / protein product (위고비프리필드펜, 보톡스주 ...)
            if b:
                return [self._bio(b)], {**info, "product": text if text.lower() != b else None}
            g = self._online(text)  # any other country: INN / brand synonym via PubChem (needs internet)
            if g is not None:
                return [g], {**info, "product": f"{text} (PubChem)"}
            raise e

    def _food_search(self, q: str, limit: int = 4) -> list[dict]:
        """Foods whose lookup name starts with q, with what they contain (shortest names first)."""
        import bisect

        from omnipharm.pipeline.food_nutrients import KO, THRESHOLD, norm

        k = norm(q)
        if len(k) < 2 or not self._food_keys:
            return []
        i = bisect.bisect_left(self._food_keys, k)
        hits = []
        while i < len(self._food_keys) and self._food_keys[i].startswith(k) and len(hits) < 40:
            hits.append(self._food_keys[i])
            i += 1
        out = []
        for key in sorted(hits, key=len)[:limit]:
            r = self.food_nutr[key]
            parts = [f"{KO[c][0]} {r[c]:g}{KO[c][1]}" for c, t in THRESHOLD.items() if (r.get(c) or 0) >= t]
            out.append({"name": q if key == k else r.get("label") or r["name"], "type": "음식·영양성분",
                        "sub": f"{r['basis']}당 " + ", ".join(parts)})
        return out

    def _food(self, text: str) -> tuple[list[Ingredient], dict] | None:
        """A food / drink of the MFDS nutrient DB, as the ingredients that matter for medicines: caffeine (with its amount
        per mL / g, so '355 mL twice a day' is added to the caffeine of painkillers), vitamin K foods, potassium-rich and
        calcium-rich foods. None when the name is unknown or the food contains none of them."""
        from omnipharm.pipeline.food_nutrients import THRESHOLD, norm

        r = self.food_nutr.get(norm(text))
        if not r:
            return None
        has = {c: (r.get(c) or 0) >= t for c, t in THRESHOLD.items()}
        ings: list[Ingredient] = []
        strength = None
        if has["caffeine_mg"] and "caffeine" in self.name_idx:
            ings.append(self._db(self.name_idx["caffeine"]))
            unit = "mL" if r["basis"] == "100mL" else "g"
            strength = {"unit": unit, "per": unit, "mg": {"caffeine": r["caffeine_mg"] / 100}}
        if has["vitamin_k_ug"]:
            ings.append(self._ext_ingredient("food:vitamin k foods"))
        if has["potassium_mg"]:
            ings.append(self._ext_ingredient("food:potassium-rich foods"))
        if has["calcium_mg"]:  # (magnesium is shown only: food amounts are far below an antacid's)
            ings.append(self._ext_ingredient("food:calcium-rich foods"))
        if not ings:
            return None
        nutrients = {k: r[k] for k in THRESHOLD if r.get(k) is not None}
        label = r["name"] + (f" ({r['maker']})" if r.get("maker") else "")
        return ings, {"product": f"{label} · 식약처 식품영양성분DB", "strength": strength,
                      "nutrients": {"per": r["basis"], **nutrients, "n": int(r.get("n") or 1)}}

    def _controlled(self, key: str) -> list[Ingredient]:
        """A recreational / controlled substance -> its ingredients (database molecule or curated structure)."""
        s = recreational.SUBSTANCES[key]
        if "parts" in s:
            return [g for p in s["parts"] for g in self._controlled(p)]
        if "db" in s:
            return [g for n in ([s["db"]] if isinstance(s["db"], str) else s["db"]) for g in self._resolve_names(n)]
        smi = _curated_smiles(s["smiles"])
        if smi[1] in self.inchikey_idx:
            return [self._db(self.inchikey_idx[smi[1]])]
        return [Ingredient(f"ing:{key}", key, smi[0], None)]

    @staticmethod
    def _bio(name: str) -> Ingredient:
        return Ingredient(f"bio:{name}", name, "", None)

    def _ext_ingredient(self, key: str) -> Ingredient:
        if key in self.name_idx:
            return self._db(self.name_idx[key])
        if key.startswith("bio:"):
            return self._bio(key.removeprefix("bio:"))
        if key.startswith("food:"):
            return Ingredient(key, key.removeprefix("food:"), "", None)
        if key.startswith("lab:"):
            b = bio_key(key.removeprefix("lab:"))
            return self._bio(b) if b else Ingredient(key, key.removeprefix("lab:"), "", None)
        r = self.ext_ing[key]
        return Ingredient(key, r.name, r.std_smiles, None)

    def _candidates(self, rows: pd.DataFrame) -> list[dict]:
        rows = rows.sort_values(["active", "otc", "name"], ascending=[False, False, True]).head(12)
        return [{"name": r["name"], "kind": self._kind(r), "company": r["company"],
                 "ingredients": ", ".join(self._display_key(k) for k in r["ingredients"])} for _, r in rows.iterrows()]

    @staticmethod
    def _kind(r) -> str:
        if r["country"] == "KR":
            if r["kind"] in _LABEL_PREFIX:  # 의약외품 / 건강기능식품 (they were shown as '전문약')
                return r["kind"]
            return "일반약" if r["otc"] else "전문약"
        return f"{r['country']} {'OTC' if r['otc'] else 'Rx' if r['kind'] else ''}".strip()

    def _product_lookup(self, text: str) -> pd.Series | None:
        """Marketed product name -> one row. Korean text searches Korean products, Latin text the US/CA/EU/FR
        ones. Several products whose composition differs -> ResolveError with candidates."""
        if not len(self.products):
            return None
        from omnipharm.pipeline import mfds, world

        P = self.products
        pool = self._pool(script(text))
        key = _key(text)
        exact = [i for i in self.prod_by_key.get(key, []) if i in pool.index]
        if exact:
            rows = P.loc[exact]
        else:
            sc = script(text)
            b = (mfds.base_name(text.replace(" ", "")) if sc == "KR" else
                 world.jp_base_name(text) if sc == "JP" else world.base_name(text))
            rows = pool[pool["base"] == b]
            if not len(rows):
                rows = pool[pool["base"].str.startswith(b) | pool["key"].str.startswith(key)]
            if not len(rows) and len(key) >= 3:  # company prefix: '정로환' -> '동성정로환에프정'
                rows = pool[pool["key"].str.contains(key, regex=False)]
        if not len(rows):
            return None
        if rows["active"].any():
            rows = rows[rows["active"]]
        comps = rows["ingredients"].map(lambda v: tuple(sorted(v)))
        if comps.nunique() == 1:
            # same composition, different products (타이레놀정500 / 타이레놀산500 / 타이레놀8시간이알서방정): the one whose
            # name is closest to what was typed - its strength per unit and release form are what the dose refers to
            import difflib

            rel = release_of(text)

            def score(r) -> float:
                k = r["key"]
                return ((k == key) * 100 + k.startswith(key) * 40 + (key in k) * 20 + (release_of(r["name"]) == rel) * 15
                        + difflib.SequenceMatcher(None, key, k).ratio() * 10 + bool(r["otc"]))
            return rows.loc[max(rows.index, key=lambda i: score(rows.loc[i]))]
        raise ResolveError(f"'{text}'에 해당하는 제품이 {len(rows)}개이며 성분이 서로 다릅니다 — 정확한 제품을 선택하세요.",
                           self._candidates(rows))

    def _pool(self, sc: str) -> pd.DataFrame:
        P = self.products
        if sc in ("KR", "JP"):
            return P[P["country"].eq(sc)]
        return P[~P["country"].isin(["KR", "JP"])]

    def _online(self, text: str) -> Ingredient | None:
        """Last resort for drugs of countries without a bulk database: PubChem name/synonym lookup (cached)."""
        if not settings.online_lookup or not text.isascii() or len(text) > 60 or len(text.split()) > 4:
            return None
        name = text.lower().strip()
        if name not in self._online_cache:
            import urllib.parse

            import httpx

            url = ("https://pubchem.ncbi.nlm.nih.gov/rest/pug/compound/name/"
                   f"{urllib.parse.quote(name)}/property/IsomericSMILES,SMILES/JSON")
            try:
                r = httpx.get(url, timeout=5.0)
            except Exception:  # offline / slow network: just say we do not know it
                return None
            if r.status_code in (400, 404):
                res = None
            elif r.status_code == 200:
                p = r.json()["PropertyTable"]["Properties"][0]
                smi = p.get("IsomericSMILES") or p.get("SMILES")
                res = {"smiles": smi} if smi else None
            else:
                return None  # transient error: do not cache
            with self._online_lock:
                self._online_cache[name] = res
                try:
                    self._online_cache_f.write_text(json.dumps(self._online_cache), encoding="utf-8")
                except OSError:
                    pass
        info = self._online_cache.get(name)
        if not info:
            return None
        try:
            std = standardize(info["smiles"])
        except MoleculeError:
            return None
        if std.inchikey in self.inchikey_idx:
            return self._db(self.inchikey_idx[std.inchikey])
        if std.mol.GetNumHeavyAtoms() > 100:
            return None
        return Ingredient(f"ing:{name}", name, std.canonical_smiles, None)

    def _display_key(self, key: str) -> str:
        bare = key.removeprefix("ing:").removeprefix("bio:").removeprefix("lab:")  # 'lab:ginseng' -> 인삼
        return KO_NAME.get(key) or KO_NAME.get(bare) or _ko_prefix(bare) or bare

    def _resolve_names(self, text: str) -> list[Ingredient]:
        """Curated Korean names, 'a+b' ingredient lists, English generic/brand names."""
        names = resolve_korean(text) or []
        out, missing = [], []
        for n in names:
            n = DRUG_SYNONYMS.get(n.strip().lower(), n.strip().lower())
            n = n if n in self.name_idx or f"ing:{n}" in self.ext_ing else self.brand.get(n, n)
            if n not in self.name_idx and f"ing:{n}" not in self.ext_ing:  # "loxoprofen sodium hydrate"
                n = normalize_ingredient(n, self.known) or n
            if n in self.name_idx:
                out.append(self._db(self.name_idx[n]))
            elif f"ing:{n}" in self.ext_ing:
                out.append(self._ext_ingredient(f"ing:{n}"))
            elif bio_key(n):
                out.append(self._bio(bio_key(n)))
            elif n in self.syn:  # any approved drug's trade name / INN / synonym world-wide (ChEMBL)
                out.append(self._ext_ingredient(self.syn[n]))
            elif (n in self.label_keys or structureless_category(n) != "기타" or drug_groups(n) != {n}
                  or not text.isascii()):  # curated Korean name (KR_INGREDIENTS / KR_BRANDS) without a structure
                # any substance with an FDA label (antibodies, vitamins ...) or a recognised herbal / supplement
                out.append(self._ext_ingredient(f"lab:{lab_name(n) or n}"))
            else:
                missing.append(n)
        if out and not missing:
            return out
        if not text.isascii() or " " in text or "+" in text or not names:
            why = f"분석 DB에 없는 성분: {', '.join(missing)}" if missing else "알 수 없는 약물명"
            raise ResolveError(why + " (생물의약품·복합제일 수 있음 — 성분명 또는 SMILES 구조식으로 입력)")
        return [self._from_smiles(text)]  # an ASCII word that is no known name: maybe a SMILES

    def search(self, q: str, limit: int = 15) -> list[dict]:
        """Autocomplete across Korean products, Korean/English ingredient and brand names."""
        q = (q or "").strip()
        if not q:
            return []
        ql, qn = q.lower(), q.replace(" ", "")
        out: list[dict] = []
        for v in FOODS.values():  # 술, 자몽주스, 우유, 흡연 ...
            if any(a.lower().startswith(ql) for a in v["aliases"]):
                out.append({"name": v["aliases"][0], "type": "음식·생활", "sub": v["ko"]})
        out += recreational.search(q)  # 대마, 엑스터시, 케타민 ...
        out += self._food_search(q)  # 아메리카노, 시금치 ... (MFDS food nutrient DB)
        cat = self.catalog()
        for kind, names in (("제품", cat["korean_brands"]), ("성분", cat["korean_ingredients"]),
                            ("ingredient", cat["ingredients"]), ("brand", cat["brands"])):
            for n in names:
                if n.lower().startswith(ql):
                    out.append({"name": n, "type": kind, "sub": ""})
        if len(self.products) and len(qn) >= 2:
            pool = self._pool(script(q))
            key = _key(q)
            m = pool[pool["key"].str.contains(re.escape(key), regex=True)]
            m = m.assign(_pre=m["key"].str.startswith(key), _len=m["name"].str.len())
            m = m.sort_values(["_pre", "active", "otc", "_len"], ascending=[False, False, False, True])
            m = m.drop_duplicates("key").head(limit)
            for _, r in m.iterrows():
                ings = ", ".join(self._display_key(k) for k in r["ingredients"])
                extra = f" +분석불가 {len(r['unresolved'])}" if len(r["unresolved"]) else ""
                out.append({"name": r["name"], "type": self._kind(r),
                            "sub": f"{ings}{extra}" + ("" if r["active"] else " · 허가취소/판매중단")})
        seen, res = set(), []
        for o in out:
            if o["name"] not in seen:
                seen.add(o["name"])
                res.append(o)
        return res[:limit]

    def _from_smiles(self, smi: str) -> Ingredient:
        try:
            std = standardize(smi)
        except MoleculeError as e:
            if not any(ch in smi for ch in "()=#[]@0123456789"):
                raise ResolveError("알 수 없는 약물명입니다 (성분명/제품명 또는 SMILES 구조식으로 입력)") from e
            raise ResolveError(f"SMILES 구조 오류: {e.message}") from e
        if std.inchikey in self.inchikey_idx:  # a known drug typed as a structure
            return self._db(self.inchikey_idx[std.inchikey])
        return Ingredient(f"smiles:{std.inchikey}", f"신규 구조 {std.inchikey[:8]}", std.canonical_smiles, None)

    # ------------------------------------------------------------------ helpers
    def ko(self, ing: Ingredient) -> str:
        if ing.food:
            return FOODS[ing.bare]["ko"]
        k = KO_NAME.get(ing.key) or KO_NAME.get(ing.bare) or _ko_prefix(ing.bare)
        return f"{k}({ing.name})" if k else ing.name

    def molecule(self, ing: Ingredient) -> dict:
        if ing.biologic:  # biologic / herbal / vitamin ...: no 3D small-molecule model
            return {"key": ing.key, "name": ing.name, "display": self.ko(ing), "smiles": "", "known": True,
                    "biologic": True, "category": structureless_category(ing.key),
                    "atoms": [], "bonds": [], "props": {}}
        m = self.mol3d.get(ing.key) if ing.idx is not None else None
        if m is None:
            m = mol_3d(ing.smiles) or {"atoms": [], "bonds": [], "props": {}}
        return {"key": ing.key, "name": ing.name, "display": self.ko(ing), "smiles": ing.smiles,
                "known": ing.idx is not None, **m}
