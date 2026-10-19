"""Clasificacion estructural del RUC (LOGICA_RUC.md 3) y su efecto en la ficha."""
from odoo.addons.ff_pos_agent.models.res_partner import ff_classify_ruc
from odoo.tests import TransactionCase, tagged


@tagged("post_install", "-at_install")
class TestFfRuc(TransactionCase):

    def test_clasificacion(self):
        casos = {
            "8-123-456": "natural",
            "4-728-1902": "natural",
            "13-1-2": "natural",
            "PE-12-345": "natural",
            "e-8-99870": "natural",       # insensible a mayusculas
            "2AV-45-9": "natural",
            "155646463-2-2017": "juridica",
            "423-125-14785": "juridica",
            "8-NT-1-26": "gobierno",
            "8-nt-1-26": "gobierno",      # NT gana sobre el prefijo numerico
            "": "desconocido",
            "ABC-1-2": "desconocido",
        }
        for ruc, esperado in casos.items():
            self.assertEqual(ff_classify_ruc(ruc), esperado, ruc)
        self.assertEqual(ff_classify_ruc(None), "desconocido")

    def _onchange(self, partner, ruc):
        partner.ff_ruc = ruc
        partner._onchange_ff_ruc()
        return partner

    def test_onchange_deduce_tipos(self):
        p = self.env["res.partner"].new({"name": "X"})
        self.assertEqual(p.ff_receptor_type, "02", "por defecto: consumidor final")

        self._onchange(p, "155646463-2-2017")
        self.assertEqual((p.ff_receptor_type, p.ff_ruc_type), ("01", "2"))

        self._onchange(p, "8-123-456")
        self.assertEqual((p.ff_receptor_type, p.ff_ruc_type), ("01", "1"))

        self._onchange(p, "8-NT-1-26")
        self.assertEqual(p.ff_receptor_type, "03")

        # Formato no reconocido: hay RUC, asi que deja de ser gobierno y pasa a
        # contribuyente; el tipo de RUC se deja como estaba (lo dira la DGI).
        self._onchange(p, "ABC-1-2")
        self.assertEqual(p.ff_receptor_type, "01")

        # Sin RUC se vuelve a consumidor final.
        self._onchange(p, "")
        self.assertEqual(p.ff_receptor_type, "02")

    def test_onchange_respeta_extranjero_y_limpia_validacion(self):
        p = self.env["res.partner"].new({
            "name": "X", "ff_receptor_type": "04", "ff_ruc": "155646463-2-2017",
            "ff_dv": "86", "ff_razon_social": "VIEJA SA", "ff_ruc_validated": True,
        })
        self._onchange(p, "E-8-99870")
        self.assertEqual(p.ff_receptor_type, "04", "no se pisa un extranjero")
        self.assertEqual(p.ff_ruc_type, "1")
        self.assertFalse(p.ff_ruc_validated)
        self.assertFalse(p.ff_dv)
        self.assertFalse(p.ff_razon_social)

    def test_vals_from_check_sobreescribe_el_nombre(self):
        """La razon social de la DGI manda sobre lo que escribio el cajero."""
        res = {
            "valido": True, "dv": "86", "razon_social": "HYPERNOVA LABS, S.A",
            "ruc_type": "2", "receptor_type": "01",
        }
        vals = self.env["res.partner"]._ff_vals_from_check(res)
        self.assertEqual(vals["name"], "HYPERNOVA LABS, S.A")
        self.assertEqual(vals["ff_razon_social"], "HYPERNOVA LABS, S.A")
        self.assertEqual(vals["ff_dv"], "86")
        self.assertEqual(vals["ff_receptor_type"], "01")
        self.assertTrue(vals["ff_ruc_validated"])

        # Sin razon social no se toca el nombre del cliente.
        vals = self.env["res.partner"]._ff_vals_from_check({"valido": True, "dv": "86"})
        self.assertNotIn("name", vals)

    def test_check_ruc_sin_ruc_no_llama_al_agente(self):
        res = self.env["res.partner"].ff_check_ruc("  ")
        self.assertFalse(res["valido"])
        self.assertIn("RUC", res["message"])

    def test_ruc_validated_escribible(self):
        """Debe ser escribible por RPC: readonly a nivel de modelo descartaria
        el valor que pone el widget al guardar la ficha (ver memoria)."""
        self.assertFalse(self.env["res.partner"]._fields["ff_ruc_validated"].readonly)

    def test_hint(self):
        p = self.env["res.partner"].new({"name": "X"})
        self.assertFalse(p.ff_ruc_hint)
        p.ff_ruc = "8-NT-1-26"
        self.assertIn("Gobierno", p.ff_ruc_hint)
