"""Idempotencia del envio al agente: una orden nunca se emite dos veces.

El agente real NO deduplica (mismo payload, incluso con el mismo fd_number,
crea otro documento con otro CUFE). Estos tests fijan cuando se puede volver a
hacer POST y cuando solo se consulta el documento ya creado.
"""
import json
from unittest.mock import patch

import requests
from urllib3.exceptions import MaxRetryError, NewConnectionError

from odoo.addons.ff_pos_agent.models import ff_fe_document as mod
from odoo.addons.point_of_sale.tests.common import TestPoSCommon
from odoo import fields
from odoo.exceptions import UserError
from odoo.tests import tagged
from odoo.tools import mute_logger

RFE = {
    "dId": "FE01TESTCUFE",
    "gDGen": {"dNroDF": "0000000001", "dFechaEm": "2026-09-23T10:00:00-05:00", "gEmis": {}},
    "gNoFirm": {"dQRCode": "https://qr.test"},
}


def _resp(status, data=None, text=""):
    r = requests.Response()
    r.status_code = status
    r._content = (json.dumps(data) if data is not None else text).encode()
    return r


def _rechazada_conexion():
    reason = NewConnectionError(None, "Connection refused")
    return requests.ConnectionError(MaxRetryError(None, "/api", reason))


class FakeAgent:
    """Agente simulado: `post` es una lista de respuestas/excepciones por
    llamada; `rfe_ready` indica si GET /api/document/ ya trae la firma."""

    def __init__(self, post=None, rfe_ready=True, alive=True, on_post=None, print_result=None):
        self.post_results = list(post or [])
        self.on_post = on_post  # callback al momento del POST (ver estado previo)
        self.rfe_ready = rfe_ready
        self.alive = alive
        self.print_result = print_result  # respuesta/excepcion de printByCufe
        self.posts = 0
        self.doc_gets = []
        self.headers = None  # headers del ultimo POST de emision

    def post(self, url, **kw):
        if "/printByCufe/" in url:
            res = self.print_result or _resp(200, text="")
            if isinstance(res, Exception):
                raise res
            return res
        self.posts += 1
        self.headers = kw.get("headers")
        if self.on_post:
            self.on_post()
        res = self.post_results.pop(0) if self.post_results else _resp(201, {"document_uuid": "uuid-%s" % self.posts})
        if isinstance(res, Exception):
            raise res
        return res

    def get(self, url, **kw):
        if "/api/healthcheck/" in url:
            if not self.alive:
                raise requests.ReadTimeout("proxy acepta la conexion y se cuelga")
            return _resp(200, {"status": "ok"})
        if "/api/document/" in url:
            self.doc_gets.append(url)
            return _resp(200, {"xml_data": {"rFE": RFE}} if self.rfe_ready else {})
        if "GetResultFe" in url:
            return _resp(200, {"authorized": False})
        return _resp(404, text="")


class FfEnvioCommon(TestPoSCommon):
    """POS con el agente activo y simulado (sin tests propios)."""

    def setUp(self):
        super().setUp()
        self.config = self.basic_config
        self.config.write({
            "ff_agent_enabled": True,
            "ff_base_url": "http://agente.test",
            "ff_company_uuid": "company",
            "ff_api_key": "key",
            "ff_print_cafe": False,
        })
        self.product = self.create_product("Producto FE", self.categ_basic, 10.0, 5.0)
        self.open_new_session()
        self.patch(mod, "FF_POLL_WAIT", 0)
        self.commits = 0

        def _commit():
            self.commits += 1
        # _ff_sign y el cron hacen commit para dejar firme cada paso del envio
        self.patch(self.env.cr, "commit", _commit)

    def _sincronizar(self, agent):
        """Lo que hace el POS al validar: sync_from_ui (sin agente)."""
        with patch.object(mod.requests, "post", agent.post), patch.object(mod.requests, "get", agent.get):
            orders = self._create_orders([{
                "pos_order_lines_ui_args": [(self.product, 1)],
                "payments": [(self.cash_pm1, 10.0)],
            }])
        return next(iter(orders.values()))

    def _vender(self, agent):
        """Sincronizar + la llamada de emision que hace el POS justo despues."""
        order = self._sincronizar(agent)
        self._con(agent, order.ff_emitir_data)
        return order

    def _con(self, agent, fn, *args):
        with patch.object(mod.requests, "post", agent.post), patch.object(mod.requests, "get", agent.get):
            return fn(*args)


@tagged("post_install", "-at_install")
class TestFfEnvio(FfEnvioCommon):

    def test_sincronizar_no_habla_con_el_agente(self):
        """La venta se guarda sin HTTP; el cron queda programado de respaldo."""
        agent = FakeAgent()
        triggers = self.env["ir.cron.trigger"].search_count([])
        order = self._sincronizar(agent)
        self.assertEqual(order.fe_estado, "pendiente")
        self.assertEqual((agent.posts, agent.doc_gets), (0, []))
        self.assertGreater(self.env["ir.cron.trigger"].search_count([]), triggers)
        self._con(agent, self.env["ff.fe.document"]._cron_ff_update_status)
        self.assertEqual(order.fe_estado, "timbrada")

    def test_marca_incierta_con_commit_antes_del_post(self):
        """Si el proceso muere durante el POST, la orden ya quedo 'incierta'."""
        visto = {}

        def on_post():
            visto.update(estado=order.fe_estado, commits=self.commits)
        agent = FakeAgent(on_post=on_post)
        order = self._sincronizar(agent)
        self._con(agent, order.ff_emitir_data)
        self.assertEqual(visto["estado"], "incierta")
        self.assertGreaterEqual(visto["commits"], 1)
        self.assertEqual(order.fe_estado, "timbrada")

    def test_emitir_data_devuelve_el_cafe(self):
        agent = FakeAgent()
        order = self._sincronizar(agent)
        rec = self._con(agent, order.ff_emitir_data)
        self.assertEqual(rec["fe_estado"], "timbrada")
        self.assertEqual(json.loads(rec["fe_doc_json"])["nro"], "0000000001")

    def test_el_pos_no_decide_el_estado_fe(self):
        """El estado fiscal lo escribe solo el servidor: una orden que llega
        del POS diciendo 'autorizada' con un CUFE se emite igual."""
        agent = FakeAgent()
        data = self.create_ui_order_data([(self.product, 1)], payments=[(self.cash_pm1, 10.0)])
        data.update(fe_estado="autorizada", fe_cufe="FE01FALSO", fe_document_uuid="uuid-falso")
        order_id = self.env["pos.order"].sync_from_ui([data])["pos.order"][0]["id"]
        order = self.env["pos.order"].browse(order_id)
        self.assertEqual((order.fe_estado, order.fe_cufe, order.fe_document_uuid), ("pendiente", False, False))
        self._con(agent, order.ff_emitir_data)
        self.assertEqual((order.fe_estado, order.fe_cufe), ("timbrada", "FE01TESTCUFE"))

    def test_firmar_desde_backend_crea_documento_y_emite(self):
        """El boton 'Firmar' del backend cierra el ciclo: una orden cobrada que
        aun no tiene documento (p.ej. creada en el backend) crea el ff.fe.document
        y lo emite en el momento."""
        agent = FakeAgent()
        order = self._sincronizar(agent)
        # Simular una orden SIN documento (como una creada fuera del POS).
        order.fe_document_id.sudo().unlink()  # doc protegido: borrar solo para el fixture
        self.assertFalse(order.fe_document_id)
        self.assertTrue(order.ff_puede_firmar)          # se ofrece firmar
        # Firmar desde el backend.
        self._con(agent, order.action_ff_firmar)
        self.assertTrue(order.fe_document_id)            # creo el documento
        self.assertEqual(order.fe_estado, "timbrada")    # y emitio
        self.assertFalse(order.ff_puede_firmar)          # ya no se ofrece

    def test_no_se_firma_sin_ff_activo(self):
        """action_ff_firmar exige orden cobrada + POS con Factura Fácil activo."""
        agent = FakeAgent()
        order = self._sincronizar(agent)
        order.fe_document_id.sudo().unlink()  # doc protegido: borrar solo para el fixture
        self.config.ff_agent_enabled = False  # el POS ya no emite FE
        self.assertFalse(order.ff_puede_firmar)
        with self.assertRaises(UserError):
            self._con(agent, order.action_ff_firmar)

    @mute_logger("odoo.addons.point_of_sale.models.pos_order")  # borrador sin pagos
    def test_solo_las_ventas_cobradas_tienen_documento(self):
        """Un borrador (p.ej. una mesa abierta) nunca se factura."""
        agent = FakeAgent()
        data = self.create_ui_order_data([(self.product, 1)], payments=[])
        data["state"] = "draft"
        order_id = self.env["pos.order"].sync_from_ui([data])["pos.order"][0]["id"]
        order = self.env["pos.order"].browse(order_id)
        self.assertFalse(order.fe_document_id)
        self._con(agent, self.env["ff.fe.document"]._cron_ff_update_status)
        self._con(agent, order.ff_emitir_data)
        self.assertEqual(agent.posts, 0)

    def test_sin_agente_no_hay_documento(self):
        self.config.ff_agent_enabled = False
        order = self._sincronizar(FakeAgent())
        self.assertFalse(order.fe_document_id)
        self.assertFalse(order.fe_estado)

    def test_documento_invalido_queda_en_error_sin_enviar(self):
        agent = FakeAgent()
        order = self._sincronizar(agent)
        with patch.object(type(order), "_ff_build_payload", side_effect=UserError("no cuadra")):
            self._con(agent, order.ff_emitir_data)
        self.assertEqual((order.fe_estado, order.fe_error), ("error", "no cuadra"))
        self.assertEqual(agent.posts, 0)

    def test_venta_ok(self):
        agent = FakeAgent()
        order = self._vender(agent)
        self.assertEqual(order.fe_estado, "timbrada")
        self.assertEqual(order.fe_cufe, "FE01TESTCUFE")
        self.assertEqual(order.fe_document_uuid, "uuid-1")
        self.assertEqual(agent.posts, 1)

    def test_agente_caido_se_reenvia(self):
        """Conexion rechazada: el POST no llego, reenviar es seguro."""
        agent = FakeAgent(post=[_rechazada_conexion()])
        order = self._vender(agent)
        self.assertEqual(order.fe_estado, "pendiente")
        self.assertFalse(order.fe_document_uuid)
        self._con(agent, order.fe_document_id._ff_retry)
        self.assertEqual(order.fe_estado, "timbrada")
        self.assertEqual(agent.posts, 2)

    def test_agente_apagado_detras_de_proxy_no_envia(self):
        """Healthcheck sin respuesta (Docker Desktop acepta la conexion con el
        agente apagado): no se hace POST y la orden queda reenviable."""
        agent = FakeAgent(alive=False)
        order = self._vender(agent)
        self.assertEqual(order.fe_estado, "pendiente")
        self.assertEqual(agent.posts, 0)
        agent.alive = True
        self._con(agent, self.env["ff.fe.document"]._cron_ff_update_status)
        self.assertEqual(order.fe_estado, "timbrada")
        self.assertEqual(agent.posts, 1)

    def test_timeout_de_lectura_queda_incierta(self):
        """El agente pudo haber creado el documento: no se reenvia nunca solo."""
        agent = FakeAgent(post=[requests.ReadTimeout("read timed out")])
        order = self._vender(agent)
        self.assertEqual(order.fe_estado, "incierta")
        self._con(agent, order.fe_document_id._ff_retry)
        self._con(agent, self.env["ff.fe.document"]._cron_ff_update_status)
        self.assertEqual(order.fe_estado, "incierta")
        self.assertEqual(agent.posts, 1, "ni el reintento ni el cron deben reenviar")

    def test_corte_a_mitad_queda_incierta(self):
        agent = FakeAgent(post=[requests.ConnectionError("Connection aborted.")])
        order = self._vender(agent)
        self.assertEqual(order.fe_estado, "incierta")

    def test_5xx_queda_incierta(self):
        agent = FakeAgent(post=[_resp(500, text="<title>NameError</title>")])
        order = self._vender(agent)
        self.assertEqual(order.fe_estado, "incierta")
        self.assertIn("NameError", order.fe_error)

    def test_4xx_es_error_y_se_puede_reenviar(self):
        """Validacion rechazada por el agente: no creo nada, reenviar es seguro."""
        agent = FakeAgent(post=[_resp(400, {"document": {"receptor": {"ruc": ["invalido"]}}})])
        order = self._vender(agent)
        self.assertEqual(order.fe_estado, "error")
        self.assertIn("receptor.ruc", order.fe_error)
        self._con(agent, order.fe_document_id._ff_retry)
        self.assertEqual(order.fe_estado, "timbrada")
        self.assertEqual(agent.posts, 2)

    def test_creada_sin_cufe_se_retoma_sin_reenviar(self):
        """201 con UUID pero la firma aun no esta: se consulta ese UUID."""
        agent = FakeAgent(rfe_ready=False)
        order = self._vender(agent)
        self.assertEqual((order.fe_estado, order.fe_document_uuid), ("pendiente", "uuid-1"))
        agent.rfe_ready = True
        self._con(agent, self.env["ff.fe.document"]._cron_ff_update_status)
        self.assertEqual(order.fe_estado, "timbrada")
        self.assertEqual(agent.posts, 1)
        self.assertTrue(agent.doc_gets[-1].endswith("/uuid-1/"))

    def test_duplicado_adopta_el_original(self):
        agent = FakeAgent(post=[_resp(201, {
            "document_uuid": "uuid-nuevo", "duplicated": True,
            "original": {"document_uuid": "uuid-original"},
        })])
        order = self._vender(agent)
        self.assertEqual(order.fe_document_uuid, "uuid-original")

    def test_incierta_vinculando_uuid_se_retoma(self):
        agent = FakeAgent(post=[requests.ReadTimeout()])
        order = self._vender(agent)
        order.fe_document_uuid = "uuid-encontrado"
        self._con(agent, order.fe_document_id._ff_retry)
        self.assertEqual(order.fe_estado, "timbrada")
        self.assertEqual(agent.posts, 1)
        self.assertTrue(agent.doc_gets[-1].endswith("/uuid-encontrado/"))

    def test_forzar_reenvio(self):
        agent = FakeAgent(post=[requests.ReadTimeout()])
        order = self._vender(agent)
        # Recien enviada: el POST podria seguir en curso, no se deja forzar
        with self.assertRaises(UserError):
            self._con(agent, order.action_ff_forzar_reenvio)
        order.fe_document_id.sudo().fe_request_date = fields.Datetime.subtract(fields.Datetime.now(), minutes=5)
        self._con(agent, order.action_ff_forzar_reenvio)
        self.assertEqual(order.fe_estado, "timbrada")
        self.assertEqual(agent.posts, 2)
        # Con documento ya vinculado no se puede forzar
        with self.assertRaises(UserError):
            order.action_ff_forzar_reenvio()

    def test_forzar_reenvio_solo_supervisor(self):
        agent = FakeAgent(post=[requests.ReadTimeout()])
        order = self._vender(agent)
        cajero = self.env["res.users"].create({
            "name": "Cajero", "login": "cajero_ff",
            "groups_id": [(6, 0, [self.env.ref("point_of_sale.group_pos_user").id])],
        })
        with self.assertRaises(UserError):
            order.with_user(cajero).action_ff_forzar_reenvio()

    def test_rechazada_reemite_documento_nuevo(self):
        agent = FakeAgent()
        order = self._vender(agent)
        order.fe_document_id.sudo().write({"fe_estado": "rechazada", "fe_error": "Rechazo 2002"})
        self._con(agent, order.fe_document_id._ff_retry)
        self.assertEqual(agent.posts, 2)
        self.assertEqual(order.fe_document_uuid, "uuid-2")
        self.assertIn("Reemisión tras rechazo DGI", order.fe_response_json)

    def test_cron_no_insiste_con_agente_caido(self):
        agent = FakeAgent(post=[_rechazada_conexion(), _rechazada_conexion()])
        o1 = self._vender(agent)
        o2 = self._vender(agent)
        self.assertEqual((o1.fe_estado, o2.fe_estado), ("pendiente", "pendiente"))
        agent.post_results = [_rechazada_conexion(), _rechazada_conexion()]
        self._con(agent, self.env["ff.fe.document"]._cron_ff_update_status)
        self.assertEqual(agent.posts, 3, "tras el primer fallo de conexion, el cron no prueba el resto")

    @mute_logger("odoo.addons.ff_pos_agent.models.ff_fe_document")
    def test_fallo_tras_post_aceptado_conserva_uuid(self):
        """Si algo revienta despues del 201, el UUID no se pierde."""
        agent = FakeAgent()
        with patch.object(type(self.env["ff.fe.document"]), "_ff_apply_signed", side_effect=ValueError("boom")):
            order = self._vender(agent)
        self.assertEqual((order.fe_estado, order.fe_document_uuid), ("pendiente", "uuid-1"))
        self._con(agent, order.fe_document_id._ff_retry)
        self.assertEqual(order.fe_estado, "timbrada")
        self.assertEqual(agent.posts, 1)

    def test_auditoria_conserva_historial(self):
        agent = FakeAgent(post=[_rechazada_conexion()])
        order = self._vender(agent)
        self._con(agent, order.fe_document_id._ff_retry)
        self.assertEqual(order.fe_response_json.count("=== Envío"), 2)
