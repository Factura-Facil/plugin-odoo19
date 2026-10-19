"""Facturas de Contabilidad como fuente de la FE (`account.move`).

Misma exigencia que el POS: lo que se emite tiene que cuadrar con lo que Odoo
calculo, porque el agente totaliza cada item como `(price - discount) * qty +
taxes` y cualquier diferencia la rechaza la DGI (2152).

Cubre ademas lo propio de la factura: la nota de credito como NTC referida al
documento original, el credito como forma de pago, y que una factura nacida de
una orden POS NO se emita dos veces.
"""
import json

from odoo import fields
from odoo.exceptions import UserError
from odoo.tests import TransactionCase, tagged

RATE_BY_CODE = {"00": 0.0, "01": 0.07, "02": 0.10, "03": 0.15}


@tagged("post_install", "-at_install")
class TestFfFactura(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.company = cls.env.company
        cls.company.write({
            "ff_inv_enabled": True,
            "ff_inv_base_url": "http://localhost:8080",
            "ff_inv_company_uuid": "uuid-de-prueba",
            "ff_inv_api_key": "k" * 128,
            "ff_inv_pos_code": "002",
            "ff_inv_branch_code": "0000",
            "ff_inv_environment": "2",
            "ff_inv_auto_emit": False,
        })
        cls.itbms7 = cls._tax(7)
        cls.itbms10 = cls._tax(10)
        cls.silla = cls._product("Silla", 25.0, cls.itbms7)
        cls.mesa = cls._product("Mesa", 9.99, cls.itbms10)
        cls.pan = cls._product("Pan", 1.25, None)  # exento
        cls.cliente = cls.env["res.partner"].create({"name": "Cliente Final"})

    @classmethod
    def _tax(cls, pct):
        return cls.env["account.tax"].create({
            "name": "ITBMS %s%% (factura)" % pct,
            "amount": pct, "amount_type": "percent", "type_tax_use": "sale",
        })

    @classmethod
    def _product(cls, name, price, tax):
        return cls.env["product.product"].create({
            "name": name, "list_price": price,
            "taxes_id": [(6, 0, tax.ids if tax else [])],
        })

    def _factura(self, lineas, partner=None, move_type="out_invoice", post=True):
        vals = []
        for producto, qty, *resto in lineas:
            precio = resto[0] if resto else producto.list_price
            vals.append((0, 0, {
                "product_id": producto.id,
                "name": producto.name,
                "quantity": qty,
                "price_unit": precio,
                "tax_ids": [(6, 0, producto.taxes_id.ids)],
            }))
        move = self.env["account.move"].create({
            "move_type": move_type,
            "partner_id": (partner or self.cliente).id,
            "invoice_date": fields.Date.today(),
            "invoice_line_ids": vals,
        })
        if post:
            move.action_post()
        return move

    def _assert_cuadra(self, move):
        """Recalcula como el agente y compara con Odoo."""
        doc = move._ff_build_payload()["document"]
        total, taxes = 0.0, 0.0
        for it in doc["items"]:
            price, disc, qty = float(it["price"]), float(it["discount"]), float(it["quantity"])
            self.assertGreaterEqual(price, 0.0, it)
            self.assertGreater(qty, 0.0, it)
            self.assertTrue(0.0 <= disc <= price + 1e-6, "descuento mayor que el precio: %s" % it)
            base = (price - disc) * qty
            tax = sum(float(t["amount"]) for t in it["taxes"])
            rate = RATE_BY_CODE[it["taxes"][0]["code"]]
            self.assertAlmostEqual(tax, base * rate, delta=0.011,
                                   msg="ITBMS no cuadra con la tasa: %s" % it)
            total += base + tax
            taxes += tax
        self.assertAlmostEqual(round(total, 2), abs(move.amount_total), delta=0.001,
                               msg=json.dumps(doc, indent=1))
        self.assertAlmostEqual(round(taxes, 2), abs(move.amount_tax), delta=0.001)
        pagos = sum(p["amount"] for p in doc["payments"])
        self.assertAlmostEqual(round(pagos, 2), round(total, 2), delta=0.011,
                               msg="los pagos informados deben sumar el total del documento")
        return doc

    # ------------------------------------------------------------------
    # Payload
    # ------------------------------------------------------------------
    def test_tasas_mixtas_y_exento(self):
        doc = self._assert_cuadra(self._factura([
            (self.silla, 2), (self.mesa, 3), (self.pan, 4)]))
        self.assertEqual([it["taxes"][0]["code"] for it in doc["items"]], ["01", "02", "00"])
        self.assertEqual(doc["type"], "FAC")
        self.assertEqual(doc["sale_type"], "1")

    def test_descuento_global_se_reparte(self):
        """Una linea negativa no es un item: se reparte como descuento entre
        los items de su misma tasa."""
        factura = self._factura([(self.silla, 2), (self.silla, 1, -5.0)])
        doc = self._assert_cuadra(factura)
        self.assertEqual(len(doc["items"]), 1, "la linea negativa no puede ser un item")
        self.assertEqual(float(doc["items"][0]["discount"]), 2.5, "2.50 por unidad")

    def test_descuento_de_linea(self):
        factura = self._factura([(self.silla, 2)], post=False)
        factura.invoice_line_ids.discount = 10.0
        factura.action_post()
        doc = self._assert_cuadra(factura)
        self.assertEqual(float(doc["items"][0]["price"]), 25.0, "el precio va sin descontar")
        self.assertEqual(float(doc["items"][0]["discount"]), 2.5)

    def test_descuento_mayor_que_la_venta_no_se_emite(self):
        """El descuento se reparte entre los items de SU tasa: si los supera,
        el documento no cuadra y no se emite. La mesa (10%) mantiene el total
        positivo, que es lo unico que Odoo exige para confirmar."""
        factura = self._factura([(self.silla, 1), (self.mesa, 10), (self.silla, 1, -50.0)])
        with self.assertRaises(UserError):
            factura._ff_build_payload()

    def test_tasa_no_dgi_no_se_emite(self):
        raro = self._tax(5)
        producto = self._product("Raro", 10.0, raro)
        with self.assertRaises(UserError):
            self._factura([(producto, 1)])._ff_build_payload()

    def test_header_y_codigo_de_punto_de_facturacion(self):
        factura = self._factura([(self.silla, 1)])
        payload = factura._ff_build_payload()
        self.assertEqual(payload["header"]["pos"], "002",
                         "el punto de facturacion de la compañia, no el de una caja")
        self.assertEqual(payload["header"]["id"], factura.name)
        self.assertIn("OdooINV", payload["document"]["info"])

    # ------------------------------------------------------------------
    # Receptor
    # ------------------------------------------------------------------
    def test_receptor_consumidor_final(self):
        doc = self._factura([(self.silla, 1)])._ff_build_payload()["document"]
        self.assertEqual(doc["receptor"]["type"], "02")
        self.assertEqual(doc["receptor"]["name"], "Cliente Final")

    def test_receptor_contribuyente(self):
        contribuyente = self.env["res.partner"].create({
            "name": "Empresa SA", "ff_receptor_type": "01",
            "ff_ruc": "155123456-2-2017", "ff_dv": "86",
            "street": "Calle 50", "ff_location_code": "8-8-7",
        })
        doc = self._factura([(self.silla, 1)], partner=contribuyente)._ff_build_payload()["document"]
        self.assertEqual(doc["receptor"]["type"], "01")
        self.assertEqual(doc["receptor"]["ruc"], "155123456-2-2017")
        self.assertEqual(doc["receptor"]["dv"], "86")

    def test_contribuyente_sin_ruc_se_degrada(self):
        """Sin RUC no se puede emitir como contribuyente: la DGI lo rechazaria."""
        sin_ruc = self.env["res.partner"].create({"name": "Empresa SA", "ff_receptor_type": "01"})
        doc = self._factura([(self.silla, 1)], partner=sin_ruc)._ff_build_payload()["document"]
        self.assertEqual(doc["receptor"]["type"], "02")

    # ------------------------------------------------------------------
    # Formas de pago
    # ------------------------------------------------------------------
    def test_factura_sin_cobrar_va_a_credito(self):
        doc = self._factura([(self.silla, 2)])._ff_build_payload()["document"]
        self.assertEqual(doc["payments"], [{"type": "01", "amount": 53.5}])

    def test_cobro_registrado_usa_la_forma_del_diario(self):
        factura = self._factura([(self.silla, 2)])
        diario = self.env["account.journal"].search(
            [("type", "=", "bank"), ("company_id", "=", self.company.id)], limit=1)
        diario.ff_payment_type = "08"  # transferencia
        self.env["account.payment.register"].with_context(
            active_model="account.move", active_ids=factura.ids
        ).create({"journal_id": diario.id})._create_payments()
        factura.invalidate_recordset()
        doc = self._assert_cuadra(factura)
        self.assertEqual([p["type"] for p in doc["payments"]], ["08"])

    def test_cobro_parcial_deja_el_resto_a_credito(self):
        factura = self._factura([(self.silla, 2)])  # 53.50
        diario = self.env["account.journal"].search(
            [("type", "=", "bank"), ("company_id", "=", self.company.id)], limit=1)
        diario.ff_payment_type = "08"
        self.env["account.payment.register"].with_context(
            active_model="account.move", active_ids=factura.ids
        ).create({"journal_id": diario.id, "amount": 20.0})._create_payments()
        factura.invalidate_recordset()
        doc = self._assert_cuadra(factura)
        self.assertEqual({p["type"] for p in doc["payments"]}, {"08", "01"})
        credito = [p["amount"] for p in doc["payments"] if p["type"] == "01"][0]
        self.assertAlmostEqual(credito, 33.5, delta=0.01)

    def test_diario_sin_forma_dgi_se_deduce(self):
        efectivo = self.env["account.journal"].search(
            [("type", "=", "cash"), ("company_id", "=", self.company.id)], limit=1)
        self.assertEqual(efectivo._ff_guess_payment_type(), "02")

    # ------------------------------------------------------------------
    # Nota de credito
    # ------------------------------------------------------------------
    def _nota_credito(self, factura):
        asistente = self.env["account.move.reversal"].with_context(
            active_model="account.move", active_ids=factura.ids
        ).create({"journal_id": factura.journal_id.id, "reason": "Devolución"})
        nc = self.env["account.move"].browse(asistente.reverse_moves()["res_id"])
        nc.action_post()
        return nc

    def _timbrada(self, move, cufe):
        move.sudo()._ff_ensure_document().sudo().write({
            "fe_estado": "autorizada", "fe_cufe": cufe})
        move.invalidate_recordset()

    def test_nota_credito_es_ntc_referida(self):
        factura = self._factura([(self.silla, 2)])
        self._timbrada(factura, "FE01" + "0" * 62)
        doc = self._assert_cuadra(self._nota_credito(factura))
        self.assertEqual(doc["type"], "NTC")
        self.assertIsNone(doc["sale_type"], "en NC el tipo de venta va nulo explicito")
        self.assertEqual(doc["referred"]["fd_number"], "FE01" + "0" * 62)

    def test_nota_credito_con_descuento_global(self):
        factura = self._factura([(self.silla, 2), (self.silla, 1, -5.0)])
        self._timbrada(factura, "FE02" + "0" * 62)
        doc = self._assert_cuadra(self._nota_credito(factura))
        self.assertEqual(len(doc["items"]), 1)

    def test_nota_credito_sin_origen_timbrado_no_se_emite(self):
        """La DGI rechaza una NC que no referencia un documento fiscal."""
        nc = self._nota_credito(self._factura([(self.silla, 1)]))
        with self.assertRaises(UserError):
            nc._ff_build_payload()

    # ------------------------------------------------------------------
    # Emision: cuando si y cuando no
    # ------------------------------------------------------------------
    def test_borrador_no_es_emitible(self):
        factura = self._factura([(self.silla, 1)], post=False)
        self.assertFalse(factura._ff_emitible())
        self.assertFalse(factura.ff_puede_firmar)
        with self.assertRaises(UserError):
            factura.action_ff_firmar()

    def test_confirmada_es_emitible(self):
        factura = self._factura([(self.silla, 1)])
        self.assertTrue(factura._ff_emitible())
        self.assertTrue(factura.ff_puede_firmar)

    def test_compra_no_se_emite(self):
        proveedor = self._factura([(self.silla, 1)], move_type="in_invoice")
        self.assertFalse(proveedor._ff_emitible())

    def test_sin_configurar_no_se_emite(self):
        self.company.ff_inv_enabled = False
        factura = self._factura([(self.silla, 1)])
        self.assertFalse(factura._ff_emitible())

    def test_auto_emit_crea_el_documento_al_confirmar(self):
        self.company.ff_inv_auto_emit = True
        factura = self._factura([(self.silla, 1)])
        self.assertTrue(factura.fe_document_id, "al confirmar debe quedar el documento FE")
        self.assertEqual(factura.fe_estado, "pendiente",
                         "sin HTTP dentro del _post: lo emite el cron")

    def test_sin_auto_emit_no_crea_documento(self):
        factura = self._factura([(self.silla, 1)])
        self.assertFalse(factura.fe_document_id)

    def test_una_factura_un_solo_documento(self):
        factura = self._factura([(self.silla, 1)])
        doc = factura._ff_ensure_document()
        self.assertEqual(factura._ff_ensure_document(), doc, "no se crea un segundo documento")

    # ------------------------------------------------------------------
    # Doble emision con el POS
    # ------------------------------------------------------------------
    def test_factura_de_orden_pos_no_se_emite_aqui(self):
        """El pedido POS ya emitio con sus pagos reales: emitirla otra vez
        crearia un segundo documento fiscal para la misma venta."""
        factura = self._factura([(self.silla, 1)])
        sesion = self.env["pos.session"].search([], limit=1)
        if not sesion:
            self.skipTest("sin sesión POS en la base")
        orden = self.env["pos.order"].create({
            "session_id": sesion.id, "company_id": self.company.id,
            "amount_tax": 0, "amount_total": 0, "amount_paid": 0, "amount_return": 0,
            "account_move": factura.id,
        })
        self.assertTrue(orden)
        factura.invalidate_recordset()
        self.assertTrue(factura.ff_desde_pos)
        self.assertFalse(factura._ff_emitible())
        with self.assertRaises(UserError):
            factura.action_ff_firmar()

    # ------------------------------------------------------------------
    # La cabecera siempre dice en qué estado está la FE
    # ------------------------------------------------------------------
    def test_fe_aplica_en_factura_de_cliente(self):
        self.assertTrue(self._factura([(self.silla, 1)]).ff_fe_aplica)

    def test_fe_no_aplica_en_compras(self):
        self.assertFalse(self._factura([(self.silla, 1)], move_type="in_invoice").ff_fe_aplica)

    def test_fe_no_aplica_sin_configurar(self):
        self.company.ff_inv_enabled = False
        self.assertFalse(self._factura([(self.silla, 1)]).ff_fe_aplica)

    def test_fe_aplica_aunque_este_en_borrador(self):
        """La cabecera debe decir algo también antes de confirmar; lo que cambia
        es la acción disponible, no si la FE le incumbe a esta factura."""
        borrador = self._factura([(self.silla, 1)], post=False)
        self.assertTrue(borrador.ff_fe_aplica)
        self.assertFalse(borrador.ff_puede_firmar, "en borrador no hay nada que emitir")

    def test_tras_emitir_ya_no_se_ofrece_emitir(self):
        """El botón 'Emitir' desaparece, pero el estado queda visible en la
        cabecera: antes no quedaba nada y parecía que no se había hecho."""
        factura = self._factura([(self.silla, 1)])
        self.assertTrue(factura.ff_puede_firmar)
        factura._ff_ensure_document()
        factura.invalidate_recordset()
        self.assertFalse(factura.ff_puede_firmar)
        self.assertTrue(factura.ff_fe_aplica, "la cabecera sigue mostrando el estado")
        self.assertEqual(factura.fe_estado, "pendiente")

    def test_el_error_queda_legible_para_reintentar(self):
        factura = self._factura([(self.silla, 1)])
        factura._ff_ensure_document().sudo().write({
            "fe_estado": "error", "fe_error": "El agente rechazó el RUC"})
        factura.invalidate_recordset()
        self.assertEqual(factura.fe_estado, "error")
        self.assertIn("RUC", factura.fe_error)
        self.assertFalse(factura.ff_puede_firmar, "con documento ya creado se reintenta, no se emite")
