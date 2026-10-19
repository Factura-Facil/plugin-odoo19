"""18.0.2.0.0: crea un ff.fe.document por cada orden con actividad FE."""
from odoo import SUPERUSER_ID, api

COLS = ("fe_estado", "fe_cufe", "fe_nro_dgi", "fe_protocolo", "fe_confirmacion",
        "fe_error", "fe_qr_base64", "fe_qr_url", "fe_document_uuid", "fe_doc_json",
        "fe_request_json", "fe_response_json", "fe_request_date", "fe_impreso")


def migrate(cr, version):
    cr.execute("SELECT to_regclass('ff_fe_legacy')")
    if not cr.fetchone()[0]:
        return
    env = api.Environment(cr, SUPERUSER_ID, {})
    cr.execute("SELECT * FROM ff_fe_legacy ORDER BY order_id")
    rows = cr.dictfetchall()
    Doc = env["ff.fe.document"]
    for row in rows:
        order = env["pos.order"].browse(row["order_id"]).exists()
        if not order:
            continue
        vals = {c: row[c] for c in COLS if c in row and row[c] is not None}
        vals.setdefault("fe_estado", "pendiente")
        doc = Doc.create({
            **vals,
            "name": order._ff_header_id(),
            "pos_order_id": order.id,
            "company_id": order.company_id.id,
        })
        order.sudo().fe_document_id = doc
    cr.execute("DROP TABLE ff_fe_legacy")
