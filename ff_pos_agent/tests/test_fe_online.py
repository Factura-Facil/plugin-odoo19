"""Modo API en linea: mismo documento, otra forma de responder.

La API en linea responde la emision de forma sincrona (DocumentResult con
CUFE, autorizacion o rechazo), no tiene healthcheck ni GetResultFe (el estado
se consulta con find_by_cufe_or_id) y no imprime: el PDF oficial del CAFE
(download_cafe) queda adjunto a la orden. El XML de la respuesta de emision
viene truncado; el que arma el CAFE es el xml_data de find_by_cufe_or_id.
"""
from pathlib import Path
from unittest.mock import patch

import requests

from odoo.addons.ff_pos_agent.models import pos_config as cfg_mod
from odoo.addons.ff_pos_agent.models import res_partner as partner_mod
from odoo.addons.ff_pos_agent.tests.test_fe_envio import FfEnvioCommon, _resp
from odoo.tests import tagged

RFE_XML = (Path(__file__).parent / "data" / "rfe_online.xml").read_text(encoding="utf-8")
CUFE = "FE0120000000357-814-78606-9100002026092300000000640030125031146146"
UUID = "101c06a9-77b6-4fa5-a367-3074c6ea0df6"
QR = "https://dgi-fep-test.mef.gob.pa:40001/Consultas/FacturasPorQR?chFE=%s&iAmb=2&jwt=x" % CUFE
PDF = b"%PDF-1.7 cafe oficial"


def _autorizada(**kw):
    return {
        "cufe": CUFE, "document_uuid": UUID, "document_number": "0000000064",
        "rejected": False, "authorization_number": "0000155723374-2-2022",
        "qr_code_data": QR, "qr_image_b64": "data:image/png;base64,QUJD",
        "messages": [{"code": "0260", "message": "Autorizado el uso de la FE", "type": "N"}],
        "duplicated": False, "original": None, **kw,
    }


class FakeOnline:
    """API en linea simulada (misma interfaz que FakeAgent)."""

    def __init__(self, post=None, find_ok=True, status=3, pdf_ok=True):
        self.post_results = list(post or [])
        self.find_ok = find_ok
        self.status = status
        self.pdf_ok = pdf_ok
        self.posts = 0
        self.gets = []

    def post(self, url, **kw):
        self.posts += 1
        res = self.post_results.pop(0) if self.post_results else _resp(201, _autorizada())
        if isinstance(res, Exception):
            raise res
        return res

    def get(self, url, **kw):
        self.gets.append(url)
        if "find_by_cufe_or_id" in url:
            if not self.find_ok:
                raise requests.ConnectTimeout()
            return _resp(200, {"id": UUID, "cufe": CUFE, "status": self.status,
                               "status_display": "Finalizado", "xml_data": RFE_XML})
        if "download_cafe" in url:
            if not self.pdf_ok:
                return _resp(404, {"detail": "El documento no existe"})
            r = requests.Response()
            r.status_code, r._content = 200, PDF
            return r
        return _resp(404, text="")


@tagged("post_install", "-at_install")
class TestFfOnline(FfEnvioCommon):

    def setUp(self):
        super().setUp()
        self.config.write({"ff_mode": "online",
                           "ff_base_url": "https://backend-qa-api.facturafacil.com.pa"})

    def test_autorizada_al_momento_con_cafe_adjunto(self):
        api = FakeOnline()
        order = self._vender(api)
        self.assertEqual(order.fe_estado, "autorizada")
        self.assertEqual((order.fe_cufe, order.fe_nro_dgi), (CUFE, "0000000064"))
        self.assertEqual(order.fe_protocolo, "0000155723374-2-2022")
        self.assertEqual(order.fe_qr_url, QR, "el QR oficial (con jwt) de la respuesta")
        self.assertFalse(any("healthcheck" in u for u in api.gets), "la API en linea no tiene healthcheck")
        pdf = order.fe_cafe_pdf_id
        self.assertEqual((pdf.res_model, pdf.res_id, pdf.mimetype), ("pos.order", order.id, "application/pdf"))
        self.assertEqual(pdf.raw, PDF)
        rec = self._con(api, order.ff_emitir_data)
        self.assertEqual(rec["fe_cafe_url"], "/web/content/%s?download=false" % pdf.id)

    def test_el_cafe_del_recibo_sale_del_xml_firmado(self):
        order = self._vender(FakeOnline())
        import json
        cafe = json.loads(order.fe_doc_json)
        self.assertEqual(cafe["nro"], "0000000064")
        self.assertEqual(cafe["emisor"]["ruc"], "357-814-78606")
        self.assertEqual(cafe["pos"], "003")

    def test_rechazada_al_momento(self):
        api = FakeOnline(post=[_resp(201, _autorizada(
            rejected=True, authorization_number=None,
            messages=[{"code": "2002", "message": "Unidad de medida invalida", "type": "R"}]))])
        order = self._vender(api)
        self.assertEqual(order.fe_estado, "rechazada")
        self.assertIn("2002", order.fe_error)
        self.assertFalse(order.fe_cafe_pdf_id)

    def test_sin_xml_se_retoma_sin_reemitir(self):
        """201 aceptado pero find falla: la orden espera sin volver a emitir."""
        api = FakeOnline(find_ok=False)
        order = self._vender(api)
        self.assertEqual((order.fe_estado, order.fe_document_uuid, order.fe_cufe), ("pendiente", UUID, CUFE))
        api.find_ok = True
        self._con(api, self.env["ff.fe.document"]._cron_ff_update_status)
        self.assertEqual(order.fe_estado, "autorizada")
        self.assertTrue(order.fe_cafe_pdf_id, "el cron tambien adjunta el PDF")
        self.assertEqual(api.posts, 1)

    def test_timbrada_se_consulta_con_find(self):
        api = FakeOnline(post=[_resp(201, _autorizada(authorization_number=None))])
        order = self._vender(api)
        self.assertEqual(order.fe_estado, "timbrada")
        self._con(api, self.env["ff.fe.document"]._cron_ff_update_status)
        self.assertEqual(order.fe_estado, "autorizada")
        self.assertTrue(any("find_by_cufe_or_id" in u for u in api.gets))

    def test_pdf_que_fallo_se_reintenta(self):
        api = FakeOnline(pdf_ok=False)
        order = self._vender(api)
        self.assertEqual(order.fe_estado, "autorizada")
        self.assertFalse(order.fe_cafe_pdf_id)
        self.assertIn("download_cafe", order.fe_document_id.fe_response_json)
        api.pdf_ok = True
        res = self._con(api, order.action_ff_ver_cafe)
        self.assertEqual(res["type"], "ir.actions.act_url")
        self.assertTrue(order.fe_cafe_pdf_id)
        self.assertEqual(api.posts, 1)

    def test_timeout_sigue_siendo_incierto(self):
        order = self._vender(FakeOnline(post=[requests.ReadTimeout()]))
        self.assertEqual(order.fe_estado, "incierta")

    def test_la_url_por_defecto_sigue_al_modo(self):
        cfg = self.env["pos.config"].new({"ff_mode": "agent", "ff_base_url": cfg_mod.FF_DEFAULT_URL["agent"]})
        cfg.ff_mode = "online"
        cfg._onchange_ff_mode()
        self.assertEqual(cfg.ff_base_url, cfg_mod.FF_DEFAULT_URL["online"])
        cfg.ff_base_url = "https://mi-api.example"
        cfg.ff_mode = "agent"
        cfg._onchange_ff_mode()
        self.assertEqual(cfg.ff_base_url, "https://mi-api.example", "no pisa una URL propia")

    def test_probar_conexion_en_linea(self):
        with patch.object(cfg_mod.requests, "get", return_value=_resp(404, {"detail": "No DocumentoPAC"})):
            self.assertEqual(self.config.action_ff_test_connection()["params"]["type"], "success")
        with patch.object(cfg_mod.requests, "get", return_value=_resp(403, {"detail": "API key"})):
            res = self.config.action_ff_test_connection()
        self.assertEqual(res["params"]["type"], "danger")
        self.assertIn("credenciales", res["params"]["message"])


@tagged("post_install", "-at_install")
class TestFfRucModos(FfEnvioCommon):
    """Validar RUC desde el POS: el cajero no lee la API Key, pero valida."""

    def setUp(self):
        super().setUp()
        self.config.ff_api_key = "K" * 20
        self.cajero = self.env["res.users"].create({
            "name": "Cajero RUC", "login": "cajero_ruc_modos",
            "company_id": self.env.company.id, "company_ids": [(6, 0, self.env.company.ids)],
            "groups_id": [(6, 0, self.env.ref("point_of_sale.group_pos_user").ids)],
        })

    def _check(self):
        ok = _resp(200, {"valido": True, "dv": "86", "tipoRuc": 2, "tipoReceptor": "01",
                         "razonSocial": "HYPERNOVA LABS, S.A"})
        with patch.object(partner_mod.requests, "get", return_value=ok) as get:
            res = self.env["res.partner"].with_user(self.cajero).ff_check_ruc("155646463-2-2017")
        return res, get.call_args[0][0]

    def test_agente(self):
        res, url = self._check()
        self.assertTrue(res["valido"], res)
        self.assertEqual(url, "http://agente.test/api/fepa/v1/test/checkRuc/155646463-2-2017/0")

    def test_en_linea(self):
        self.config.write({"ff_mode": "online", "ff_base_url": "https://backend-qa-api.facturafacil.com.pa"})
        res, url = self._check()
        self.assertTrue(res["valido"], res)
        self.assertEqual(url, "https://backend-qa-api.facturafacil.com.pa/api/gateway/wp/test/checkRUC/%s/"
                              "155646463-2-2017/0" % ("K" * 20))
