/** @odoo-module **/
/**
 * Traza de la emision desde la caja.
 *
 * Siempre activa (no depende del modo debug): cuando algo falla en una caja
 * real, el cajero no va a reproducirlo con las herramientas de desarrollo
 * abiertas. Queda en consola y en un buffer que se puede volcar despues.
 *
 * NUNCA se registran las credenciales: solo URL, estado y tiempos.
 */

const PREFIJO = "%c[FE Caja]";
const ESTILO = "background:#714B67;color:#fff;padding:1px 5px;border-radius:3px";
const MAX = 200;

const buffer = [];

function guardar(paso, datos) {
    buffer.push({ t: new Date().toISOString(), paso, ...datos });
    if (buffer.length > MAX) {
        buffer.shift();
    }
}

/** Traza normal. */
export function ffLog(paso, datos = {}) {
    guardar(paso, datos);
    console.log(PREFIJO, ESTILO, paso, datos);
}

/**
 * Algo salio mal pero el flujo continua.
 *
 * Lo importante va en el TEXTO del mensaje, no solo en el objeto: la consola
 * colapsa los objetos como "Object" y, al copiar el log para reportarlo, ese
 * contenido se pierde entero. Quien depura una caja remota solo tiene el texto.
 */
export function ffWarn(paso, datos = {}) {
    guardar(paso, { ...datos, nivel: "warn" });
    const resumen = [datos.error, datos.tipo && `(${datos.tipo})`, datos.componente && `en ${datos.componente}`]
        .filter(Boolean)
        .join(" ");
    console.warn(`${PREFIJO} ${paso}${resumen ? " — " + resumen : ""}`, ESTILO, datos);
    if (datos.stack) {
        console.warn(datos.stack);
    }
}

/**
 * Convierte un error en algo legible.
 *
 * En consola un error de Odoo sale como "[object Object]", que no dice nada a
 * quien tiene que arreglarlo. Aqui se saca el mensaje, el tipo y la traza.
 */
export function ffError(e) {
    if (!e) {
        return { error: "(sin detalle)" };
    }
    const datos = e.data || {};
    return {
        error: datos.message || e.message || String(e),
        tipo: datos.name || e.name || typeof e,
        traceback: datos.debug || undefined,
        stack: e.stack ? String(e.stack).split("\n").slice(0, 6).join("\n") : undefined,
        crudo: typeof e === "object" ? JSON.stringify(e, Object.getOwnPropertyNames(e)).slice(0, 800) : undefined,
    };
}

/** Cronometra un paso y traza cuanto tardo. */
export async function ffTiempo(paso, fn, extra = {}) {
    const t0 = performance.now();
    try {
        const res = await fn();
        ffLog(paso, { ...extra, ms: Math.round(performance.now() - t0) });
        return res;
    } catch (e) {
        ffWarn(paso + " (excepción)", {
            ...extra,
            ms: Math.round(performance.now() - t0),
            error: e?.message || String(e),
        });
        throw e;
    }
}

// Volcado desde la consola de la caja: ffFeLog() para verlo,
// ffFeLog.texto() para copiarlo y pegarlo en un reporte.
if (typeof window !== "undefined") {
    window.ffFeLog = () => {
        console.table(buffer);
        return buffer;
    };
    window.ffFeLog.texto = () => buffer.map((e) => JSON.stringify(e)).join("\n");
    window.ffFeLog.limpiar = () => {
        buffer.length = 0;
    };
}
