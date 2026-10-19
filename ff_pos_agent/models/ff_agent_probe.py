"""Prueba de conexion con Factura Facil, compartida por el POS y la compania.

Quien le pega al agente (o a la API en linea) es el SERVIDOR de Odoo, no el
navegador: esta prueba se ejecuta ahi para que el error que ve el usuario sea
el real ("no resuelve el DNS", "credenciales rechazadas") y no un CORS.
"""
import requests

from odoo import _


def ff_probe_agent(base_url, mode, company_uuid, api_key):
    """(ok, detalle) contra la URL dada. `mode`: 'agent' u 'online'."""
    base = (base_url or "").rstrip("/")
    try:
        if mode == "online":
            # La API en linea no tiene healthcheck: se consulta un documento
            # que no existe. 404 = credenciales validas; 401/403 = invalidas.
            r = requests.get(base + "/api/pac/reception_fe/find_by_cufe_or_id/",
                             params={"cufe_or_id": "00000000-0000-4000-8000-000000000000"},
                             headers={"X-FF-Company": company_uuid or "",
                                      "X-FF-API-Key": api_key or ""},
                             timeout=(5, 10))
            ok = r.status_code == 404
            detail = (_("credenciales rechazadas (HTTP %s)", r.status_code)
                      if r.status_code in (401, 403) else _("HTTP %s", r.status_code))
        else:
            r = requests.get(base + "/api/healthcheck/",
                             headers={"X-FF-Company": company_uuid or ""},
                             timeout=(5, 5))
            ok = r.status_code == 200
            detail = _("HTTP %s", r.status_code)
    except requests.RequestException as e:
        ok, detail = False, e.__class__.__name__
    return ok, detail


def ff_probe_notification(base_url, mode, company_uuid, api_key):
    """La prueba anterior, ya envuelta en la notificacion que espera el cliente."""
    base = (base_url or "").rstrip("/")
    ok, detail = ff_probe_agent(base_url, mode, company_uuid, api_key)
    quien = _("La API de Factura Fácil") if mode == "online" else _("El agente")
    if ok:
        msg, ntype = _("%(quien)s responde en %(url)s.", quien=quien, url=base), "success"
    else:
        msg, ntype = _("%(quien)s no responde en %(url)s (%(d)s). La URL debe ser "
                       "alcanzable desde el servidor de Odoo, no desde tu PC.",
                       quien=quien, url=base, d=detail), "danger"
    return {
        "type": "ir.actions.client",
        "tag": "display_notification",
        "params": {"title": _("Agente Factura Fácil"), "message": msg,
                   "type": ntype, "sticky": not ok},
    }
