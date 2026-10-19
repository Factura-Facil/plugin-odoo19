import { Component, useState } from "@odoo/owl";
import { _t } from "@web/core/l10n/translation";
import { registry } from "@web/core/registry";
import { standardWidgetProps } from "@web/views/widgets/standard_widget_props";
import { useService } from "@web/core/utils/hooks";

/**
 * Boton "Validar RUC con la DGI" para la ficha de cliente.
 *
 * Es un widget y no un `<button type="object">` porque el dialogo de cliente
 * del POS (`point_of_sale.res_partner_action_edit_pos`) se cierra en cuanto el
 * registro se guarda (`makeActionAwaitable` resuelve en `onSave`), y un boton
 * de tipo object guarda antes de ejecutar. Aca no se guarda nada: se consulta,
 * se actualizan los campos en el formulario y el resultado se muestra en linea,
 * asi el cajero ve si funciono y el dialogo sigue abierto.
 */
export class FfRucValidator extends Component {
    static template = "ff_pos_agent.FfRucValidator";
    static props = { ...standardWidgetProps };

    setup() {
        this.orm = useService("orm");
        this.state = useState({ loading: false, result: null });
    }

    get ruc() {
        return (this.props.record.data.ff_ruc || "").trim();
    }

    /**
     * Consulta el RUC. Cuando la caja tiene su propio agente (modo "agente en
     * la caja"), el servidor no lo alcanza: consulta el navegador y el servidor
     * solo interpreta la respuesta, que es donde vive la logica.
     */
    async checkRuc() {
        const local = typeof window !== "undefined" ? window.__ffLocal : null;
        if (!local?.base_url) {
            // Fuera del modo local consulta el servidor. Se le dice desde que
            // caja se pregunta (si es que se pregunta desde una): sin eso usaria
            // la conexion de la compañia, que puede ser otra distinta.
            const posId = typeof window !== "undefined" ? window.__ffPosConfigId : null;
            return await this.orm.call("res.partner", "ff_check_ruc", [this.ruc, posId || null]);
        }
        const base = local.base_url.replace(/\/+$/, "");
        // El parámetro esquiva el service worker del POS, que intercepta los
        // GET y al reemitirlos pierde el permiso de red local (ver ff_agent.js).
        const url =
            `${base}/api/fepa/v1/${local.env_name}/checkRuc/` +
            `${encodeURIComponent(this.ruc)}/0?ff_sw=web/dataset`;
        let datos;
        try {
            const r = await fetch(url, {
                method: "GET",
                headers: {
                    "X-FF-Company": local.company_uuid,
                    "X-FF-API-Key": local.api_key,
                },
                targetAddressSpace: "loopback",
            });
            // El agente responde 400 con {"valido": false} cuando el RUC no
            // existe: el cuerpo se parsea igual, no se mira solo el estado.
            datos = await r.json();
        } catch (e) {
            return {
                valido: false,
                message:
                    "No se pudo contactar el agente de esta caja. " +
                    "Revisa que esté arrancado y que el navegador tenga permiso " +
                    `de acceso a la red local. (${e?.message || e})`,
            };
        }
        return await this.orm.call("res.partner", "ff_ruc_from_agent", [this.ruc, datos]);
    }

    async onValidate() {
        if (this.state.loading || !this.ruc) {
            return;
        }
        this.state.loading = true;
        this.state.result = null;
        try {
            const res = await this.checkRuc();
            if (res.valido) {
                const vals = { ff_ruc_validated: true };
                if (res.dv) {
                    vals.ff_dv = res.dv;
                }
                if (res.razon_social) {
                    vals.ff_razon_social = res.razon_social;
                    // El nombre oficial de la DGI pisa lo que escribio el cajero.
                    vals.name = res.razon_social;
                }
                if (res.ruc_type) {
                    vals.ff_ruc_type = res.ruc_type;
                }
                if (res.receptor_type) {
                    vals.ff_receptor_type = res.receptor_type;
                }
                await this.props.record.update(vals);
            } else {
                await this.props.record.update({ ff_ruc_validated: false });
            }
            this.state.result = { ok: Boolean(res.valido), message: res.message };
        } catch (error) {
            // Se atrapa a proposito: el dialogo de error del webclient taparia
            // el de creacion de cliente y el cajero perderia lo que escribio.
            this.state.result = {
                ok: false,
                message:
                    error.data?.message ||
                    error.message ||
                    _t("Fallo inesperado al validar el RUC."),
            };
        } finally {
            this.state.loading = false;
        }
    }
}

registry.category("view_widgets").add("ff_ruc_validator", {
    component: FfRucValidator,
    fieldDependencies: [{ name: "ff_ruc", type: "char" }],
});
