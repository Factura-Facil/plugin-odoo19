"""18.0.2.0.0: el estado FE pasa de columnas de pos_order a ff.fe.document.

Antes de actualizar se copia lo que habia en pos_order a una tabla temporal
y se vacian las columnas que pasan a ser related (Odoo no recalcula una
columna que ya existe). El post-migrate crea los documentos.

Solo se migran las ordenes con actividad FE real (payload enviado, CUFE o
UUID). La 1.0 ponia 'pendiente' por defecto a TODA orden, incluso a las que
ya existian al instalar: esas no tienen documento y no se deben emitir.
"""

COLS = ("fe_estado", "fe_cufe", "fe_nro_dgi", "fe_protocolo", "fe_confirmacion",
        "fe_error", "fe_qr_base64", "fe_qr_url", "fe_document_uuid", "fe_doc_json",
        "fe_request_json", "fe_response_json", "fe_request_date", "fe_impreso")
STORED_RELATED = COLS[:10]


def migrate(cr, version):
    if not version:
        return
    cr.execute("""SELECT column_name FROM information_schema.columns
                  WHERE table_name = 'pos_order' AND column_name = ANY(%s)""", [list(COLS)])
    present = [c for (c,) in cr.fetchall()]
    if "fe_estado" not in present:
        return
    cr.execute("DROP TABLE IF EXISTS ff_fe_legacy")
    cr.execute("CREATE TABLE ff_fe_legacy AS SELECT id AS order_id, %s FROM pos_order "
               "WHERE fe_request_json IS NOT NULL OR fe_cufe IS NOT NULL "
               "OR fe_document_uuid IS NOT NULL" % ", ".join(present))
    cr.execute("UPDATE pos_order SET %s" % ", ".join(
        "%s = NULL" % c for c in STORED_RELATED if c in present))
