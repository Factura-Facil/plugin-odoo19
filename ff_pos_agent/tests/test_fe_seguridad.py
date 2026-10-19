"""Credenciales del agente y estado fiscal: quien puede ver y quien escribir."""
from unittest.mock import patch

import requests

from odoo.addons.ff_pos_agent.models import ff_fe_document as mod
from odoo.addons.ff_pos_agent.tests.test_fe_envio import FakeAgent, FfEnvioCommon
from odoo.exceptions import AccessError
from odoo.tests import tagged

API_KEY = "K" * 128


@tagged("post_install", "-at_install")
class TestFfSeguridad(FfEnvioCommon):

    def setUp(self):
        super().setUp()
        self.config.ff_api_key = API_KEY
        groups = self.env.ref("point_of_sale.group_pos_user") | self.env.ref("account.group_account_invoice")
        self.cajero = self.env["res.users"].create({
            "name": "Cajero FE", "login": "cajero_fe_seg",
            "company_id": self.env.company.id, "company_ids": [(6, 0, self.env.company.ids)],
            "groups_id": [(6, 0, groups.ids)],
        })
        self.responsable = self.env["res.users"].create({
            "name": "Responsable FE", "login": "responsable_fe_seg",
            "company_id": self.env.company.id, "company_ids": [(6, 0, self.env.company.ids)],
            "groups_id": [(6, 0, self.env.ref("point_of_sale.group_pos_manager").ids)],
        })

    def test_el_cajero_no_puede_leer_las_credenciales(self):
        cfg = self.config.with_user(self.cajero)
        with self.assertRaises(AccessError):
            cfg.read(["ff_api_key"])
        with self.assertRaises(AccessError):
            cfg.read(["ff_company_uuid"])
        self.assertNotIn("ff_api_key", cfg.read()[0])

    def test_las_credenciales_no_viajan_al_pos(self):
        """Ni siquiera cuando abre la caja un responsable."""
        data = {"pos.session": {"data": [{"config_id": self.config.id}]}}
        for user in (self.cajero, self.responsable):
            loaded = self.config.with_user(user)._load_pos_data(data)["data"][0]
            self.assertNotIn("ff_api_key", loaded)
            self.assertNotIn("ff_company_uuid", loaded)
            self.assertTrue(loaded["ff_agent_enabled"])

    def test_el_cajero_emite_con_la_clave_del_servidor(self):
        agent = FakeAgent()
        order = self._sincronizar(agent)
        self._con(agent, order.with_user(self.cajero).ff_emitir_data)
        self.assertEqual(order.fe_estado, "timbrada")
        self.assertEqual(agent.headers["X-FF-API-Key"], API_KEY)

    def test_el_estado_fiscal_no_se_escribe_a_mano(self):
        agent = FakeAgent()
        order = self._vender(agent)
        doc = order.fe_document_id
        with self.assertRaises(AccessError):
            doc.with_user(self.cajero).write({"fe_estado": "autorizada"})
        with self.assertRaises(AccessError):
            doc.with_user(self.responsable).write({"fe_estado": "autorizada", "fe_cufe": "X"})
        # En la orden (espejo) la escritura no tiene efecto
        order.with_user(self.responsable).write({"fe_estado": "rechazada", "fe_cufe": "FALSO"})
        self.assertEqual((order.fe_estado, order.fe_cufe), ("timbrada", "FE01TESTCUFE"))

    def test_el_responsable_solo_vincula_uuid_si_es_incierta(self):
        agent = FakeAgent(post=[requests.ReadTimeout()])
        order = self._vender(agent)
        self.assertEqual(order.fe_estado, "incierta")
        with self.assertRaises(AccessError):
            order.with_user(self.cajero).fe_document_uuid = "uuid-x"
        order.with_user(self.responsable).fe_document_uuid = "uuid-encontrado"
        self.assertEqual(order.fe_document_id.fe_document_uuid, "uuid-encontrado")
        # Ya no es incierta tras retomarlo: no se puede volver a cambiar
        self._con(agent, order.fe_document_id._ff_retry)
        self.assertEqual(order.fe_estado, "timbrada")
        with self.assertRaises(AccessError):
            order.with_user(self.responsable).fe_document_uuid = "otro"

    def test_la_clave_no_queda_en_la_auditoria(self):
        """printByCufe lleva la api_key en la URL, y el error de red de
        requests la repite: la auditoria debe enmascararla."""
        self.config.ff_print_cafe = True
        url = "http://agente.test/fe/rest/api/printByCufe/%s" % API_KEY
        agent = FakeAgent(print_result=requests.ConnectionError(
            "HTTPConnectionPool(host='agente.test'): Max retries exceeded with url: %s" % url))
        order = self._vender(agent)
        audit = order.fe_document_id.fe_response_json
        self.assertIn("printByCufe", audit)
        self.assertNotIn(API_KEY, audit)
        self.assertIn("***", audit)

    def test_probar_conexion(self):
        ok = patch.object(requests, "get", return_value=mod.requests.Response())
        with ok as get:
            get.return_value.status_code = 200
            res = self.config.action_ff_test_connection()
        self.assertEqual(res["params"]["type"], "success")
        with patch.object(requests, "get", side_effect=requests.ConnectTimeout()):
            res = self.config.action_ff_test_connection()
        self.assertEqual(res["params"]["type"], "danger")
        self.assertIn("servidor", res["params"]["message"])
