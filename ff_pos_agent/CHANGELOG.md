# Changelog — Factura Electrónica Panamá (Factura Fácil) - POS

Formato: cada versión lista lo que cambia para quien instala o actualiza el módulo.

## 19.0.6.2.0

### Corregido
- **Las facturas generadas por el POS aparecían sin estado FE.** El documento
  fiscal cuelga de la **orden**, no de la factura, así que en Contabilidad —con
  facturación automática del POS— todas salían en blanco aunque la venta
  estuviera timbrada y autorizada.

  La factura comparte ahora el mismo registro `ff.fe.document` que su orden: no
  se copia nada, así que un cambio de estado se ve en los dos sitios a la vez.
  Se enlaza en ambos sentidos — al emitir (la factura ya existe) y al facturar
  una orden más tarde desde el backend.

- Esas facturas muestran el estado pero **no ofrecen acciones FE**: el documento
  es de la orden, y en modo "Agente en la caja" el servidor ni siquiera alcanza
  ese agente. En su lugar se indica *Emitida desde el Punto de Venta*.



### Corregido
- **Validar el RUC desde Contabilidad decía "no hay agente"** aunque la compañía
  estuviera configurada con la API en línea. La consulta solo miraba los puntos
  de venta, nunca la configuración de facturas — y si todas las cajas usan
  "Agente en la caja" (que el servidor no alcanza), no quedaba ninguna opción.

  Ahora cada origen consulta con **su propia** conexión, por este orden:
  1. El punto de venta que pregunta, si lo indica
  2. La configuración de **facturas de la compañía** — la que corresponde al
     backend, y la que suele estar en modo API en línea
  3. Cualquier punto de venta cuyo agente alcance el servidor

  Las cajas en modo "Agente en la caja" se excluyen siempre de la vía servidor,
  incluso si se piden explícitamente: ese agente solo lo alcanza su navegador.
  Y el mensaje de error, cuando no hay ninguna conexión usable, ahora dice dónde
  configurarla.



### Nuevo
- **El estado de la factura electrónica, siempre a la vista en la cabecera.**
  Antes el botón desaparecía en cuanto se emitía y el estado quedaba escondido en
  una pestaña: quien facturaba no sabía si la DGI lo había aceptado sin ir a
  buscarlo. Ahora la cabecera muestra el estado y la acción que toca:

  | Estado | Qué ofrece |
  |---|---|
  | Sin emitir | **Emitir FE** |
  | Pendiente / Error | **Reintentar FE** |
  | Rechazada DGI | **Reemitir FE** (pide confirmación: el rechazado no vale y se emite uno nuevo) |
  | Timbrada | **Actualizar estado DGI** |
  | Autorizada | solo la insignia verde |
  | Envío incierto | aviso explícito y **sin** botón de reintento — reenviar duplicaría la factura |

- **El motivo del fallo se lee encima de la factura**, no dentro de una pestaña:
  franja roja si está rechazada o en error, ámbar si está pendiente.
- Si la factura está confirmada pero la compañía no tiene Factura Fácil activo,
  se dice —en vez de dejar un hueco sin explicación.



### Corregido
- **La pantalla en blanco del POS: `not` en una plantilla Owl.** Owl no compila
  expresiones Python. Su tabla de traducción (`WORD_REPLACEMENT` en `owl.js`)
  cubre `and`, `or`, `gt`, `gte`, `lt` y `lte` — **`not` no está**. Se compilaba
  como un identificador y la plantilla `point_of_sale.ReceiptScreen` **entera**
  dejaba de compilar (`Unexpected identifier 'ctx'`), así que el POS moría al
  pintar el recibo con la venta ya cobrada y timbrada. Ahora usa `!`.
- Nuevo test que recorre las plantillas del módulo y falla si aparece un
  operador que Owl no traduce, señalando fichero y línea.



### Corregido
- **Los avisos ya dicen qué pasó en el propio texto del mensaje.** La consola
  colapsa los objetos como `Object`, y al copiar el log para reportarlo ese
  contenido se perdía entero — quien depura una caja remota solo tiene el texto.
  Ahora el error, el tipo y el componente van en la línea, y la pila se imprime
  aparte.



### Nuevo
- **El error real de Owl en la consola.** Un fallo de render se registraba como
  `[object Object]`, que no dice nada. Ahora se capturan `error` y
  `unhandledrejection` y se muestran mensaje, tipo, pila, **el componente que
  falló** y la causa encadenada.

### Corregido
- `ffFeReceipt` (el getter que arma el CAFE del recibo) se evalúa **durante el
  render**: si lanzaba, Owl destruía el componente raíz y la caja se quedaba en
  blanco. Ahora va envuelto y, en el peor caso, devuelve un recibo incompleto en
  lugar de tumbar el POS.



### Corregido
- **El POS se quedaba en blanco justo después de timbrar.** Con la factura ya
  emitida ante la DGI, Owl destruía el componente raíz y la caja se quedaba
  muerta. Tres causas, las tres mías:
  - `ffApplyFe` hacía un `Object.assign` del bloque entero: si el modelo del POS
    rechazaba **una sola** clave, reventaba todo. Ahora va campo a campo y
    dentro de `try/catch`.
  - La plantilla del recibo leía `fe_impreso` (un related **no almacenado**, que
    el modelo cliente puede no conocer) y `currentOrder.raw` directamente. Una
    excepción renderizando mata el componente raíz. Ahora pasan por getters con
    red (`ffImpreso`, `ffFacturaId`).
  - Un error en consola salía como `[object Object]`. Ahora se muestran mensaje,
    tipo, traceback y pila.

El principio: **con el documento fiscal ya emitido, ningún fallo de interfaz
puede tumbar la caja.** La venta está cobrada y timbrada; lo peor que debe pasar
es que falte un dato en el recibo.



### Corregido
- **La factura seguía descargándose sola cuando la orden sí generaba factura.**
  Era un problema de orden: en `finalizeValidation` la descarga está en la línea
  163 y la emisión (`postPushOrderResolve`) en la 193, así que al decidir la
  descarga `fe_impreso` todavía es `false` **siempre** — la condición no podía
  cumplirse nunca. El recibo sí funcionaba porque se decide después.
  Ahora la descarga se suprime **por configuración**, y la red de seguridad va
  detrás: si al terminar la emisión resulta que el agente no imprimió, la factura
  se descarga entonces, para que el cliente no se quede sin ningún papel.
- El botón **"Ver factura de Odoo (PDF)"** aparece siempre que haya factura, no
  solo cuando el agente imprimió.



### Nuevo
- **Tampoco se descarga sola la factura de Odoo** cuando el agente ya imprimió el
  CAFE. Odoo descarga el PDF de toda orden facturada, y con un módulo de
  facturación automática eso es *todas*: en la caja abre el explorador encima del
  POS y el cajero acaba con dos papeles. Se suprime con el mismo criterio que el
  recibo — por `fe_impreso`, no por la configuración — y queda el botón
  **"Ver factura de Odoo (PDF)"** en el recibo para quien la necesite.



### Nuevo
- **Con la impresión en el agente activa, el recibo de Odoo ya no salta solo.**
  Si el agente imprimió el CAFE, mandar además el recibo a la impresora de Odoo
  era un segundo paso para el cajero (y en muchos equipos abre el diálogo del
  PDF). Ahora el recibo ofrece dos botones a mano:
  - **Imprimir recibo (Odoo)** — el diseño de Odoo, por si hace falta
  - **Reimprimir FE** — reenvía `printByCufe` al agente de esa caja
- Se decide por **`fe_impreso`, no por la configuración**: si el agente falló al
  imprimir, el recibo de Odoo sí sale — es lo único que le quedaría al cliente.
  Y si no se confirmó la impresión, el recibo lo avisa.
- Sin la impresión en el agente, todo sigue igual: el recibo salta directo, que
  es lo que el cajero necesita para imprimirlo.

### Corregido
- Dos llamadas RPC apuntaban a métodos con guion bajo (`_ff_fe_data`,
  `_ff_apply_local_print`), que Odoo bloquea remotamente. Se exponen ahora como
  `ff_fe_data` y `ff_local_print_done`, con un test que vigila que sigan siendo
  públicos.



### Corregido
- **El cajero no podía preparar la emisión.** `ff_local_prepare` leía las
  credenciales del agente como el usuario actual, y esos campos llevan
  `groups=responsable del Punto de Venta`: cualquier cajero se topaba con un
  `AccessError` que llegaba a la caja como un **"Odoo Server Error"** sin más
  explicación. Ahora se leen con `sudo()` — es el cajero quien tiene que
  mandárselas a su propio agente.
- Un fallo inesperado construyendo el documento ya no vuelve como un 500 opaco:
  se registra en el log del servidor y a la caja llega el motivo.
- La consola muestra el error real del servidor (`e.data.message` y su
  traceback), no el genérico "Odoo Server Error".



### Corregido
- **Dos comprobaciones previas impedían emitir con el agente accesible.**
  - El estado del permiso de red local se exigía `granted` para siquiera
    intentarlo, pero `navigator.permissions.query` puede devolver `prompt`
    aunque las peticiones ya funcionen. Ahora se informa y **no bloquea**: quien
    decide es la petición real.
  - El sondeo previo exigía `200` en `/api/healthcheck/`. Basta con que el
    agente **responda algo**, aunque sea 404: eso ya prueba que hay alguien
    escuchando. Solo un fallo de red significa que el POST no llegaría.

### Nuevo
- `ffFeEmitir()` en la consola de la caja, para emitir la orden actual (o una por
  id) sin tener que hacer una venta entera.
- Traza al entrar al gancho de pago: si no aparece, el problema está en el
  gancho y no en la emisión.



### Nuevo
- **La impresión del CAFE también la manda la caja.** En modo "Agente en la caja"
  la impresora cuelga de esa máquina, así que el servidor no la alcanza: ahora el
  POS llama a `printByCufe` contra su propio agente, justo después de la firma.
  Son dos llamadas distintas y así se tratan — si la impresión falla, el
  documento fiscal sigue siendo válido y solo queda constancia en la auditoría.
  El servidor deja de intentar imprimir en este modo.



### Corregido
- **El service worker del POS impedía hablar con el agente.** Intercepta todos
  los GET y los reemite con `fetch(event.request)` desde su propio contexto; en
  esa reemisión se pierden `targetAddressSpace` (es una opción de `fetch`, no
  viaja en el `Request`) y la activación del usuario, así que Chrome denegaba el
  acceso a la red local **sin llegar a preguntar** — incluso pulsando un botón.
  Odoo tiene el mismo problema con su hardware local y lo resuelve excluyendo a
  mano esas URLs (`hw_proxy/hello`, `Cashdro3WS/index3.php`); como no podemos
  tocar su service worker, las peticiones al agente llevan ahora un parámetro
  inocuo que cae en una de sus exclusiones. El POST de emisión no lo necesitaba:
  el service worker ignora todo lo que no sea GET.



### Corregido
- **El permiso de red local no se llegaba a pedir nunca.** Chrome solo muestra su
  diálogo cuando la petición nace de un gesto del usuario; el sondeo automático
  al abrir el POS se rechazaba en silencio y el permiso se quedaba en `prompt`
  para siempre. Ahora, si falta el permiso, el POS muestra un aviso con el botón
  **"Autorizar el agente"**, y es la pulsación la que dispara el diálogo. Si está
  denegado, se explica cómo reabrirlo desde el candado de la barra de direcciones.
- **La validación de RUC usaba el agente equivocado.** `_ff_get_agent_config`
  buscaba el primer punto de venta configurado, sin mirar si el servidor podía
  alcanzarlo: con una caja en modo local, Odoo intentaba llamar a `127.0.0.1`
  — o sea, a sí mismo. Ahora esos puntos de venta se excluyen de la búsqueda
  server-side y, en modo local, la consulta la hace el navegador contra su propio
  agente; el servidor solo interpreta la respuesta (`ff_ruc_from_agent`), que es
  donde vive la lógica de tipoRuc/tipoReceptor.
- El diagnóstico ya no sondea la red cuando falta el permiso: Chrome rechaza todo
  por igual y el resultado no distinguía nada. Informa del permiso y para.



### Nuevo
- **Traza completa de la emisión en la consola de la caja**, siempre activa (no
  depende del modo debug: cuando falla en una caja real, nadie tiene abiertas las
  herramientas de desarrollo). Cada paso queda registrado — preparación, sondeo
  del agente, envío, firma, resultado del PAC y registro en Odoo — con su
  duración. Las credenciales nunca se registran.
  - `ffFeLog()` vuelca la traza · `ffFeLog.texto()` la da lista para pegar
- **Diagnóstico de por qué falla.** Un `fetch` bloqueado llega al JS como un
  `TypeError` seco, igual si el agente está apagado que si lo bloqueó el CORS.
  Se distinguen con una sonda `mode:"no-cors"`, que sí llega cuando el servidor
  está ahí: si esa pasa y la normal no, el problema es CORS. Se comprueba además
  el permiso de red local de Chrome. Cada caso dice qué hacer.
  - Se ejecuta solo al abrir el POS, para avisar **antes** de la primera venta
  - `ffFeDiag()` para lanzarlo a mano
- El servidor registra en `odoo.log` la preparación y el resultado de cada
  emisión desde caja.

### Corregido
- El test del cron contaminaba la configuración del POS: `_cron_ff_update_status`
  hace `commit` por documento y escapaba del aislamiento transaccional, dejando
  escrito un punto de venta con la configuración de prueba.

## 19.0.3.0.0

### Nuevo
- **Modo "Agente en la caja"**: el Agente Offline está instalado en cada caja y
  lo llama el **navegador** (127.0.0.1), no el servidor. Pensado para Odoo en la
  nube, donde el servidor no alcanza la red de la tienda.

  Reparto de responsabilidades, a propósito:
  - el **servidor** construye el payload (código probado) y escribe el estado
    fiscal, validando lo que la caja le devuelve;
  - el **navegador** solo transporta;
  - un **diario en la caja** (`localStorage`) impide que un documento salga dos
    veces: la marca se escribe ANTES del POST y solo se borra cuando Odoo
    confirma que registró el resultado. Al abrir el POS se reconcilia lo que
    quedó en el aire, sin reenviar nunca.

  Mismas reglas de idempotencia que el servidor: con `document_uuid` no se
  reemite jamás, y un envío incierto no se reintenta solo.

### Requisitos del modo (no se pueden comprobar desde Odoo)
- Chrome, Edge o Brave **142+** con Local Network Access — parámetro de sistema
  `point_of_sale.use_lna` = `1`. **Firefox e iOS no sirven.**
- El agente debe permitir el dominio de Odoo por **CORS**. Sin eso el navegador
  bloquea la llamada, y no hay nada que Odoo pueda hacer.
- La API Key se carga en el navegador de la caja: deja de ser un secreto de
  servidor. Vive en la misma máquina que el agente que la usa.

### Limitación conocida
El payload lo prepara el servidor, así que **durante un corte de internet la
venta se cobra y se encola, pero el timbrado se hace al reconectar**. Emitir con
la caja totalmente aislada exige portar la aritmética fiscal al navegador; es el
siguiente paso y tiene su propio riesgo (duplicar en JS una lógica delicada).

## 19.0.2.0.0

### Nuevo
- **Facturas de Contabilidad ante la DGI.** Las facturas de cliente y sus notas
  de crédito emitidas fuera del POS ya se timbran con Factura Fácil, con el
  mismo ciclo que el POS (estado, CUFE, reintentos, auditoría, CAFE oficial).
  Se emiten solas al confirmar la factura, o a mano con el botón
  **Firmar / Emitir FE** de la cabecera. La nota de crédito viaja como NTC
  referida al CUFE de la factura original.
- **Configuración propia de la compañía** (Ajustes → Contabilidad → Facturas de
  cliente): URL, credenciales, ambiente y punto de facturación. OJO: el código
  de punto de facturación debe ser DISTINTO al de las cajas — Factura Fácil
  numera por punto de venta y compartirlo choca los correlativos ante la DGI.
- **Forma de pago DGI por diario** (`account.journal.ff_payment_type`), como ya
  existía por método de pago del POS. Lo que quede sin cobrar en la factura se
  informa como crédito (01).
- No hay doble emisión: una factura nacida de una orden POS no se vuelve a
  emitir aquí (el pedido ya la emitió con sus pagos reales).

### Corregido
- **Las devoluciones del POS no se podían emitir en Odoo 19.** Desde la 19 el
  `price_subtotal` de una línea de devolución es POSITIVO (el signo lo lleva el
  total de la orden, no la línea), así que todas las líneas se tomaban como
  descuentos y la emisión fallaba con "el descuento supera las ventas". El
  sentido lo da ahora el tipo de documento (NTC), no el signo de la línea.
- **Las restricciones de unicidad no se aplicaban.** Odoo 19 ignora
  `_sql_constraints`; migradas a `models.Constraint`. Sin ellas nada impedía que
  una misma orden o factura acabara con dos documentos fiscales.
- Vistas heredadas rotas en Odoo 19: la agrupación de la búsqueda de órdenes
  (`<group expand>` ya no es válido) y el código DGI de la unidad de medida
  (la vista de `uom.uom` ya no muestra `rounding` y la de categorías no existe).
- Tests adaptados a Odoo 19 (`groups_id` → `group_ids`, `uom_po_id` retirado,
  `_load_pos_data` → `_load_pos_data_read`, el vuelto como pago `is_change`).

### Interno
- La aritmética fiscal (bases, ITBMS, reparto de descuentos, cuotas de crédito)
  vive ahora en el mixin `ff.fe.source`, que comparten `pos.order` y
  `account.move`: un arreglo sirve para los dos. Las fuentes solo aportan de
  dónde salen los datos.

## 19.0.1.1.0

### Nuevo
- **Botón “Firmar / Emitir FE” en el backend**, en la cabecera de la orden POS.
  Cierra el ciclo: permite firmar una orden que aún no tiene documento — creada
  en el backend, o del POS que nunca llegó a emitir. Crea el documento si falta
  y lo emite en el momento (mismo flujo que el POS). Aparece solo cuando la
  orden está cobrada y el punto de venta tiene Factura Fácil activo; una vez con
  documento, se usa “Reintentar / actualizar FE”.

## 19.0.1.0.0 — Port a Odoo 19

Port del módulo a **Odoo 19** (misma funcionalidad que 18.0.3.0.0). Rama `19.0`.

### Cambiado (compatibilidad con Odoo 19)
- **Punto de emisión FE**: Odoo 19 movió la validación del pago de `PaymentScreen` a la clase
  `OrderPaymentValidation`. La emisión ahora se engancha en `beforePostPushOrderResolve`
  (antes `PaymentScreen._postPushOrderResolve`).
- **JS del POS a camelCase**: `wait_for_push_order`→`waitForPushOrder`, `get_order()`→`getOrder()`,
  `printReceipt()`→`printReceipt({order})`.
- **Recibo CAFE**: en Odoo 19 el recibo recibe `props.order` (antes un dict `props.data` armado por
  `export_for_printing`, que ya no existe). Los datos FE los expone la orden con el getter
  `ffFeReceipt`; la plantilla ahora ancla en `//OrderDisplay` (antes `//OrderWidget`).
- **Carga de la config al POS**: `pos.config._load_pos_data` → `_load_pos_data_read` para seguir
  ocultando la API Key y el Company UUID al navegador de la caja.
- Sin cambios de datos ni de esquema: no requiere migración desde una 19.0 previa.

### Requisitos
- Odoo 19 con Punto de Venta. (Para Odoo 18 usar la serie `18.0.x` en la rama `main`/`18.0`.)

## 18.0.3.0.0 — 2026-09-24

### Nuevo
- **Modo de emisión** por punto de venta: *Agente offline* (como hasta ahora) o **API en línea**
  de Factura Fácil. Mismo documento; en línea la factura se autoriza al momento.
- En línea, el **PDF oficial del CAFE** (`download_cafe`) queda adjunto a la orden, con el botón
  *Ver CAFE oficial* en el POS y en el backend. El recibo sigue saliendo con el diseño CAFE de Odoo.
- Validación de RUC con la DGI también en línea.

### Corregido
- Un cajero no podía validar un RUC desde el POS (error de acceso desde 18.0.2.0.0, al restringir
  la API Key a responsables).

### A tener en cuenta
- Los dos modos **comparten la numeración** del punto de venta en Factura Fácil. No uses el mismo
  código POS en una caja con agente y otra en línea: si el agente numera sin conexión, los números
  pueden chocar.

## 18.0.2.1.0 — 2026-09-23

### Nuevo
- **Forma de pago DGI** por método de pago del POS (`Crédito`, `Efectivo`, tarjetas, `Vale`,
  `Tarjeta de regalo`, `Transferencia`, `Cheque`, `Otro`…). Se propone al instalar a partir del
  tipo del método y se puede corregir con las cajas abiertas.
- **Código DGI de la unidad de medida** (vacío = `und`).
- **Ventas a crédito** (cuenta de cliente) informadas como forma de pago *Crédito*. Opción
  *Informar cuotas de crédito* para enviar plazo y cuota cuando el agente lo soporte.
- Botón **Probar conexión** en los ajustes del POS.
- Ficha de la tienda de apps (ícono, banner, capturas).
- Plantilla de traducción `i18n/ff_pos_agent.pot`.

### Cambios
- Antes, la cuenta de cliente y cualquier método con un nombre no reconocido se informaban como
  *Efectivo*. Ahora la cuenta de cliente es *Crédito* y lo desconocido, *Otro* con descripción.
  **Revisá la forma de pago DGI de tus métodos al actualizar.**
- Un impuesto que no es una tasa de ITBMS (7, 10 o 15%) deja la orden en *Error* con el motivo,
  en vez de enviarse como exento.
- Todos los textos de la interfaz, los mensajes y el CAFE, con la ortografía correcta
  ("Factura Electrónica", "Código", "Envío incierto"…).

### Problemas conocidos del agente
- El agente falla con cuotas de crédito (HTTP 500). Ver *Bugs conocidos del agente* en
  `INTEGRACION_ODOO_fe_detailed.md`.

## 18.0.2.0.0 — 2026-09-23

Actualización grande. Incluye una migración: hacé un respaldo de la base antes de actualizar.

### Nuevo
- Modelo **`ff.fe.document`**: el documento fiscal de cada venta, con su estado, CUFE, auditoría
  y reintentos. Las columnas FE de la orden pasan a ser un espejo del documento.
- Estado **Envío incierto**: si el agente pudo haber recibido la venta sin confirmarla (timeout,
  corte, HTTP 5xx), no se reenvía sola. Un responsable del POS la resuelve vinculando el UUID del
  documento o con *Forzar reenvío*.
- El cron reintenta las ventas pendientes y deja de insistir con un agente caído.

### Cambios
- **La emisión va aparte de la venta**: la sincronización de la orden ya no habla con el agente;
  el POS pide la emisión justo después y el recibo sale igual con el CAFE. Si el navegador se
  cierra, el cron la emite a los 2 minutos.
- Antes de enviar se consulta el healthcheck del agente: con el agente apagado la caja espera
  ~3 s (antes hasta 45 s).
- **Solo se facturan las ventas cobradas.** Los borradores (mesas abiertas) nunca se emiten.
  Las devoluciones pagadas desde el backend ahora sí se emiten.
- Precios con **ITBMS incluido** y **descuentos globales / premios** se emiten con el total
  cobrado (antes se facturaba de más y el agente lo rechazaba).
- **Seguridad**: la API Key y el Company UUID solo los ve un responsable del POS y ya no se cargan
  en el navegador de la caja; el estado fiscal es de solo lectura; la auditoría enmascara la
  API Key. **Si usaste la 18.0.1.0.0 en una caja real, rotá la API Key.**
- La auditoría conserva el historial de todos los intentos.

### Migración
- Se crea un documento FE por cada orden con actividad FE (payload enviado, CUFE o UUID). Las
  demás no reciben documento y **no se emiten**.

### Corregido
- En la 18.0.1.0.0 toda orden nacía *pendiente*: con el reintento automático del cron, una base
  con historial habría emitido sus ventas antiguas. Corregido: solo se emite lo que tiene
  documento, y el documento nace al cobrar.

## 18.0.1.0.0 — 2026-09-22

Primera versión: emisión desde el POS, CAFE en el recibo, notas de crédito, validación de RUC con
la DGI, facturas a Gobierno (GNS), impresión en el agente, Monitor y auditoría.
