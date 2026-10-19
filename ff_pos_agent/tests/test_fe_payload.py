"""Payload fiscal que se manda al agente, contra lo que el agente calcula.

El agente totaliza cada item como `(price - discount) * quantity + taxes`
(INTEGRACION_ODOO_fe_detailed.md 2.4), con `price` SIN impuestos y `discount`
por unidad. Estos tests reconstruyen ese calculo y exigen que cuadre con los
totales de Odoo: si no cuadra, la DGI rechaza (2152) o el agente rechaza por
"vuelto negativo".
"""
import json
from datetime import timedelta

from odoo import fields
from odoo.addons.point_of_sale.tests.common import TestPoSCommon
from odoo.exceptions import UserError
from odoo.tests import tagged

RATE_BY_CODE = {"00": 0.0, "01": 0.07, "02": 0.10, "03": 0.15}


@tagged("post_install", "-at_install")
class TestFfPayload(TestPoSCommon):

    def setUp(self):
        super().setUp()
        self.config = self.basic_config
        self.config.write({"ff_agent_enabled": False})  # solo se arma el payload
        self.itbms7 = self._tax(7)
        self.itbms10 = self._tax(10)
        self.itbms15 = self._tax(15)
        self.itbms7_incl = self._tax(7, incl=True)
        self.silla = self.create_product("Silla", self.categ_basic, 25.0, tax_ids=self.itbms7.ids)
        self.mesa = self.create_product("Mesa", self.categ_basic, 9.99, tax_ids=self.itbms10.ids)
        self.licor = self.create_product("Licor", self.categ_basic, 12.35, tax_ids=self.itbms15.ids)
        self.pan = self.create_product("Pan", self.categ_basic, 1.25)  # exento
        self.combo = self.create_product("Combo", self.categ_basic, 10.0, tax_ids=self.itbms7_incl.ids)
        self.open_new_session()

    def _tax(self, pct, incl=False):
        return self.env["account.tax"].create({
            "name": "ITBMS %s%%%s" % (pct, " incluido" if incl else ""),
            "amount": pct,
            "amount_type": "percent",
            "type_tax_use": "sale",
            "price_include_override": "tax_included" if incl else "tax_excluded",
        })

    def _order(self, lines, **kw):
        data = self.create_ui_order_data(lines, **kw)
        order_id = self.env["pos.order"].sync_from_ui([data])["pos.order"][0]["id"]
        return self.env["pos.order"].browse(order_id)

    def _timbrada(self, order, cufe):
        """Documento FE ya autorizado para `order` (origen de una devolucion)."""
        order.sudo().fe_document_id = self.env["ff.fe.document"].sudo().create({
            "name": order._ff_header_id(), "pos_order_id": order.id,
            "fe_estado": "autorizada", "fe_cufe": cufe,
        })

    def _assert_cuadra(self, order):
        """Recalcula como el agente y compara con Odoo."""
        payload = order._ff_build_payload()
        doc = payload["document"]
        total, taxes = 0.0, 0.0
        for it in doc["items"]:
            price, disc, qty = float(it["price"]), float(it["discount"]), float(it["quantity"])
            self.assertGreaterEqual(price, 0.0, it)
            self.assertGreater(qty, 0.0, it)
            self.assertTrue(0.0 <= disc <= price + 1e-6, "descuento mayor que el precio: %s" % it)
            base = (price - disc) * qty
            tax = sum(float(t["amount"]) for t in it["taxes"])
            rate = RATE_BY_CODE[it["taxes"][0]["code"]]
            self.assertAlmostEqual(tax, base * rate, delta=0.011, msg="ITBMS no cuadra con la tasa: %s" % it)
            total += base + tax
            taxes += tax
        self.assertAlmostEqual(round(total, 2), abs(order.amount_total), delta=0.001, msg=json.dumps(doc, indent=1))
        self.assertAlmostEqual(round(taxes, 2), abs(order.amount_tax), delta=0.001)
        pagos = sum(p["amount"] for p in doc["payments"])
        self.assertGreaterEqual(round(pagos, 2), round(total, 2), "pagos < total: vuelto negativo")
        return doc

    # ------------------------------------------------------------------

    def test_tasas_mixtas_y_exento(self):
        order = self._order([(self.silla, 2), (self.mesa, 3), (self.licor, 1), (self.pan, 4)])
        doc = self._assert_cuadra(order)
        self.assertEqual([it["taxes"][0]["code"] for it in doc["items"]], ["01", "02", "03", "00"])
        self.assertEqual(doc["items"][0]["price"], "25.000000")

    def test_descuento_de_linea(self):
        order = self._order([(self.silla, 3, 10.0), (self.mesa, 3, 15.0)])
        doc = self._assert_cuadra(order)
        self.assertEqual(doc["items"][0]["discount"], "2.500000")

    def test_descuento_100(self):
        order = self._order([(self.silla, 1, 100.0), (self.mesa, 1)])
        doc = self._assert_cuadra(order)
        self.assertEqual(doc["items"][0]["price"], "25.000000")
        self.assertEqual(doc["items"][0]["taxes"][0]["code"], "00")

    def test_precio_con_itbms_incluido(self):
        """10.00 con 7% incluido: el agente debe recibir el precio SIN ITBMS."""
        order = self._order([(self.combo, 3)])
        doc = self._assert_cuadra(order)
        # Sale de la base que Odoo redondeo (28.04 / 3), no de 10 / 1.07 exacto:
        # asi el total emitido es exactamente el cobrado.
        price = float(doc["items"][0]["price"])
        self.assertAlmostEqual(price, 10.0 / 1.07, delta=0.005 / 3 + 1e-6)
        self.assertAlmostEqual(price * 3, order.lines.price_subtotal, places=5)

    def test_precio_incluido_con_descuento(self):
        self._assert_cuadra(self._order([(self.combo, 7, 12.5), (self.silla, 1)]))

    def test_descuento_global(self):
        """Linea de descuento global (precio negativo, como pos_discount o un
        premio de lealtad): no puede viajar como un item cobrado."""
        desc = self.create_product("Descuento 10%", self.categ_basic, -5.0, tax_ids=self.itbms7.ids)
        order = self._order([(self.silla, 2), (desc, 1)])
        self.assertAlmostEqual(order.amount_total, 48.15)
        doc = self._assert_cuadra(order)
        self.assertEqual(len(doc["items"]), 1, "el descuento se reparte en los items, no es un item")

    def test_descuento_global_con_tasas_mixtas(self):
        desc7 = self.create_product("Desc 7", self.categ_basic, -4.0, tax_ids=self.itbms7.ids)
        desc10 = self.create_product("Desc 10", self.categ_basic, -1.0, tax_ids=self.itbms10.ids)
        order = self._order([(self.silla, 1), (self.combo, 2), (self.mesa, 3), (desc7, 1), (desc10, 1)])
        self._assert_cuadra(order)

    def test_devolucion_en_absoluto(self):
        origen = self._order([(self.silla, 2), (self.mesa, 1, 10.0)])
        self._timbrada(origen, "FE01ORIGEN")
        refund = self.env["pos.order"].browse(origen.refund()["res_id"])
        doc = self._assert_cuadra(refund)
        self.assertEqual(doc["type"], "NTC")
        self.assertEqual(doc["referred"]["fd_number"], "FE01ORIGEN")

    def test_devolucion_con_descuento_global(self):
        desc = self.create_product("Descuento", self.categ_basic, -5.0, tax_ids=self.itbms7.ids)
        origen = self._order([(self.silla, 2), (desc, 1)])
        self._timbrada(origen, "FE01ORIGEN")
        refund = self.env["pos.order"].browse(origen.refund()["res_id"])
        doc = self._assert_cuadra(refund)
        self.assertEqual(len(doc["items"]), 1)

    def test_vuelto_no_es_pago(self):
        """El POS registra el vuelto como un pago en efectivo negativo con
        is_change: no es un pago para la DGI (el agente calcula el vuelto)."""
        data = self.create_ui_order_data([(self.silla, 1)], payments=[(self.cash_pm1, 30.0)])
        data["amount_return"] = 3.25  # Odoo crea la linea de vuelto (is_change)
        order = self.env["pos.order"].browse(self.env["pos.order"].sync_from_ui([data])["pos.order"][0]["id"])
        self.assertEqual(order.state, "paid")
        self.assertTrue(order.payment_ids.filtered("is_change"))
        doc = self._assert_cuadra(order)
        self.assertEqual([p["amount"] for p in doc["payments"]], [30.0])
        self.assertEqual(order._ff_cafe_payload({"gDGen": {}})["vuelto"], "B/.3.25")

    def test_pago_mixto(self):
        order = self._order([(self.silla, 2)], payments=[(self.cash_pm1, 20.0), (self.bank_pm1, 33.5)])
        doc = self._assert_cuadra(order)
        self.assertEqual(len(doc["payments"]), 2)

    def test_cafe_coincide_con_el_documento_fiscal(self):
        """El recibo (fe_doc_json) debe mostrar los mismos montos que se emiten."""
        desc = self.create_product("Descuento", self.categ_basic, -5.0, tax_ids=self.itbms7.ids)
        order = self._order([(self.silla, 2), (self.combo, 1, 10.0), (desc, 1)])
        self._assert_cuadra(order)
        cafe = order._ff_cafe_payload({"gDGen": {}})
        self.assertEqual(cafe["tot"]["total"], "B/.%0.2f" % order.amount_total)
        self.assertEqual(len(cafe["items"]), len(order._ff_build_payload()["document"]["items"]))
        itbms = sum(float(t["val"][3:]) for t in cafe["tot"]["itbms"])
        self.assertAlmostEqual(itbms, order.amount_tax, delta=0.001)
        sub = float(cafe["tot"]["subtotal"][3:]) - float(cafe["tot"]["dscto"][4:])
        self.assertAlmostEqual(sub + itbms, order.amount_total, delta=0.011)

    def test_descuento_mayor_que_las_ventas_queda_en_error(self):
        """No se puede expresar como documento valido: error, sin reintentos."""
        desc = self.create_product("Descuento", self.categ_basic, -30.0, tax_ids=self.itbms7.ids)
        order = self._order([(self.silla, 1), (self.mesa, 1), (desc, 1)])
        with self.assertRaisesRegex(UserError, "Descuento"):
            order._ff_build_payload()

    def test_receptor_contribuyente(self):
        cliente = self.env["res.partner"].create({
            "name": "Hypernova", "ff_receptor_type": "01", "ff_ruc": "155646463-2-2017",
            "ff_ruc_type": "2", "ff_dv": "86", "ff_razon_social": "HYPERNOVA LABS, S.A",
            "ff_location_code": "8-8-7", "street": "Calle 50",
        })
        order = self._order([(self.silla, 1)], customer=cliente)
        rec = self._assert_cuadra(order)["receptor"]
        self.assertEqual((rec["type"], rec["ruc"], rec["dv"], rec["name"]),
                         ("01", "155646463-2-2017", "86", "HYPERNOVA LABS, S.A"))
        self.assertNotIn("gns", order._ff_build_payload()["document"]["items"][0])

    def test_gobierno_lleva_gns_en_todos_los_items(self):
        gobierno = self.env["res.partner"].create({
            "name": "Ministerio", "ff_receptor_type": "03", "ff_ruc": "8-NT-1-26",
            "ff_dv": "00", "ff_location_code": "8-8-7",
        })
        self.config.ff_codigo_gns = "8111"
        self.silla.product_tmpl_id.ff_gns_code = "4410"
        order = self._order([(self.silla, 1), (self.mesa, 1)], customer=gobierno)
        items = self._assert_cuadra(order)["items"]
        self.assertEqual([it["gns"] for it in items], ["4410", "8111"])

    # ------------------------------------------------------------------
    # Formas de pago, credito, unidad de medida, tasas
    # ------------------------------------------------------------------
    def _metodo(self, name, journal=None, **kw):
        return self.env["pos.payment.method"].create({
            "name": name, "journal_id": (journal or self.bank_pm1.journal_id).id, **kw})

    def test_forma_de_pago_propuesta(self):
        casos = {"Tarjeta Visa": "03", "Clave": "04", "Yappy": "08", "Cheque": "09",
                 "Gift card": "07", "Terminal X": "99"}
        for name, esperado in casos.items():
            self.assertEqual(self._metodo(name).ff_payment_type, esperado, name)
        self.assertEqual(self.cash_pm1.ff_payment_type, "02")
        self.assertEqual(self.pay_later_pm.ff_payment_type, "01")

    def test_la_forma_de_pago_elegida_no_se_pisa(self):
        metodo = self._metodo("Terminal X")
        metodo.ff_payment_type = "10"
        metodo.name = "Tarjeta de credito"
        self.assertEqual(metodo.ff_payment_type, "10")

    def test_otro_lleva_descripcion(self):
        metodo = self.bank_pm1
        # Con la sesion abierta: la forma de pago DGI se puede corregir igual.
        metodo.write({"ff_payment_type": "99"})
        order = self._order([(self.silla, 1)], payments=[(metodo, 26.75)])
        pago = self._assert_cuadra(order)["payments"][0]
        self.assertEqual(pago["type"], "99")
        self.assertTrue(10 <= len(pago["description"]) <= 100, pago)
        self.assertIn(metodo.name, pago["description"])

    def test_credito_sin_cuotas_por_defecto(self):
        """El agente actual falla con cuotas: por defecto el credito va como
        forma de pago 01 al contado (autorizado por la DGI en pruebas)."""
        cliente = self.env["res.partner"].create({"name": "Cliente a credito"})
        order = self._order([(self.silla, 2)], customer=cliente,
                            payments=[(self.pay_later_pm, 53.5)])
        doc = self._assert_cuadra(order)
        self.assertEqual((doc["payment_term"], doc["payments"][0]["type"]), (1, "01"))
        self.assertNotIn("installments", doc)

    def test_venta_a_credito(self):
        self.config.ff_credit_installments = True
        cliente = self.env["res.partner"].create({"name": "Cliente a credito"})
        order = self._order([(self.silla, 2)], customer=cliente,
                            payments=[(self.pay_later_pm, 53.5)])
        doc = self._assert_cuadra(order)
        self.assertEqual(doc["payment_term"], 2)
        fecha = fields.Datetime.context_timestamp(order, order.date_order).date()
        self.assertEqual(doc["installments"], [{
            "expiration_date": (fecha + timedelta(days=30)).isoformat(), "value": "53.50"}])

    def test_credito_con_plazo_del_cliente_y_mixto(self):
        self.config.ff_credit_installments = True
        plazo = self.env["account.payment.term"].create({
            "name": "15 dias", "line_ids": [(0, 0, {"value": "percent", "value_amount": 100, "nb_days": 15})]})
        cliente = self.env["res.partner"].create({"name": "Cliente 15", "property_payment_term_id": plazo.id})
        order = self._order([(self.silla, 2)], customer=cliente,
                            payments=[(self.cash_pm1, 20.0), (self.pay_later_pm, 33.5)])
        doc = self._assert_cuadra(order)
        self.assertEqual(doc["payment_term"], 3)
        fecha = fields.Datetime.context_timestamp(order, order.date_order).date()
        self.assertEqual(doc["installments"][0]["expiration_date"], (fecha + timedelta(days=15)).isoformat())
        self.assertEqual(doc["installments"][0]["value"], "33.50")

    def test_contado_sin_cuotas(self):
        doc = self._assert_cuadra(self._order([(self.silla, 1)]))
        self.assertEqual(doc["payment_term"], 1)
        self.assertNotIn("installments", doc)

    def test_unidad_de_medida_dgi(self):
        kg = self.env.ref("uom.product_uom_kgm")
        queso = self.create_product("Queso", self.categ_basic, 8.0, tax_ids=self.itbms7.ids)
        queso.write({"uom_id": kg.id, "uom_po_id": kg.id})
        items = self._assert_cuadra(self._order([(queso, 1.25), (self.silla, 1)]))["items"]
        self.assertEqual([it["mu"] for it in items], ["und", "und"], "sin codigo: und")
        kg.ff_dgi_code = "kg"
        items = self._assert_cuadra(self._order([(queso, 1.25)]))["items"]
        self.assertEqual(items[0]["mu"], "kg")

    def test_tasa_que_no_es_itbms(self):
        raro = self.create_product("Raro", self.categ_basic, 10.0, tax_ids=self._tax(8).ids)
        with self.assertRaisesRegex(UserError, "8%"):
            self._order([(raro, 1)])._ff_build_payload()
