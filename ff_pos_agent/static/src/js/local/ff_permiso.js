/** @odoo-module **/
/**
 * Permiso de acceso a la red local (LNA) para hablar con el agente de la caja.
 *
 * Chrome solo muestra el aviso de permiso cuando la peticion nace de un GESTO
 * DEL USUARIO. Una peticion automatica al abrir el POS no pregunta nada: la
 * rechaza en silencio y el permiso se queda en "prompt" para siempre.
 *
 * Por eso aqui no se sondea sin mas: si el permiso no esta concedido se enseña
 * un aviso con un boton, y es la pulsacion la que dispara la peticion que hace
 * aparecer el dialogo de Chrome.
 */
import { _t } from "@web/core/l10n/translation";
import { sinServiceWorker } from "./ff_agent";
import { ffLog, ffWarn } from "./ff_log";

/** granted | prompt | denied | no-soportado */
export async function ffEstadoPermiso() {
    try {
        const r = await navigator.permissions.query({ name: "local-network-access" });
        return r.state;
    } catch {
        return "no-soportado";
    }
}

/**
 * Pide el permiso. DEBE llamarse desde un manejador de evento del usuario
 * (click), o Chrome no preguntara.
 */
export async function ffPedirPermiso(baseUrl) {
    // Sin esquivar el service worker, Chrome nunca llega a preguntar.
    const url = sinServiceWorker((baseUrl || "").replace(/\/+$/, "") + "/api/healthcheck/");
    try {
        await fetch(url, { method: "GET", targetAddressSpace: "loopback" });
    } catch {
        // Da igual si la respuesta falla: lo que importa es que la peticion
        // nacio de un gesto y Chrome ya mostro (o no) su dialogo.
    }
    const estado = await ffEstadoPermiso();
    ffLog("Permiso de red local tras pedirlo", { estado });
    return estado;
}

/**
 * Al abrir la caja: si falta el permiso, avisa con un boton que lo solicita.
 * No sondea la red por su cuenta — eso es lo que quemaba el permiso.
 */
export async function ffAsegurarPermiso(pos) {
    const cfg = pos.config;
    const notification = pos.env?.services?.notification;
    const estado = await ffEstadoPermiso();
    ffLog("Permiso de red local", { estado });

    if (estado === "granted") {
        return true;
    }

    if (estado === "no-soportado") {
        ffWarn("Este navegador no puede hablar con el agente de la caja", {
            causa: "No soporta Local Network Access.",
            arreglo: "Usa Chrome, Edge o Brave 142 o superior. Firefox e iOS no sirven.",
        });
        notification?.add(
            _t(
                "Este navegador no puede conectarse al agente de esta caja. " +
                    "Usa Chrome, Edge o Brave 142 o superior."
            ),
            { type: "danger", sticky: true }
        );
        return false;
    }

    // prompt o denied: hace falta que el usuario lo conceda.
    const mensaje =
        estado === "denied"
            ? _t(
                  "El acceso a la red local está BLOQUEADO para este sitio, así que no se " +
                      "puede emitir la factura electrónica. Ábrelo en el candado de la barra " +
                      "de direcciones → Configuración del sitio → Acceso a la red local → Permitir."
              )
            : _t(
                  "Falta autorizar el acceso al agente de facturación de esta caja. " +
                      "Sin eso no se emite la factura electrónica."
              );

    notification?.add(mensaje, {
        type: "warning",
        sticky: true,
        buttons:
            estado === "denied"
                ? []
                : [
                      {
                          name: _t("Autorizar el agente"),
                          onClick: async () => {
                              // Nace de un click: aqui Chrome SI pregunta.
                              const nuevo = await ffPedirPermiso(cfg.ff_base_url);
                              notification?.add(
                                  nuevo === "granted"
                                      ? _t("Agente autorizado. Ya se puede emitir.")
                                      : _t(
                                            "No se concedió el permiso. Revísalo en el candado " +
                                                "de la barra de direcciones → Configuración del sitio."
                                        ),
                                  { type: nuevo === "granted" ? "success" : "danger", sticky: nuevo !== "granted" }
                              );
                          },
                      },
                  ],
    });
    return false;
}
