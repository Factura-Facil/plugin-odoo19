/** @odoo-module **/
/**
 * Al abrir el POS en modo "agente en la caja", cierra los envios que quedaron
 * en el aire: caja apagada durante la emision, navegador cerrado antes de que
 * Odoo registrara el resultado, o corte de red al reportar.
 *
 * No reenvia nada: consulta lo que ya existe y, si no hay UUID, lo deja
 * marcado como envio incierto para que un responsable lo resuelva.
 */
import { patch } from "@web/core/utils/patch";
import { PosStore } from "@point_of_sale/app/services/pos_store";
import { ffAsegurarPermiso } from "./ff_permiso";
import { ffError, ffLog, ffWarn } from "./ff_log";
import { ffEmitirEnCaja, ffReconciliarCaja } from "./ff_local";

patch(PosStore.prototype, {
    async afterProcessServerData() {
        const res = await super.afterProcessServerData(...arguments);
        // Que caja pregunta. El validador de RUC lo manda al servidor para que
        // use el agente de ESTA caja en vez del de la compañia (ver
        // res.partner._ff_ruc_params).
        if (this.config?.ff_agent_enabled) {
            window.__ffPosConfigId = this.config.id;
        }
        if (this.config?.ff_agent_enabled && this.config?.ff_mode === "agent_local") {
            // El validador de RUC (widget compartido con el backend) necesita
            // saber que hay un agente local y como llamarlo, sin depender del
            // servicio del POS: se publica aqui.
            window.__ffLocal = {
                base_url: this.config.ff_base_url,
                env_name: this.config.ff_environment === "1" ? "prod" : "test",
                company_uuid: this.config.ff_company_uuid || "",
                api_key: this.config.ff_api_key || "",
            };
            ffLog("POS abierto en modo 'Agente en la caja'", {
                agente: this.config.ff_base_url,
                pos: this.config.ff_pos_code,
                ambiente: this.config.ff_environment === "1" ? "Producción" : "Pruebas",
                ayuda:
                    "ffFeLog() traza · ffFeDiag() diagnóstico · ffFeEmitir() emitir la orden actual",
            });
            // Sin await: no deben retrasar la apertura de la caja.
            //
            // OJO: aqui NO se sondea la red. Chrome solo muestra el dialogo de
            // permiso cuando la peticion nace de un gesto del usuario; una
            // peticion automatica se rechaza en silencio y el permiso se queda
            // en "prompt" para siempre. Se avisa con un boton, y es el click
            // quien dispara la peticion que hace aparecer el dialogo.
            // Disparador manual, para probar sin hacer una venta entera:
            //   ffFeEmitir()        -> la orden actual
            //   ffFeEmitir(1234)    -> esa orden por id
            const pos = this;
            window.ffFeEmitir = async (idOrden) => {
                const orden = idOrden
                    ? pos.models["pos.order"].find((o) => o.id === idOrden)
                    : pos.getOrder();
                if (!orden?.id) {
                    console.warn("[FE Caja] Esa orden no existe o aún no se sincronizó.");
                    return null;
                }
                return await ffEmitirEnCaja(pos, orden);
            };
            // Owl registra los fallos de render como "[object Object]", que no
            // dice absolutamente nada. Estos dos oyentes sacan el error de
            // verdad: mensaje, tipo, pila y el componente que lo provoco.
            if (!window.__ffErrHandlers) {
                window.__ffErrHandlers = true;
                window.addEventListener("error", (ev) => {
                    ffWarn("ERROR NO CAPTURADO", {
                        ...ffError(ev.error || ev),
                        origen: ev.filename ? `${ev.filename}:${ev.lineno}` : undefined,
                    });
                });
                window.addEventListener("unhandledrejection", (ev) => {
                    const motivo = ev.reason;
                    ffWarn("PROMESA RECHAZADA", {
                        ...ffError(motivo),
                        // Los errores de Owl traen el componente que fallo.
                        componente: motivo?.node?.component?.constructor?.name
                            || motivo?.cause?.node?.component?.constructor?.name
                            || undefined,
                        causa: motivo?.cause ? ffError(motivo.cause) : undefined,
                    });
                });
            }
            ffAsegurarPermiso(this).catch(() => {});
            ffReconciliarCaja(this).catch((e) =>
                console.warn("[ff_pos_agent] reconciliación al abrir:", e)
            );
        }
        return res;
    },
});
