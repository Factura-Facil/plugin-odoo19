/** @odoo-module **/
import { patch } from "@web/core/utils/patch";
import { PosOrder } from "@point_of_sale/app/models/pos_order";

patch(PosOrder.prototype, {
    setup(vals) {
        super.setup(vals);
        this.fe_cufe = vals.fe_cufe || false;
        this.fe_estado = vals.fe_estado || "pendiente";
        this.fe_nro_dgi = vals.fe_nro_dgi || false;
        this.fe_protocolo = vals.fe_protocolo || false;
        this.fe_confirmacion = vals.fe_confirmacion || false;
        this.fe_error = vals.fe_error || false;
        this.fe_qr_url = vals.fe_qr_url || false;
        this.fe_qr_base64 = vals.fe_qr_base64 || false;
        this.fe_doc_json = vals.fe_doc_json || false;
        this.fe_impreso = vals.fe_impreso || false;
        // No es un campo del modelo: lo trae ff_emitir_data (API en linea).
        this.fe_cafe_url = false;
    },

    // La emision FE corre despues de sincronizar (ver order_validation_patch.js):
    // el POS la espera para que el recibo salga con el CAFE.
    // Odoo 19 renombro wait_for_push_order -> waitForPushOrder (camelCase).
    waitForPushOrder() {
        return super.waitForPushOrder(...arguments) || Boolean(this.config.ff_agent_enabled);
    },

    /**
     * Aplica en la orden local los datos FE que devuelve el servidor.
     *
     * Campo a campo y dentro de try/catch a proposito: a estas alturas el
     * documento fiscal YA esta emitido ante la DGI, asi que un fallo pintando
     * el recibo no puede tumbar el POS. Un `Object.assign` directo revienta
     * entero si el modelo del POS rechaza una sola clave (p.ej. un related no
     * almacenado que el cliente no conoce), y se lleva por delante la pantalla
     * con la venta ya cobrada y timbrada.
     */
    ffApplyFe(rec) {
        if (!rec) {
            return;
        }
        for (const [campo, valor] of Object.entries(rec)) {
            try {
                this[campo] = valor;
            } catch (e) {
                console.warn("[ff_pos_agent] no se pudo aplicar", campo, e);
            }
        }
    },

    // Datos FE para el template del recibo (CAFE). En Odoo 19 el recibo lee
    // directamente `props.order`, asi que exponemos un getter en la orden
    // (antes se inyectaban via export_for_printing, que ya no existe en 19).
    get ffFeReceipt() {
        try {
            return this._ffFeReceiptData();
        } catch (e) {
            // Este getter se evalua RENDERIZANDO el recibo: si lanza, Owl
            // destruye el componente raiz y la caja se queda en blanco con la
            // venta ya cobrada y timbrada. Mejor un recibo incompleto.
            console.warn("[ff_pos_agent] no se pudo armar el CAFE del recibo:", e);
            return { estado: this.fe_estado || false, enabled: false, doc: false };
        }
    },

    _ffFeReceiptData() {
        const cfg = this.config || {};
        let doc = false;
        try {
            doc = this.fe_doc_json ? JSON.parse(this.fe_doc_json) : false;
        } catch {
            doc = false;
        }
        return {
            estado: this.fe_estado,
            cufe: this.fe_cufe,
            nro_dgi: this.fe_nro_dgi,
            protocolo: this.fe_protocolo,
            confirmacion: this.fe_confirmacion,
            error: this.fe_error,
            qr_base64: this.fe_qr_base64,
            qr_url: this.fe_qr_url,
            pos: cfg.ff_pos_code,
            sucursal: cfg.ff_branch_code,
            ambiente: cfg.ff_environment,
            enabled: cfg.ff_agent_enabled,
            doc: doc,
            impreso: Boolean(this.fe_impreso),
        };
    },
});
