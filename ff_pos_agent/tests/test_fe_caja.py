"""Modo "agente en la caja": el navegador emite contra su agente local.

Lo que se prueba aquí es el lado servidor, que es el que decide el estado
fiscal. El transporte (fetch con Local Network Access) no se puede ejercitar
desde un test de Python: eso se verifica en la caja.

El invariante que más importa: **nunca se emite dos veces**. El agente no
deduplica, así que un segundo POST sería una segunda factura ante la DGI.
"""
from unittest.mock import patch

from odoo.addons.point_of_sale.tests.common import TestPoSCommon
from odoo.exceptions import UserError
from odoo.tests import tagged


@tagged("post_install", "-at_install")
class TestFfCaja(TestPoSCommon):

    def setUp(self):
        super().setUp()
        self.config = self.basic_config
        self.config.write({
            "ff_agent_enabled": True,
            "ff_mode": "agent_local",
            "ff_base_url": "http://127.0.0.1:8080",
            "ff_company_uuid": "uuid-de-prueba",
            "ff_api_key": "k" * 128,
            "ff_pos_code": "001",
            "ff_environment": "2",
        })
        self.itbms7 = self.env["account.tax"].create({
            "name": "ITBMS 7% (caja)", "amount": 7.0,
            "amount_type": "percent", "type_tax_use": "sale",
        })
        self.silla = self.create_product("Silla", self.categ_basic, 25.0,
                                         tax_ids=self.itbms7.ids)
        self.open_new_session()

    def _orden(self):
        data = self.create_ui_order_data([(self.silla, 2)])
        oid = self.env["pos.order"].sync_from_ui([data])["pos.order"][0]["id"]
        return self.env["pos.order"].browse(oid)

    def _rfe(self, cufe="FE01" + "0" * 62):
        return {
            "dId": cufe,
            "gDGen": {"dNroDF": "0000000123", "dFechaEm": "2026-09-28T10:00:00-05:00",
                      "gEmis": {"dNombEm": "MI EMPRESA", "gRucEmi": {"dRuc": "1-2-3", "dDV": "11"}}},
            "gNoFirm": {"dQRCode": "https://dgi-fep.mef.gob.pa/Consultas/x?CUFE=" + cufe},
        }

    # ------------------------------------------------------------------
    # Preparación
    # ------------------------------------------------------------------
    def test_prepare_entrega_payload_y_credenciales(self):
        prep = self._orden().ff_local_prepare()
        self.assertIn("payload", prep)
        self.assertEqual(prep["base_url"], "http://127.0.0.1:8080")
        self.assertEqual(prep["company_uuid"], "uuid-de-prueba")
        self.assertTrue(prep["api_key"], "la caja necesita la clave para hablar con su agente")
        self.assertEqual(prep["payload"]["document"]["type"], "FAC")

    def test_prepare_deja_el_payload_en_la_auditoria_antes_de_salir(self):
        order = self._orden()
        order.ff_local_prepare()
        self.assertTrue(order.fe_request_json,
                        "si la caja muere a mitad, el payload debe constar igualmente")

    def test_prepare_no_reemite_lo_ya_emitido(self):
        """Con document_uuid NUNCA se vuelve a hacer POST: el agente no deduplica."""
        order = self._orden()
        order._ff_ensure_document().sudo().write({"fe_document_uuid": "uuid-1"})
        prep = order.ff_local_prepare()
        self.assertEqual(prep["skip"], "ya_emitido")
        self.assertNotIn("payload", prep)

    def test_prepare_no_reemite_un_envio_incierto(self):
        order = self._orden()
        order._ff_ensure_document().sudo().write({"fe_estado": "incierta"})
        prep = order.ff_local_prepare()
        self.assertEqual(prep["skip"], "incierta")
        self.assertNotIn("payload", prep)

    def test_prepare_exige_modo_de_caja(self):
        self.config.ff_mode = "agent"
        with self.assertRaises(UserError):
            self._orden().ff_local_prepare()

    # ------------------------------------------------------------------
    # Aplicación del resultado
    # ------------------------------------------------------------------
    def test_ok_timbra_con_cufe_y_qr(self):
        order = self._orden()
        prep = order.ff_local_prepare()
        order.ff_local_apply({
            "estado": "ok", "status": 201, "body": "{}",
            "document_uuid": "uuid-1", "payload": prep["payload"],
            "rfe": self._rfe(),
        })
        self.assertEqual(order.fe_estado, "timbrada")
        self.assertEqual(order.fe_cufe, "FE01" + "0" * 62)
        self.assertEqual(order.fe_nro_dgi, "0000000123")
        self.assertTrue(order.fe_qr_url)
        self.assertTrue(order.fe_doc_json, "el CAFE del recibo se arma en el servidor")

    def test_resultado_del_pac_autoriza(self):
        order = self._orden()
        prep = order.ff_local_prepare()
        order.ff_local_apply({
            "estado": "ok", "document_uuid": "uuid-1", "payload": prep["payload"],
            "rfe": self._rfe(),
            "resultado": {"authorized": True, "authNumber": "PROTO-9"},
        })
        self.assertEqual(order.fe_estado, "autorizada")
        self.assertEqual(order.fe_protocolo, "PROTO-9")

    def test_resultado_del_pac_rechaza(self):
        order = self._orden()
        prep = order.ff_local_prepare()
        order.ff_local_apply({
            "estado": "ok", "document_uuid": "uuid-1", "payload": prep["payload"],
            "rfe": self._rfe(),
            "resultado": {"received": True, "accepted": False, "dgiErrMsg": "2152 tasa"},
        })
        self.assertEqual(order.fe_estado, "rechazada")
        self.assertIn("2152", order.fe_error)

    def test_no_entregado_queda_pendiente_y_es_reenviable(self):
        """Si el POST no salió, reenviar es seguro."""
        order = self._orden()
        order.ff_local_prepare()
        order.ff_local_apply({"estado": "no_entregado", "message": "agente apagado"})
        self.assertEqual(order.fe_estado, "pendiente")
        prep = order.ff_local_prepare()
        self.assertIn("payload", prep, "un envío que no salió sí se puede reintentar")

    def test_incierta_bloquea_el_reenvio(self):
        """Si pudo llegar, NO se reenvía: duplicaría la factura ante la DGI."""
        order = self._orden()
        order.ff_local_prepare()
        order.ff_local_apply({"estado": "incierta", "message": "timeout"})
        self.assertEqual(order.fe_estado, "incierta")
        self.assertEqual(order.ff_local_prepare()["skip"], "incierta")

    def test_error_del_agente_no_deja_documento(self):
        order = self._orden()
        order.ff_local_prepare()
        order.ff_local_apply({"estado": "error", "status": 400, "message": "RUC inválido"})
        self.assertEqual(order.fe_estado, "error")
        self.assertIn("RUC", order.fe_error)

    def test_sin_document_uuid_es_incierto(self):
        order = self._orden()
        order.ff_local_prepare()
        order.ff_local_apply({"estado": "ok", "status": 200, "body": "{}"})
        self.assertEqual(order.fe_estado, "incierta")

    # ------------------------------------------------------------------
    # La caja no es de fiar: el servidor valida lo que le devuelve
    # ------------------------------------------------------------------
    def test_payload_alterado_queda_registrado(self):
        """El documento fiscal ya existe en la DGI, así que no se rechaza —
        pero tiene que quedar rastro para poder anularlo."""
        order = self._orden()
        prep = order.ff_local_prepare()
        alterado = dict(prep["payload"])
        alterado["document"] = dict(alterado["document"], items=[])
        order.ff_local_apply({
            "estado": "ok", "document_uuid": "uuid-1",
            "payload": alterado, "rfe": self._rfe(),
        })
        self.assertEqual(order.fe_estado, "timbrada")
        self.assertIn("NO coincide", order.fe_response_json or "")

    def test_payload_intacto_no_avisa(self):
        order = self._orden()
        prep = order.ff_local_prepare()
        order.ff_local_apply({
            "estado": "ok", "document_uuid": "uuid-1",
            "payload": prep["payload"], "rfe": self._rfe(),
        })
        self.assertNotIn("NO coincide", order.fe_response_json or "")

    def test_la_respuesta_del_agente_queda_en_la_auditoria(self):
        order = self._orden()
        order.ff_local_prepare()
        order.ff_local_apply({"estado": "error", "status": 400, "body": '{"detail":"x"}',
                              "message": "x"})
        self.assertIn("caja", order.fe_response_json or "")

    # ------------------------------------------------------------------
    # El cron no debe tocar lo que emite la caja
    # ------------------------------------------------------------------
    def test_el_cron_ignora_los_documentos_de_caja(self):
        """El servidor no alcanza 127.0.0.1 de la caja: si el cron lo intentara,
        marcaría como caído lo que está perfectamente bien."""
        order = self._orden()
        order.ff_local_prepare()
        self.assertEqual(order.fe_estado, "pendiente")
        # El cron hace commit por documento: sin neutralizarlo, la configuración
        # de esta prueba escapa del aislamiento y contamina otros tests.
        with patch.object(type(self.env.cr), "commit"), \
                patch.object(type(order.fe_document_id), "_ff_sign") as firmar:
            self.env["ff.fe.document"]._cron_ff_update_status()
            firmar.assert_not_called()

    # ------------------------------------------------------------------
    # Las credenciales solo bajan al navegador en este modo
    # ------------------------------------------------------------------
    def _cargar_pos(self):
        """En Odoo 18 la carga del POS es _load_pos_data(data)."""
        datos = {"pos.session": {"data": [{"config_id": self.config.id}]}}
        return self.config._load_pos_data(datos)["data"][0]

    def test_en_modo_caja_las_credenciales_llegan_al_pos(self):
        cargado = self._cargar_pos()
        self.assertEqual(cargado.get("ff_api_key"), "k" * 128)
        self.assertEqual(cargado.get("ff_company_uuid"), "uuid-de-prueba")

    def test_en_los_demas_modos_no_llegan(self):
        self.config.ff_mode = "agent"
        cargado = self._cargar_pos()
        self.assertNotIn("ff_api_key", cargado)
        self.assertNotIn("ff_company_uuid", cargado)

    # ------------------------------------------------------------------
    # Validación de RUC: el servidor no alcanza al agente de la caja
    # ------------------------------------------------------------------
    def test_el_servidor_no_usa_el_agente_de_la_caja_para_el_ruc(self):
        """127.0.0.1 desde el servidor es el propio servidor, no la caja: un POS
        en modo local nunca debe elegirse para una consulta server-side."""
        self.env.company.ff_inv_enabled = False
        otras = self.env["pos.config"].search([("id", "!=", self.config.id)])
        otras.write({"ff_agent_enabled": False})
        with self.assertRaises(UserError):
            self.env["res.partner"]._ff_ruc_params()

    def test_se_prefiere_un_pos_alcanzable_desde_el_servidor(self):
        self.env.company.ff_inv_enabled = False
        alcanzable = self.env["pos.config"].create({
            "name": "Caja con agente en red",
            "ff_agent_enabled": True, "ff_mode": "agent",
            "ff_base_url": "http://agente-red.test", "ff_api_key": "K" * 20,
        })
        params = self.env["res.partner"]._ff_ruc_params()
        self.assertEqual(params["base_url"], "http://agente-red.test")
        self.assertIn(alcanzable.name, params["origen"])

    def test_el_backend_valida_con_la_conexion_de_la_compania(self):
        """Facturando desde Contabilidad con la API en línea, el RUC se valida
        por esa misma conexión — no buscando un agente de caja que no existe."""
        self.env.company.write({
            "ff_inv_enabled": True, "ff_inv_mode": "online",
            "ff_inv_base_url": "https://api.facturafacil.test",
            "ff_inv_api_key": "K" * 20, "ff_inv_company_uuid": "uuid-cia",
            "ff_inv_environment": "1",
        })
        params = self.env["res.partner"]._ff_ruc_params()
        self.assertEqual(params["mode"], "online")
        self.assertEqual(params["base_url"], "https://api.facturafacil.test")
        self.assertEqual(params["env_name"], "prod")
        self.assertIn("compañía", params["origen"])

    def test_el_pos_que_pregunta_manda_sobre_la_compania(self):
        """Si la consulta nace de una caja concreta, se usa SU agente."""
        self.env.company.write({
            "ff_inv_enabled": True, "ff_inv_mode": "online",
            "ff_inv_base_url": "https://api.facturafacil.test",
        })
        caja = self.env["pos.config"].create({
            "name": "Caja con agente propio",
            "ff_agent_enabled": True, "ff_mode": "agent",
            "ff_base_url": "http://agente-de-esa-caja.test", "ff_api_key": "K" * 20,
        })
        params = self.env["res.partner"]._ff_ruc_params(config_id=caja.id)
        self.assertEqual(params["base_url"], "http://agente-de-esa-caja.test")

    def test_una_caja_local_no_se_usa_ni_aunque_la_pidan(self):
        """Pedir explícitamente una caja en modo local no debe hacer que el
        servidor intente llamarse a sí mismo: se cae a la compañía."""
        self.env.company.write({
            "ff_inv_enabled": True, "ff_inv_mode": "online",
            "ff_inv_base_url": "https://api.facturafacil.test",
        })
        params = self.env["res.partner"]._ff_ruc_params(config_id=self.config.id)
        self.assertEqual(params["base_url"], "https://api.facturafacil.test")

    def test_la_caja_manda_la_respuesta_cruda_y_el_servidor_la_interpreta(self):
        """En modo local consulta el navegador, pero el parseo sigue en Python."""
        res = self.env["res.partner"].ff_ruc_from_agent("155723374-2-2022", {
            "valido": True, "ruc": "155723374-2-2022", "dv": "91",
            "tipoRuc": 2, "tipoReceptor": "01", "iAfilFE": 1,
            "razonSocial": "FACTURA FACIL S.A",
        })
        self.assertTrue(res["valido"])
        self.assertEqual(res["dv"], "91")
        self.assertEqual(res["ruc_type"], "2")
        self.assertEqual(res["receptor_type"], "01")
        self.assertIn("FACTURA FACIL", res["message"])

    def test_ruc_inexistente_desde_la_caja(self):
        res = self.env["res.partner"].ff_ruc_from_agent("155123456-2-2017", {
            "valido": False, "ruc": "155123456-2-2017", "dv": None,
        })
        self.assertFalse(res["valido"])

    # ------------------------------------------------------------------
    # Impresión del CAFE: segunda llamada, independiente de la firma
    # ------------------------------------------------------------------
    def test_prepare_dice_si_hay_que_imprimir(self):
        self.config.ff_print_cafe = True
        self.assertTrue(self._orden().ff_local_prepare()["print_cafe"])
        self.config.ff_print_cafe = False
        self.assertFalse(self._orden().ff_local_prepare()["print_cafe"])

    def test_impresion_ok_marca_el_documento(self):
        order = self._orden()
        prep = order.ff_local_prepare()
        order.ff_local_apply({
            "estado": "ok", "document_uuid": "uuid-1", "payload": prep["payload"],
            "rfe": self._rfe(), "impresion": {"impreso": True, "status": 200},
        })
        self.assertTrue(order.fe_impreso)

    def test_si_no_imprime_el_documento_sigue_siendo_valido(self):
        """La impresora es un periférico: su fallo no invalida la factura."""
        order = self._orden()
        prep = order.ff_local_prepare()
        order.ff_local_apply({
            "estado": "ok", "document_uuid": "uuid-1", "payload": prep["payload"],
            "rfe": self._rfe(),
            "impresion": {"impreso": False, "status": 500, "body": "printer offline"},
        })
        self.assertEqual(order.fe_estado, "timbrada", "el documento fiscal no se toca")
        self.assertFalse(order.fe_impreso)
        self.assertIn("printByCufe", order.fe_response_json or "")

    def test_sin_impresion_no_se_marca_nada(self):
        order = self._orden()
        prep = order.ff_local_prepare()
        order.ff_local_apply({
            "estado": "ok", "document_uuid": "uuid-1", "payload": prep["payload"],
            "rfe": self._rfe(),
        })
        self.assertFalse(order.fe_impreso)

    def test_el_servidor_no_intenta_imprimir_en_modo_caja(self):
        """La impresora cuelga de la caja: si el servidor lo intentara, llamaría
        a su propio 127.0.0.1."""
        self.config.ff_print_cafe = True
        order = self._orden()
        doc = order._ff_ensure_document().sudo()
        doc.write({"fe_estado": "autorizada", "fe_cufe": "FE01" + "0" * 62})
        with patch.object(type(doc), "_ff_print_cafe") as imprimir:
            doc._ff_maybe_print()
            imprimir.assert_not_called()

    # ------------------------------------------------------------------
    # El cajero prepara la emisión, y no es responsable del POS
    # ------------------------------------------------------------------
    def test_el_cajero_puede_preparar_la_emision(self):
        """Las credenciales del agente llevan groups=responsable del POS, pero
        es el CAJERO quien tiene que mandárselas a su agente local. Sin sudo,
        esto reventaba con AccessError y llegaba a la caja como un 500 opaco."""
        cajero = self.env["res.users"].create({
            "name": "Cajero caja local", "login": "cajero_caja_local",
            "company_id": self.env.company.id,
            "company_ids": [(6, 0, self.env.company.ids)],
            "group_ids": [(6, 0, self.env.ref("point_of_sale.group_pos_user").ids)],
        })
        order = self._orden()
        prep = order.with_user(cajero).ff_local_prepare()
        self.assertIn("payload", prep)
        self.assertEqual(prep["api_key"], "k" * 128,
                         "el cajero necesita la clave para hablar con SU agente")
        self.assertEqual(prep["company_uuid"], "uuid-de-prueba")

    def test_un_fallo_inesperado_no_llega_como_500(self):
        """El cajero tiene un cliente delante: un error tiene que explicarse,
        no aparecer como 'Odoo Server Error'."""
        order = self._orden()
        with patch.object(type(order), "_ff_build_payload",
                          side_effect=ValueError("algo raro")):
            prep = order.ff_local_prepare()
        self.assertEqual(prep["skip"], "error")
        self.assertIn("algo raro", prep["message"])
        self.assertEqual(order.fe_estado, "error")

    # ------------------------------------------------------------------
    # Lo que la caja llama por RPC tiene que ser público
    # ------------------------------------------------------------------
    def test_los_metodos_que_llama_la_caja_son_publicos(self):
        """Odoo bloquea las llamadas remotas a métodos con guion bajo: si uno de
        estos se vuelve privado, la caja deja de emitir sin avisar."""
        for nombre in ("ff_local_prepare", "ff_local_apply", "ff_fe_data",
                       "ff_local_print_done", "ff_emitir_data"):
            self.assertFalse(nombre.startswith("_"), nombre)
            self.assertTrue(hasattr(self.env["pos.order"], nombre),
                            "falta %s en pos.order" % nombre)

    def test_ff_fe_data_incluye_lo_que_necesita_el_recibo(self):
        order = self._orden()
        prep = order.ff_local_prepare()
        order.ff_local_apply({
            "estado": "ok", "document_uuid": "uuid-1", "payload": prep["payload"],
            "rfe": self._rfe(), "impresion": {"impreso": True, "status": 200},
        })
        datos = order.ff_fe_data()
        self.assertEqual(datos["fe_estado"], "timbrada")
        self.assertTrue(datos["fe_impreso"],
                        "el recibo decide con esto si saltarse el PDF de Odoo")

    def test_reimpresion_desde_el_recibo(self):
        order = self._orden()
        prep = order.ff_local_prepare()
        order.ff_local_apply({
            "estado": "ok", "document_uuid": "uuid-1", "payload": prep["payload"],
            "rfe": self._rfe(),
        })
        self.assertFalse(order.fe_impreso)
        order.ff_local_print_done({"impreso": True, "status": 200})
        self.assertTrue(order.fe_impreso)

    # ------------------------------------------------------------------
    # La factura del POS enseña el estado FE de su orden
    # ------------------------------------------------------------------
    def _factura_vacia(self):
        return self.env["account.move"].create({
            "move_type": "out_invoice",
            "partner_id": self.env["res.partner"].create({"name": "Cliente POS"}).id,
        })

    def test_la_factura_de_la_orden_comparte_el_documento_fe(self):
        """Con facturación automática del POS, en Contabilidad se veían todas
        las facturas sin estado: el documento cuelga de la orden, no de ellas."""
        order = self._orden()
        factura = self._factura_vacia()
        order.sudo().account_move = factura.id
        doc = order._ff_ensure_document()
        factura.invalidate_recordset()
        self.assertEqual(factura.fe_document_id, doc)

    def test_el_estado_se_refleja_en_la_factura(self):
        order = self._orden()
        factura = self._factura_vacia()
        order.sudo().account_move = factura.id
        prep = order.ff_local_prepare()
        order.ff_local_apply({
            "estado": "ok", "document_uuid": "uuid-1", "payload": prep["payload"],
            "rfe": self._rfe(), "resultado": {"authorized": True, "authNumber": "P-1"},
        })
        factura.invalidate_recordset()
        self.assertEqual(factura.fe_estado, "autorizada")
        self.assertEqual(factura.fe_cufe, order.fe_cufe)

    def test_se_comparte_el_registro_no_se_copia(self):
        """Un cambio de estado posterior debe verse en los dos sitios."""
        order = self._orden()
        factura = self._factura_vacia()
        order.sudo().account_move = factura.id
        doc = order._ff_ensure_document()
        doc.sudo().write({"fe_estado": "rechazada", "fe_error": "2152"})
        factura.invalidate_recordset()
        order.invalidate_recordset()
        self.assertEqual(factura.fe_estado, "rechazada")
        self.assertEqual(order.fe_estado, "rechazada")

    def test_la_factura_del_pos_no_se_emite_por_su_cuenta(self):
        """Comparte el estado, pero sigue sin poder emitir: eso crearía un
        segundo documento fiscal para la misma venta."""
        order = self._orden()
        factura = self._factura_vacia()
        order.sudo().account_move = factura.id
        order._ff_ensure_document()
        factura.invalidate_recordset()
        self.assertTrue(factura.ff_desde_pos)
        self.assertFalse(factura._ff_emitible())
        self.assertFalse(factura.ff_puede_firmar)
